"""Only the selected upstream planning snapshots; no ambient skill discovery."""
import hashlib
from pathlib import Path

from .repositories import RepositoryError

PINS = {
    'grill-me': 'caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637',
    'grilling': '10ff989e7498b23b5acb49d5048f11dcd906757d2f79c5cdf8a00001381296f2',
    'to-spec': '43ad9cf318e5e7d3d1fa360253a37021796dc87a0c2e595ad262661a10f85088',
}


def resolve(configuration):
    if not isinstance(configuration, list) or len(configuration) != len(PINS):
        raise RepositoryError('skill_blocked', 'Configure exactly grill-me, grilling and to-spec with explicit paths and supplied snapshot pins.')
    resolved = {}
    for descriptor in configuration:
        if (not isinstance(descriptor, dict) or set(descriptor) != {'name', 'path', 'sha256'} or
                not isinstance(descriptor['name'], str) or descriptor['name'] not in PINS or
                descriptor['name'] in resolved or not isinstance(descriptor['path'], str) or
                not Path(descriptor['path']).is_absolute() or descriptor['sha256'] != PINS[descriptor['name']]):
            raise RepositoryError('skill_blocked', 'Planning skill selection is missing, ambiguous or differs from the supplied upstream pins.')
        name = descriptor['name']
        path = Path(descriptor['path'])
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError('Not a regular selected file')
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != PINS[name]:
                raise RepositoryError('skill_blocked', 'Selected skill bytes differ from the supplied upstream snapshot.')
            instructions = content.decode('utf-8')
        except (OSError, UnicodeError) as error:
            raise RepositoryError('skill_blocked', 'Selected planning skill file is unavailable.') from error
        resolved[name] = {**descriptor, 'instructions': instructions}
    return {name: resolved[name] for name in PINS}
