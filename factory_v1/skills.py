"""Only the selected upstream planning snapshots; no ambient skill discovery."""
import hashlib
from pathlib import Path

INSTALLED_ROOT = Path.home() / '.hermes/skills'
INSTALLED_PINS = {
    'grill-me': 'caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637',
    'grilling': 'b4846894c01ea1b216019c5ab83c4d9ab286ff6f5faf78e1c7edc965a365489e',
    'to-spec': 'f3baf5f84dd2e47cb000bdb67a2794e714a59826acb50514b2aa80e802e525a9',
}

from .repositories import RepositoryError

PINS = {
    'grill-me': 'caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637',
    'grilling': '10ff989e7498b23b5acb49d5048f11dcd906757d2f79c5cdf8a00001381296f2',
    'to-spec': '43ad9cf318e5e7d3d1fa360253a37021796dc87a0c2e595ad262661a10f85088',
}


def resolve(configuration, *, pins=None, installed=False):
    pins = PINS if pins is None else pins
    if not isinstance(configuration, list) or len(configuration) != len(pins):
        raise RepositoryError('skill_blocked', 'Configure exactly grill-me, grilling and to-spec with explicit paths and supplied snapshot pins.')
    resolved = {}
    for descriptor in configuration:
        if (not isinstance(descriptor, dict) or set(descriptor) != {'name', 'path', 'sha256'} or
                not isinstance(descriptor['name'], str) or descriptor['name'] not in pins or
                descriptor['name'] in resolved or not isinstance(descriptor['path'], str) or
                not Path(descriptor['path']).is_absolute() or descriptor['sha256'] != pins[descriptor['name']]):
            raise RepositoryError('skill_blocked', 'Planning skill selection is missing, ambiguous or differs from the supplied upstream pins.')
        name = descriptor['name']
        path = Path(descriptor['path'])
        if installed and (path != INSTALLED_ROOT / name / 'SKILL.md' or
                          any(p.is_symlink() for p in (path, *path.parents))):
            raise RepositoryError('skill_blocked', 'Select the installed exact source, not an alternate snapshot.')
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError('Not a regular selected file')
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != pins[name]:
                raise RepositoryError('skill_blocked', 'Selected skill bytes differ from the supplied upstream snapshot.')
            instructions = content.decode('utf-8')
        except (OSError, UnicodeError) as error:
            raise RepositoryError('skill_blocked', 'Selected planning skill file is unavailable.') from error
        resolved[name] = {**descriptor, 'instructions': instructions}
    return {name: resolved[name] for name in pins}


def resolve_installed(configuration):
    return resolve(configuration, pins=INSTALLED_PINS, installed=True)


def resolve_planning(configuration):
    """Admit installed reviewed sources; retain explicit historical snapshots."""
    if isinstance(configuration, list) and any(isinstance(row, dict) and
            isinstance(row.get('name'), str) and
            row.get('path') == str(INSTALLED_ROOT / row['name'] / 'SKILL.md')
            for row in configuration):
        return resolve_installed(configuration)
    return resolve(configuration)
