"""Conversation adapters over existing setup, native publication and continuation."""
import copy
import re
import threading

from . import assignments, planning_wakeup, readiness, repositories, service_dispatch, tickets
from .conversation_enrollment import native_context
from .planning import require
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

    def __init__(self, enrollment, setup, creation, tracker, execution, decisions, config, native_project=None):
        require(isinstance(config, dict) and set(config) == {'publication_config', 'wakeup_config'} and
                all(v is None or (isinstance(v, str) and v.startswith('/')) for v in config.values()),
                'Explicit native publisher and wakeup selections required.', 'approval_required')
        self.enrollment, self.ctx = enrollment, enrollment.ctx
        self.setup, self.creation, self.tracker, self.execution = setup, creation, tracker, execution
        self.decisions, self.config = decisions, copy.deepcopy(config)
        self.routes = {}
        self.native_project = native_project
        self.tasks = {}
        self.closed = False
        self.stop_event = threading.Event()

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
        require(producer in {'planning', 'execution'} and self.execution is not None,
                'Execution setup unavailable.', 'execution_capability_required')
        if self.execution is None:
            raise repositories.RepositoryError('execution_capability_required', 'Execution unavailable.')
        if producer == 'planning':
            return self.execution.prepare_planning(identity, revision)
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
        tracked = self.perform(draft, 'tracker', repository, session_id)
        if tracked['status'] != 'ready':
            return self.blocked(draft, 'tracker', repository, tracked)
        require(self.execution is not None, 'Preplanning capability preparation required.', 'execution_capability_required')
        prepared = self.execution.prepare_planning(draft['draft_id'], draft['revision'])
        if prepared['status'] != 'planning_prepared':
            return self.blocked(draft, 'planning', repository, prepared)
        native = self.route(draft['draft_id'])
        self.schedule(draft['draft_id'])
        return {'status': 'planning', 'binding': native.config}

    def route(self, draft_id):
        native = self.setup.route(draft_id)
        if self.native_project is not None:
            native = self.native_project.bind(native, draft_id)
        else:
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
                self.schedule(identity)
            return result
        return self.enrollment.context(session_id, **kwargs)

    def native_tool(self, name, args, *, session_id='', **kwargs):
        host = native_context()
        source = {k: host.get(k, '') for k in (
            'platform', 'chat_id', 'chat_type', 'thread_id', 'scope_id', 'parent_chat_id', 'user_id')}
        _, identity = self.enrollment.identity(source, host.get('platform'))
        result = getattr(self.route(identity), name)(args, session_id=session_id, **kwargs)
        if name in {'publish', 'complete'}:
            # Consumption re-reads real native execution and immutable publication;
            # neither returned status nor a loader/enqueue receipt grants progress.
            self.schedule(identity)
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
        require(self.execution is not None, 'Execution setup unavailable.', 'execution_capability_required')
        # Before service registration: use the existing guarded public producers,
        # not service.tick (which requires the policy this publication produces).
        host = self.execution.inventory(draft, attached)
        native = self.route(draft_id)
        with connect(native.config) as database, repositories.state_lock(database):
            require(self.native_project is not None, 'Explicit host ticket transport required.', 'approval_required')
            from .project_native import TicketTransport
            github = TicketTransport(self.native_project, draft_id, database, native)
            for _ in range(3):
                require(not self.stop_event.is_set(), 'Project handoff is stopping.', 'service_stopping')
                item, _ = native.current(database, native.config['session_id'])
                work = item.get('ticket_work')
                if work is None:
                    board = self.tracker.tracker_config(draft_id)
                    tickets.synthesize(database, item, {'tracker': board, 'skill': host['ticket_skill']}, github)
                elif work.get('status') == 'synthesis_pending':
                    service_dispatch.synthesize(database, item, {'synthesis_config': host['synthesis_config']}, github,
                                                stop_event=self.stop_event)
                elif not work.get('publication_complete'):
                    tickets.publish(database, item, github)
                else:
                    break
            # Actual durable publication, not an executor return, admits activation.
            item, _ = native.current(database, native.config['session_id'])
            work = item.get('ticket_work', {})
            require(work.get('status') == 'published' and work.get('publication_complete') is True,
                    'Actual ticket publication readback required.', 'execution_publication_required')
        require(not self.stop_event.is_set(), 'Project handoff is stopping.', 'service_stopping')
        executed = self.perform(draft, 'execution', repository, attached['binding']['session_id'])
        if executed['status'] != 'registered':
            return self.blocked(draft, 'execution', repository, executed)
        return executed

    def consume(self, draft_id):
        """Read actual execution/publication authority in either native scheduling order."""
        native = self.route(draft_id)
        selected = self.native_project.resolve(draft_id) if self.native_project is not None else self.config
        require(selected['wakeup_config'] is not None, 'Exact native wakeup authority required.', 'approval_required')
        with connect(native.config) as database:
            item, _ = native.current(database, native.config['session_id'])
            return planning_wakeup.supervise(database, item, selected['wakeup_config'], native.github)

    def schedule(self, draft_id):
        """One host task per project; durable producers, not a polling controller."""
        previous = self.tasks.get(draft_id)
        if self.closed or (previous is not None and not previous.done()):
            return
        async def run():
            # Every operation below owns its normal public admission/readback.
            # No retry loop: uncertain effects remain in the producer journal.
            try:
                return self.after_publication(draft_id)
            except (repositories.RepositoryError, OSError, ValueError, KeyError) as error:
                draft = self.enrollment.store.read(draft_id)
                attached = self.setup.read(draft_id)
                return self.blocked(draft, 'execution', attached['repository'], {'status': 'blocked',
                    'blocker': {'code': getattr(error, 'code', 'handoff_platform_unavailable'),
                                'next_decision': 'Restore the exact host prerequisite; retained effects require producer readback.'}})
        coroutine = run()
        try:
            self.tasks[draft_id] = self.ctx.spawn_task(coroutine, name='factory-project-handoff-' + draft_id[:12])
        except Exception:
            coroutine.close()
            raise

    def close(self):
        self.closed = True
        self.stop_event.set()
        for task in self.tasks.values():
            task.cancel()
        if self.native_project is not None:
            self.native_project.close()

    def producer_status(self, draft_id, operation, session_id):
        draft = self.enrollment.store.read(draft_id)
        scope = operation['scope']
        require(set(scope) == {'producer', 'repository', 'expected_revision'} and
                scope['expected_revision'] == draft['revision'] and operation == self.operation(
                    draft, scope['producer'], scope['repository']), 'Retained producer scope changed.', 'scope_mismatch')
        producer, repository = scope['producer'], scope['repository']
        require(producer in {'setup', 'create', 'tracker', 'planning', 'execution'}, 'Unknown producer.', 'scope_mismatch')
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
            elif producer in {'planning', 'execution'}:
                require(self.execution is not None, 'Execution unavailable.', 'execution_capability_required')
                self.execution.inventory(draft, self.setup.read(draft_id))
        except (repositories.RepositoryError, OSError, ValueError, KeyError):
            return False
        return True

    def producer_resume(self, draft_id, operation, session_id):
        require(self.producer_status(draft_id, operation, session_id), 'Host prerequisite remains blocked.', 'approval_required')
        draft = self.enrollment.store.read(draft_id)
        scope = operation['scope']
        producer = scope['producer']
        result = self.perform(draft, producer, scope['repository'], session_id)
        complete = result['status'] in {'ready', 'registered', 'planning_prepared'}
        if complete:
            # Public producers perform real external readbacks. Require their
            # durable journal to agree before advancing anything else.
            component = {'setup': self.setup, 'create': self.creation,
                         'tracker': self.tracker, 'planning': self.execution, 'execution': self.execution}[producer]
            retained = component.read(draft_id)
            require(retained is not None and retained['draft_id'] == draft_id and
                    retained['draft_revision'] == draft['revision'] and retained['status'] == result['status'],
                    'Producer completion journal differs.', 'execution_uncertain')
        return {'operation': operation, 'status': 'complete' if complete else
                'uncertain' if (result.get('blocker') or {}).get('reconciliation_required') else 'blocked',
                'producer_status': result['status']}

    def producer_continue(self, draft_id, operation, session_id):
        """Called only after the exact producer outcome has been durably retained."""
        draft = self.enrollment.store.read(draft_id)
        if operation['scope']['producer'] in {'setup', 'create', 'tracker'}:
            return self.advance(draft, session_id)
        self.schedule(draft_id)
        return {'status': 'scheduled'}

    def register(self):
        self.enrollment.handoff = self
        self.ctx.on_unload(self.close)
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
