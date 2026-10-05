"""Read pinned Git objects without executing repository configuration, hooks or code."""
import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from .planning import require
from .repositories import RepositoryError

MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_OBJECT_BYTES = 512 * 1024 * 1024
MAX_FILES = 20000


def physical(path, directory=False):
    path = Path(path)
    require(path.anchor == '/' and '..' not in path.parts and
            not any(p.is_symlink() for p in (path, *path.parents)),
            'Sandbox paths must be physical absolute paths, not dotdot or symlink aliases.', 'unsafe_path')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            'Expected a physical directory or regular file.', 'unsafe_path')
    return path


def relative(name):
    require(isinstance(name, str) and name and '\x00' not in name and '\\' not in name and
            not name.startswith('/') and all(part not in {'', '.', '..', '.git'} for part in name.split('/')),
            'Unsafe source artifact path.', 'source_blocked')
    return name


def manifest(root):
    """Independently pin source artifacts; never follow worker-created links/devices."""
    root = physical(root, directory=True)
    result, total = {}, 0
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories:
            physical(Path(parent) / name, directory=True)
            relative(str((Path(parent) / name).relative_to(root)))
        for name in files:
            path = physical(Path(parent) / name)
            key = relative(str(path.relative_to(root)))
            size = path.stat().st_size
            total += size
            require(total <= MAX_SOURCE_BYTES and len(result) < MAX_FILES,
                    'Source artifacts exceed the bounded archive limit.', 'source_blocked')
            with path.open('rb') as stream:
                pin = hashlib.file_digest(stream, 'sha256').hexdigest()
            result[key] = {'sha256': pin, 'bytes': size, 'executable': bool(path.stat().st_mode & 0o111)}
    return result


def snapshot(workspace, revisions, destination):
    """Copy only physical object files into a clean, trusted bare Git reader."""
    workspace = physical(workspace, directory=True)
    marker = workspace / '.git'
    if marker.is_dir():
        git_dir = physical(marker, directory=True)
    else:
        line = physical(marker).read_text().strip()
        require(line.startswith('gitdir: ') and '\n' not in line, 'Invalid worktree Git marker.', 'source_blocked')
        git_dir = physical(Path(os.path.abspath(workspace / line[8:])), directory=True)
    common = git_dir / 'commondir'
    if common.exists():
        value = physical(common).read_text().strip()
        require(value and '\n' not in value, 'Invalid common Git object directory.', 'source_blocked')
        git_dir = physical(Path(os.path.abspath(git_dir / value)), directory=True)
    source_objects = physical(git_dir / 'objects', directory=True)
    reader = destination / 'git-reader'
    reader.mkdir(mode=0o700)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(reader), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'}

    def git(*args):
        response = subprocess.run(['/usr/bin/git', '--git-dir=' + str(reader), *args], env=env,
                                  cwd=reader, capture_output=True, timeout=30)
        require(response.returncode == 0, 'Pinned Git objects unavailable; no fetching or host repo execution.', 'source_blocked')
        return response.stdout

    git('init', '--bare', '--template=')
    copied = 0
    for directory in source_objects.iterdir():
        if not (re.fullmatch('[0-9a-f]{2}', directory.name) or directory.name == 'pack'):
            continue  # Never copy alternates, grafts, refs, config, credentials or hooks.
        physical(directory, directory=True)
        for obj in directory.iterdir():
            accepted = (re.fullmatch('[0-9a-f]{38}', obj.name) if directory.name != 'pack' else
                        re.fullmatch(r'pack-[0-9a-f]{40}\.(pack|idx)', obj.name))
            if not accepted:
                continue
            physical(obj)
            copied += obj.stat().st_size
            require(copied <= MAX_OBJECT_BYTES, 'Git object snapshot exceeds its bound.', 'source_blocked')
            target = reader / 'objects' / directory.name / obj.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(obj, target)
    manifests = {}
    try:
        for label, revision in revisions.items():
            require(git('rev-parse', '--verify', revision + '^{commit}').decode().strip() == revision,
                    'Physical source must match the full pinned commit.', 'source_blocked')
            root = destination / label
            root.mkdir(mode=0o755)
            total = 0
            entries = git('ls-tree', '-rz', '--full-tree', revision).split(b'\x00')
            require(len(entries) <= MAX_FILES + 1, 'Pinned source has too many files.', 'source_blocked')
            for entry in entries:
                if not entry:
                    continue
                header, raw_name = entry.split(b'\t', 1)
                mode, kind, object_id = header.decode('ascii').split()
                name = relative(raw_name.decode('utf-8'))
                require(kind == 'blob' and mode in {'100644', '100755'},
                        'Symlinks and submodules are not admitted source mounts.', 'source_blocked')
                size = int(git('cat-file', '-s', object_id).decode())
                total += size
                require(total <= MAX_SOURCE_BYTES, 'Pinned source exceeds its bound.', 'source_blocked')
                content = git('cat-file', 'blob', object_id)
                require(len(content) == size, 'Source object changed.', 'source_blocked')
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o755 if mode == '100755' else 0o644)
            manifests[label] = manifest(root)
        return manifests
    except (ValueError, UnicodeError, subprocess.SubprocessError) as error:
        raise RepositoryError('source_blocked', 'Unable to validate the pinned physical source.') from error
    finally:
        shutil.rmtree(reader)
