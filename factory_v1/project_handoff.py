"""Conversation adapters over existing setup, native publication and continuation."""
import copy
import re

from . import assignments, planning_wakeup, readiness, repositories
from .conversation_enrollment import native_context
from .planning import require, text
from .service import connect


class ToolContext:
    """Reuse component tool schemas while the composition owns native hooks."""

    def __init__(self, ctx):
        self.ctx = ctx

    def __getattr__(self, name):
        return getattr(self.ctx, name)

    def register_hook(self, name, handler):
        return None


def tools_only(component):
    original = component.ctx
    component.ctx = ToolContext(original)
    try:
        component.register()
    finally:
        component.ctx = original


class ProjectHandoff:
    """One host owner; never a replacement execution or delivery controller."""

    def __init__(self, enrollment, setup, creation, tracker, execution, decisions, config):
        require(isinstance(config, dict) and set(config) == {'publication_config', 'wakeup_config'} and
                all(v is None or (isinstance(v, str) and v.startswith('/')) for v in config.values()),
                'Explicit native publisher and wakeup selections required.', 'approval_required')
        self.enrollment, self.ctx = enrollment, enrollment.ctx
        self.setup, self.creation, self.tracker, self.execution = setup, creation, tracker, execution
        self.decisions, self.config = decisions, copy.deepcopy(config)
        self.routes = {}

    def selected(self, draft):
        product = draft['product']
        mode = product.get('repository_mode', 'new')
        require(mode in {'existing', 'new'}, 'Choose an existing repository or a new private product.', 'invalid_repository')
        repository = product.get('repository')
        if mode == 'new' and repository is None:
            require(self.creation is not None, 'Private repository creation is unavailable.', 'creation_unavailable')
            name = re.sub(r'[^a-z0-9._-]+', '-', product['name'].lower()).strip('-')
            repository = self.creation.config['account'] + '/' + name
        require(repositories.valid_repository(repository), 'Select the exact repository target.', 'invalid_repository')
        if mode == 'existing':
            require(any(repository in row['text'] for row in draft['observations']),
                    'The existing target must occur literally in admitted operator input.', 'approval_required')
        return mode, repository

    def operation(self, draft, producer, repository):
        scope = {'producer': producer, 'repository': repository, 'expected_revision': draft['revision']}
        return {'operation_id': 'onboard-' + assignments.digest(scope)[:48],
                'project': draft['product']['name'], 'scope': scope, 'ceiling': 0}

    def blocked(self, draft, producer, repository, result):
        if self.decisions is not None:
            blocker = result.get('blocker') or {}
            cause = blocker.get('cause', 'platform')
            if blocker.get('reconciliation_required') or 'uncertain' in blocker.get('code', ''):
                cause = 'uncertain'
            if cause == 'capacity':
                cause = 'allowance'
            if cause not in {'product', 'platform', 'permission', 'allowance', 'unsupported', 'uncertain'}:
                cause = 'platform'
            operation = self.operation(draft, producer, repository)
            self.decisions.request(draft['draft_id'], operation, [{
                'decision_id': 'prerequisite', 'cause': cause, 'scope': operation['scope'], 'ceiling': 0,
                'question': blocker.get('next_decision', 'Restore the scoped platform prerequisite; no authority is granted by replying.')}])
        return result

    def perform(self, draft, producer, repository, session_id):
        identity, revision = draft['draft_id'], draft['revision']
        if producer == 'setup':
            return self.setup.attach(identity, revision, repository, session_id=session_id)
        if producer == 'create':
            require(self.creation is not None, 'Private creation unavailable.', 'creation_unavailable')
            return self.creation.create(identity, revision, repository, session_id=session_id)
        if producer == 'tracker':
            require(self.tracker is not None, 'Tracker setup unavailable.', 'tracker_setup_required')
            return self.tracker.prepare(identity, revision)
        require(producer == 'execution' and self.execution is not None,
                'Execution setup unavailable.', 'execution_capability_required')
        if self.execution is None:
            raise repositories.RepositoryError('execution_capability_required', 'Execution unavailable.')
        return self.execution.register(identity, revision)

    def advance(self, draft, session_id):
        """Automatic setup; native planning deliberately precedes engineering readiness."""
        if not draft['sufficient'] or draft['pending_questions']:
            return {'status': 'interview'}
        mode, repository = self.selected(draft)
        producer = 'setup' if mode == 'existing' else 'create'
        attached = self.perform(draft, producer, repository, session_id)
        if attached['status'] != 'ready':
            return self.blocked(draft, producer, repository, attached)
        native = self.route(draft['draft_id'])
        return {'status': 'planning', 'binding': native.config}

    def route(self, draft_id):
        native = self.setup.route(draft_id)
        native.publication_config = self.config['publication_config']
        previous = self.routes.get(draft_id)
        if previous is not None:
            require(previous.config == native.config, 'Native binding changed; reconcile the original session.', 'scope_mismatch')
            return previous
        self.routes[draft_id] = native
        return native

    def inbound(self, **event):
        if self.decisions is not None:
            return self.decisions.inbound(**event)
        # Transferred input stays in the exact native route; it is not a new interview.
        return self.enrollment.inbound(**event)

    def context(self, session_id, **kwargs):
        host = native_context()
        source = {k: host.get(k, '') for k in (
            'platform', 'chat_id', 'chat_type', 'thread_id', 'scope_id', 'parent_chat_id', 'user_id')}
        _, identity = self.enrollment.identity(source, host.get('platform'))
        draft = self.enrollment.store.read(identity)
        require(draft is not None, 'Admitted project input required.', 'scope_mismatch')
        if draft.get('onboarding_decisions') and self.decisions is not None:
            decision = self.decisions.context(session_id, **kwargs)
            if decision is not None:
                return decision
        if draft.get('transfer'):
            native = self.route(identity)
            require(session_id == native.config['session_id'], 'Use the original native session.', 'scope_mismatch')
            result = native.context(session_id, **kwargs)
            if result is None:
                self.after_publication(identity)
            return result
        return self.enrollment.context(session_id, **kwargs)

    def native_tool(self, name, args, *, session_id='', **kwargs):
        host = native_context()
        source = {k: host.get(k, '') for k in (
            'platform', 'chat_id', 'chat_type', 'thread_id', 'scope_id', 'parent_chat_id', 'user_id')}
        _, identity = self.enrollment.identity(source, host.get('platform'))
        result = getattr(self.route(identity), name)(args, session_id=session_id, **kwargs)
        if name == 'publish':
            # Consumption re-reads real native execution and immutable publication;
            # neither returned status nor a loader/enqueue receipt grants progress.
            self.after_publication(identity)
        return result

    def after_publication(self, draft_id):
        if self.consume(draft_id) != 'consumed':
            return {'status': 'planning-pending'}
        draft = self.enrollment.store.read(draft_id)
        attached = self.setup.read(draft_id)
        repository = attached['repository']
        tracked = self.perform(draft, 'tracker', repository, attached['binding']['session_id'])
        if tracked['status'] != 'ready':
            return self.blocked(draft, 'tracker', repository, tracked)
        executed = self.perform(draft, 'execution', repository, attached['binding']['session_id'])
        if executed['status'] != 'registered':
            return self.blocked(draft, 'execution', repository, executed)
        return executed

    def consume(self, draft_id):
        """Read actual execution/publication authority in either native scheduling order."""
        native = self.route(draft_id)
        require(self.config['wakeup_config'] is not None, 'Exact native wakeup authority required.', 'approval_required')
        with connect(native.config) as database:
            item, _ = native.current(database, native.config['session_id'])
            return planning_wakeup.supervise(database, item, self.config['wakeup_config'], native.github)

    def producer_status(self, draft_id, operation, session_id):
        draft = self.enrollment.store.read(draft_id)
        scope = operation['scope']
        require(set(scope) == {'producer', 'repository', 'expected_revision'} and
                scope['expected_revision'] == draft['revision'] and operation == self.operation(
                    draft, scope['producer'], scope['repository']), 'Retained producer scope changed.', 'scope_mismatch')
        producer, repository = scope['producer'], scope['repository']
        require(producer in {'setup', 'create', 'tracker', 'execution'}, 'Unknown producer.', 'scope_mismatch')
        try:
            self.setup.capability(draft, repository)
            if producer == 'create':
                require(self.creation is not None and repository.split('/')[0] == self.creation.config['account'],
                        'Exact reviewed creation account required.', 'approval_required')
            elif producer == 'tracker':
                require(self.tracker is not None, 'Tracker unavailable.', 'tracker_setup_required')
                capability = readiness.load_config(self.tracker.config['capability_config'])
                require(capability['repository'] == repository and capability['operator_id'] == draft['operator_id'],
                        'Tracker authority differs.', 'scope_mismatch')
            elif producer == 'execution':
                require(self.execution is not None, 'Execution unavailable.', 'execution_capability_required')
                self.execution.inventory(draft, self.setup.read(draft_id))
        except (repositories.RepositoryError, OSError, ValueError, KeyError):
            return False
        return True

    def producer_resume(self, draft_id, operation, session_id):
        require(self.producer_status(draft_id, operation, session_id), 'Host prerequisite remains blocked.', 'approval_required')
        draft = self.enrollment.store.read(draft_id)
        scope = operation['scope']
        result = self.perform(draft, scope['producer'], scope['repository'], session_id)
        # Producer retains/reconciles its own unknown effects; never blind retry an admitted operation.
        return {'operation': operation, 'status': 'complete' if result['status'] in {'ready', 'registered'} else
                'uncertain' if (result.get('blocker') or {}).get('reconciliation_required') else 'blocked',
                'producer_status': result['status']}

    def register(self):
        self.enrollment.handoff = self
        if self.decisions is None:
            tools_only(self.enrollment)
        if self.decisions is not None:
            self.decisions.producer = self
            tools_only(self.decisions)
        # Native tool schemas remain the original public contracts.
        from .conversation_binding import ConversationBinding
        native = object.__new__(ConversationBinding)
        native.ctx = ToolContext(self.ctx)
        for name in ('sufficient', 'publish', 'complete'):
            def handler(args, *, session_id='', _name=name, **kwargs):
                return self.native_tool(_name, args, session_id=session_id, **kwargs)
            handler.__doc__ = getattr(ConversationBinding, name).__doc__
            setattr(native, name, handler)
        native.register()
        # Replace only the draft handler, not its schema or native identity checks.
        self.ctx.register_hook('post_gateway_admission', self.inbound)
        self.ctx.register_hook('pre_llm_call', self.context)
        return self
