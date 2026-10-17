"""Sealed host verifier policy and append-only exact receipt identities.

Issuer verification is read-only external I/O outside the interruption lane;
consumer-specific execution/candidate semantics remain mandatory.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import assignments, controls, readiness
from .planning import require


def receipt_location(database, path):
    """Reject worker-mounted policy/receipt locations, including not-yet-issued files."""
    from .merge import private_file
    location = Path(path)
    state = private_file(database.execute('PRAGMA database_list').fetchone()[2])
    require(assignments.absolute(path) and not any(p.is_symlink() for p in (location, *location.parents)),
            'Physical absolute receipt location required.', 'execution_required')
    for row in assignments.rows(database):
        scopes = [row['handoff']['workspace'], row['handoff']['profile']['home']]
        if row.get('runtime'):
            scopes.append(row['runtime']['artifacts'])
        for scope in scopes:
            require(assignments.absolute(scope) and not any(p.is_symlink() for p in (Path(scope), *Path(scope).parents)) and
                    not location.is_relative_to(Path(scope)) and not state.is_relative_to(Path(scope)),
                    'Receipt policy/state must remain outside worker mounts.', 'execution_required')


def receipt_policy(database, config, selected):
    """Only an explicit sealed verifier policy can designate mutable receipt slots."""
    from .readiness_gate import scope_identity
    approval = selected.get('runtime_receipts')
    if approval is None:
        return None
    from .sandbox_source import physical
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    require(isinstance(approval, dict) and set(approval) == {'scope', 'verifier', 'receipts'} and
            approval['scope'] == scope_identity(item) and isinstance(approval['receipts'], dict) and
            set(approval['receipts']) <= {'review', 'diagnosis', 'publication', 'merge'},
            'Exact immutable receipt-issuer scope required.', 'readiness_inputs_changed')
    verifier = approval['verifier']
    require(isinstance(verifier, dict) and set(verifier) == {'path', 'sha256'} and
            assignments.absolute(verifier['path']) and assignments.sha(verifier['sha256']),
            'Explicit reviewed verifier required.', 'readiness_inputs_changed')
    source = physical(verifier['path'])
    info = source.stat()
    require(info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o022 and
            os.access(source, os.X_OK) and hashlib.sha256(source.read_bytes()).hexdigest() == verifier['sha256'],
            'Reviewed verifier implementation changed.', 'readiness_inputs_changed')
    seen = set()
    receipt_location(database, str(source))
    for paths in approval['receipts'].values():
        require(isinstance(paths, dict) and 0 < len(paths) <= 1000,
                'Finite exact receipt paths required.', 'readiness_inputs_changed')
        for path, constraints in paths.items():
            require(assignments.absolute(path) and Path(path).suffix == '.json' and path not in seen and
                    path != verifier['path'] and isinstance(constraints, dict) and bool(constraints),
                    'Distinct constrained JSON receipt paths required.', 'readiness_inputs_changed')
            seen.add(path)
            parent = physical(Path(path).parent, directory=True)
            controls.private(parent, directory=True)
            require(not Path(path).is_symlink(), 'Physical receipt slots required.', 'readiness_inputs_changed')
            receipt_location(database, path)
    return approval


def receipt_request(approval, kind, path, raw):
    return {'scope': approval['scope'], 'kind': kind, 'path': path,
            'sha256': hashlib.sha256(raw).hexdigest(), 'constraints': approval['receipts'][kind][path]}


def receipt_identity(database, project, iteration, kind, path):
    state = database.execute('PRAGMA database_list').fetchone()[2]
    root = Path(state + '.receipt-identities')
    return root, root / (assignments.digest([project, iteration, kind, path]) + '.json')


def validate_runtime_receipts(database, config, selected, *, local=False):
    """Admission never trusts a newly materialized or rewritten unverified slot."""
    approval = receipt_policy(database, config, selected)
    if approval is None:
        return
    from .merge import private_file
    for kind, paths in approval['receipts'].items():
        for path in paths:
            root, identity = receipt_identity(database, config['project'], config['iteration'], kind, path)
            if not os.path.lexists(path):
                require(not identity.exists(), 'Accepted receipt disappeared.', 'execution_required')
                continue
            if not local:
                runtime_receipt(database, config['project'], config['iteration'], kind, path)
                continue
            controls.private(root, directory=True)
            controls.private(identity)
            expected = receipt_request(approval, kind, path, private_file(path).read_bytes())
            require(identity.read_bytes() == json.dumps(expected, sort_keys=True).encode(),
                    'Receipt changed after independent observation.', 'execution_required')


def runtime_receipt(database, project, iteration, kind, path, *, missing_ok=False):
    """Freshly verify exact issued bytes; never replace an already accepted identity.

    This host read belongs outside the short interruption lane. Consumer-specific
    run/candidate/evidence validation remains mandatory after this issuer check.
    """
    from . import merge
    from .repositories import RepositoryError, unambiguous_fields
    from .readiness_gate import binding, configuration, policy
    current = binding(database, project, iteration)
    if current is None:
        return readiness.load_config(path)
    config, selected = configuration(current[0])
    require((config['project'], config['iteration']) == (project, iteration) and
            config['state'] == database.execute('PRAGMA database_list').fetchone()[2],
            'Exact receipt state/scope required.', 'scope_mismatch')
    expected_policy = policy(config, selected, database)
    require(current[1] == expected_policy, 'Receipt issuer policy changed.', 'readiness_inputs_changed')
    approval = receipt_policy(database, config, selected)
    if approval is None or path not in approval['receipts'].get(kind, {}):
        return readiness.load_config(path)  # Legacy immutable files are still sealed.
    if not os.path.lexists(path) and missing_ok:
        _, identity = receipt_identity(database, project, iteration, kind, path)
        require(not identity.exists(), 'Accepted receipt disappeared.', 'execution_required')
        return None
    source = merge.private_file(path)
    raw = source.read_bytes()
    require(len(raw) <= 8_000_000, 'Bounded exact receipt required.', 'execution_required')
    value = json.loads(raw, object_pairs_hook=unambiguous_fields)
    require(isinstance(value, dict), 'Exact receipt object required.', 'execution_required')
    request = receipt_request(approval, kind, path, raw)
    try:
        result = subprocess.run([approval['verifier']['path']], input=json.dumps(request).encode(),
            capture_output=True, timeout=5, check=False, env={}, cwd=str(Path(path).parent))
        require(result.returncode == 0 and len(result.stdout) <= 16384 and
                json.loads(result.stdout, object_pairs_hook=unambiguous_fields) == {'verified': request},
                'Independent exact receipt verification refused.', 'execution_required')
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise RepositoryError('execution_required', 'Independent exact receipt verification unavailable.') from error
    require(source.read_bytes() == raw and merge.private_file(path) == source and
            binding(database, project, iteration) == current and
            policy(*configuration(current[0]), database) == expected_policy,
            'Receipt or issuer changed during verification.', 'readiness_inputs_changed')
    # An independent private ledger avoids committing a caller's open transaction.
    # O_EXCL makes first acceptance atomic across processes and survives a crash.
    root, identity = receipt_identity(database, project, iteration, kind, path)
    root.mkdir(mode=0o700, exist_ok=True)
    controls.private(root, directory=True)
    retained = json.dumps(request, sort_keys=True).encode()
    try:
        fd = os.open(identity, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        controls.private(identity)
        require(identity.read_bytes() == retained, 'Accepted receipt identity is immutable.', 'execution_required')
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(retained)
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return value
