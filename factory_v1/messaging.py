"""Small supported Hermes CLI adapter; acknowledgement is not a verified receipt."""
import json
import subprocess
from pathlib import Path

from .errors import IntakeError
from .repositories import unambiguous_fields as unique_fields


class HermesTransport:
    """Trusted configuration pins a scoped Hermes executable and exact origins.

    No credential discovery, platform API, lookup protocol or home fallback.
    The CLI has no Discord allowed_mentions option, so render inert text instead.
    """
    def __init__(self, path, destination):
        try:
            with open(path, encoding='utf-8') as source:
                config = json.load(source, object_pairs_hook=unique_fields)
            if (not isinstance(config, dict) or set(config) != {'command', 'approved_destinations'}
                    or not isinstance(config['command'], list) or not config['command']
                    or any(not isinstance(arg, str) or not arg or '\x00' in arg for arg in config['command'])
                    or not Path(config['command'][0]).is_absolute()
                    or not isinstance(config['approved_destinations'], list)
                    or destination not in config['approved_destinations']):
                raise ValueError('invalid configuration')
        except (TypeError, OSError, ValueError, RecursionError, IntakeError):
            raise IntakeError('capability_required', 'Configure a trusted scoped Hermes command and exact approved origin.') from None
        self.command = config['command']
        self.destination = destination

    def send(self, record):
        target = 'discord:' + self.destination['chat_id'] + ':' + self.destination['thread_id']
        # JSON escapes make user text inert, including native mentions and MEDIA:
        # attachment directives. The original content remains in durable state.
        body = json.dumps({'event': record['event'], 'correlation': record['correlation']},
                          ensure_ascii=True, sort_keys=True)
        body = body.replace('@', r'\u0040').replace('MEDIA:', r'MEDIA\u003a').replace('[[', r'\u005b\u005b')
        try:
            result = subprocess.run(self.command + ['send', '--to', target, '--file', '-', '--json'],
                                    input=body, text=True, capture_output=True, timeout=30, check=False)
        except OSError:
            return 'not_sent'  # exec failed before the child could send
        except (subprocess.SubprocessError, UnicodeError):
            return 'uncertain'
        # Installed send_cmd.py reserves exit 2 for validation before send_message_tool.
        if result.returncode == 2:
            return 'not_sent'
        try:
            if result.returncode != 0 or len(result.stdout) > 1048576:
                return 'uncertain'
            reply = json.loads(result.stdout, object_pairs_hook=unique_fields)
            if (isinstance(reply, dict) and reply.get('success') is True
                    and not reply.get('error') and not reply.get('skipped')):
                return 'accepted'
        except (ValueError, RecursionError, IntakeError):
            pass
        # Never persist raw output/errors: they may contain platform credentials.
        return 'uncertain'
