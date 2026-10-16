"""Repository-free intake in the admitted native conversation, without execution grants."""
import copy
from contextlib import contextmanager
from contextvars import copy_context
import fcntl
import json
import os
from pathlib import Path

from . import skills
from .assignments import digest
from .controls import private
from .conversation_binding import ConversationBinding
from .planning import require, text
from .sandbox_source import physical
from .service import atomic, isolated_root

PRODUCT_FIELDS = ('name', 'intent', 'objectives', 'users', 'milestone', 'constraints', 'acceptance')
ORIGIN_FIELDS = ('platform', 'chat_id', 'chat_type', 'thread_id', 'scope_id', 'parent_chat_id', 'profile')


def native_context():
    """Read only actually bound task-local host variables; never environment fallback."""
    return {var.name.removeprefix('HERMES_SESSION_').lower(): value
            for var, value in copy_context().items() if var.name.startswith('HERMES_SESSION_')}


class DraftStore:
    """Private durable draft snapshots. Host consumers read/transfer; no execution authority."""

    def __init__(self, root):
        self.root = physical(root, directory=True)
        private(self.root, directory=True)
        isolated_root(self.root)

    @contextmanager
    def locked(self):
        path = self.root / 'lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as lock:
            private(path)
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def path(self, draft_id):
        require(isinstance(draft_id, str) and len(draft_id) == 64 and
                all(c in '0123456789abcdef' for c in draft_id), 'Exact draft ID required.', 'scope_mismatch')
        return self.root / (draft_id + '.json')

    def read(self, draft_id):
        """Return an independent snapshot or None; trusted host-only API, not a model tool."""
        path = self.path(draft_id)
        if not path.exists():
            return None
        private(path)
        result = json.loads(path.read_text())
        require(result['draft_id'] == draft_id, 'Draft identity changed.', 'scope_mismatch')
        return result

    def save(self, draft):
        atomic(self.path(draft['draft_id']), draft)

    def transfer(self, draft_id, expected_revision, binding):
        """Retain a host-verified T2 binding receipt, not verify/create it or grant authority."""
        require(isinstance(binding, dict) and set(binding) == {'project', 'iteration', 'assignment_id', 'reference'} and
                all(text(v) for v in binding.values()), 'Verified binding receipt required.', 'scope_mismatch')
        with self.locked():
            draft = self.read(draft_id)
            require(draft is not None and draft['revision'] == expected_revision and draft['sufficient'] and
                    not draft['pending_questions'], 'Current sufficient draft required.', 'planning_conflict')
            require(draft.get('transfer') in (None, binding), 'Draft already transferred elsewhere.', 'scope_mismatch')
            draft['transfer'] = copy.deepcopy(binding)
            self.save(draft)
            return copy.deepcopy(draft)


class ConversationEnrollment:
    """Capture admitted input and feed selected interview instructions into that native turn."""

    def __init__(self, ctx, config):
        require(isinstance(config, dict) and set(config) == {'root', 'operators', 'skills'} and
                isinstance(config['root'], str) and Path(config['root']).is_absolute() and
                isinstance(config['operators'], dict) and bool(config['operators']) and
                all(text(platform) and isinstance(users, list) and users and all(text(u) for u in users)
                    for platform, users in config['operators'].items()) and text(ctx.profile_name),
                'Separate host platform/operator policy and private draft root required.', 'approval_required')
        self.ctx, self.config = ctx, copy.deepcopy(config)
        self.bound_planning = None
        self.store = DraftStore(config['root'])
        self.selected = skills.resolve_installed(config['skills'])

    def identity(self, source, platform):
        require(isinstance(source, dict) and source.get('platform') == platform and
                text(source.get('user_id')) and text(source.get('chat_id')) and
                source['user_id'] in self.config['operators'].get(platform, []) and
                source.get('profile') in (None, '', self.ctx.profile_name),
                'Authenticated admitted operator and matching host profile required.', 'approval_required')
        origin = {k: source.get(k) or '' for k in ORIGIN_FIELDS}
        require(all(isinstance(v, str) for v in origin.values()), 'Exact source route required.', 'scope_mismatch')
        identity = {'origin': origin, 'operator_id': source['user_id'], 'host_profile': self.ctx.profile_name}
        return identity, digest(identity)

    def inbound(self, *, session_key, platform, source, message_id, text, **kwargs):
        """Called only by post_gateway_admission, never by a model or pre-auth hook."""
        if self.bound_planning is not None and isinstance(source, dict) and all(
                source.get(k) == v for k, v in self.bound_planning['origin'].items()):
            return None
        from .repositories import RepositoryError
        try:
            identity, draft_id = self.identity(source, platform)
            require(all(isinstance(v, str) and v.strip() for v in (session_key, message_id, text)),
                    'Nonempty admitted message reference required.', 'scope_mismatch')
            observation = {'message_id': message_id, 'session_key': session_key, 'text': text,
                           'source': copy.deepcopy(source)}
            with self.store.locked():
                draft = self.store.read(draft_id)
                if draft is None:
                    draft = {'version': 1, 'draft_id': draft_id, **identity, 'revision': 0,
                             'observations': [], 'product': {}, 'pending_questions': [],
                             'sufficient': False, 'rationale': '', 'native': None, 'transfer': None}
                previous = next((o for o in draft['observations'] if o['message_id'] == message_id), None)
                if previous is not None:
                    # Reconnect may change routing key, never the observed text or source identity.
                    require(previous['text'] == text and previous['source'] == source,
                            'Repeated message changed.', 'scope_mismatch')
                    previous['session_key'] = session_key
                else:
                    require(draft['transfer'] is None, 'Use the established project route.', 'planning_conflict')
                    draft['observations'].append(observation)
                    draft['sufficient'] = False
                    draft['native'] = None
                    draft['revision'] += 1
                self.store.save(draft)
        except RepositoryError:
            return {'action': 'handled', 'reply': 'Project intake is held: a nonempty message from an approved '
                    'authenticated operator in the originating conversation is required. No project action was authorized.'}
        return None  # Continue the actual admitted agent turn; do not synthesize a user turn.

    def correlated(self, session_id):
        host = native_context()
        require(text(session_id) and host.get('id') == session_id and text(host.get('key')) and
                text(host.get('message_id')), 'Actual task-local native message context required.', 'scope_mismatch')
        source = {k: host.get(k, '') for k in ORIGIN_FIELDS}
        source['user_id'] = host.get('user_id', '')
        identity, draft_id = self.identity(source, host.get('platform'))
        draft = self.store.read(draft_id)
        require(draft is not None and all(draft[k] == v for k, v in identity.items()),
                'No admitted conversation draft.', 'scope_mismatch')
        observed = next((o for o in draft['observations'] if o['message_id'] == host['message_id'] and
                         o['session_key'] == host['key']), None)
        require(observed is not None and observed == draft['observations'][-1],
                'No current matching admitted input.', 'scope_mismatch')
        return draft, observed

    def context(self, session_id, *, task_id='', turn_id='', sender_id='', user_message=None, **kwargs):
        if self.bound_planning is not None and session_id == self.bound_planning['session_id']:
            return None
        with self.store.locked():
            draft, observed = self.correlated(session_id)
            require(text(task_id) and text(turn_id) and sender_id == draft['operator_id'] and text(user_message),
                    'Actual native task/turn/sender/input required.', 'scope_mismatch')
            # Shared-channel renderings are host text, not an identity or answer source.
            # The exact raw input and message ID come exclusively from admission.
            raw = observed['text']
            source = observed['source']
            renderings = [raw]
            if text(source.get('user_name')):
                if source['platform'] == 'discord':
                    renderings.append('[' + source['user_name'] + '] ' + raw)
                elif source['platform'] == 'slack':
                    renderings.append('[' + source['user_name'] + ' | Slack user <@' + source['user_id'] + '>] ' + raw)
            require(user_message in renderings, 'Native turn does not match admitted input.', 'scope_mismatch')
            require(draft['transfer'] is None, 'Use the established planning route.', 'planning_conflict')
            selected = skills.resolve_installed(self.config['skills'])
            require(selected == self.selected, 'Selected interview sources changed.', 'skill_blocked')
            instructions = [ConversationBinding.load(self, name, selected, session_id) for name in ('grill-me', 'grilling')]
            draft['native'] = {'session_id': session_id, 'task_id': task_id, 'turn_id': turn_id,
                               'message_id': observed['message_id'], 'source_digest': digest(selected)}
            self.store.save(draft)
            return {'context': '\n\n'.join([*instructions,
                'This is repository-free project intake, not permission to onboard, spend, publish or code. '
                'Only if the actual user requests a project or answers its interview, follow the selected interview '
                'skills in this turn. Extract already supplied name, intent, objectives, users, finite milestone, '
                'constraints and acceptance into product fields. Recommend defaults; ask only unresolved '
                'decision-changing questions. Do not ask answered questions. Silence, acknowledgements and '
                'unrelated chat do not resolve decisions. Preserve the raw observations verbatim. '
                'Use factory_project_draft_update to retain your product interpretation and pending questions '
                'before replying. These are model judgments, never authenticated approvals. '
                'Complete input needs no routine confirmation; later setup is a separate verified host operation.',
                'Retained draft: ' + json.dumps(draft, sort_keys=True)])}

    def update(self, args, *, session_id='', **kwargs):
        """Retain product interpretation/questions in the currently admitted native turn; no authority granted."""
        with self.store.locked():
            draft, observed = self.correlated(session_id)
            require(draft['native'] is not None and draft['native']['session_id'] == session_id and
                    draft['native']['message_id'] == observed['message_id'] and draft['transfer'] is None,
                    'Load interview instructions in this actual native turn first.', 'execution_required')
            require(digest(skills.resolve_installed(self.config['skills'])) == draft['native']['source_digest'],
                    'Interview sources changed.', 'skill_blocked')
            require(isinstance(args, dict) and set(args) == {'expected_revision', 'product', 'pending_questions', 'sufficient', 'rationale'} and
                    type(args['expected_revision']) is int and args['expected_revision'] == draft['revision'] and
                    isinstance(args['product'], dict) and set(args['product']) <= set(PRODUCT_FIELDS) and
                    all(text(v) for v in args['product'].values()) and isinstance(args['pending_questions'], list) and
                    all(text(q) for q in args['pending_questions']) and type(args['sufficient']) is bool and text(args['rationale']) and
                    (not args['sufficient'] or not args['pending_questions']),
                    'Current product judgment and unresolved questions required.', 'planning_conflict')
            draft['product'].update(args['product'])
            require(not args['sufficient'] or all(text(draft['product'].get(k)) for k in PRODUCT_FIELDS),
                    'Retain all supplied product dimensions before declaring sufficiency.', 'planning_conflict')
            draft.update(pending_questions=copy.deepcopy(args['pending_questions']), sufficient=args['sufficient'],
                         rationale=args['rationale'], revision=draft['revision'] + 1)
            self.store.save(draft)
            return json.dumps(draft, sort_keys=True)

    def register(self):
        self.ctx.register_hook('post_gateway_admission', self.inbound)
        self.ctx.register_hook('pre_llm_call', self.context)
        properties = {'expected_revision': {'type': 'integer'},
                      'product': {'type': 'object', 'properties': {k: {'type': 'string'} for k in PRODUCT_FIELDS},
                                  'additionalProperties': False},
                      'pending_questions': {'type': 'array', 'items': {'type': 'string'}},
                      'sufficient': {'type': 'boolean'}, 'rationale': {'type': 'string'}}
        self.ctx.register_tool(name='factory_project_draft_update', toolset='factory_planning', handler=self.update,
            schema={'name': 'factory_project_draft_update', 'description': self.update.__doc__,
                    'parameters': {'type': 'object', 'properties': properties, 'required': list(properties),
                                   'additionalProperties': False}})
        return self
