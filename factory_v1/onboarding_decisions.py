"""Scoped setup direction in the admitted conversation; never an authority issuer."""
import copy
import json
import re
import sqlite3

from . import attention, controls, spending
from .assignments import digest
from .conversation_enrollment import PRODUCT_FIELDS
from .planning import require, text
from .repositories import RepositoryError

CAUSES = {
    'product': ('A product answer is missing.', 'Answer the exact product question.'),
    'platform': ('The approved platform setup is unavailable.', 'Wait for platform access to be restored, or defer this operation.'),
    'permission': ('The required permission is unavailable.', 'Approve only this exact scope, or decline; host access must still be verified.'),
    'allowance': ('The existing allowance does not cover this operation.', 'Defer, or request an exact bounded allowance through the trusted spending route.'),
    'unsupported': ('This integration is not supported.', 'Choose a supported alternative or defer; no fallback was selected.'),
    'uncertain': ('The previous effect is uncertain.', 'Reconcile the retained effect through its trusted readback; do not resend.'),
}


class OnboardingDecisions:
    """Host request/status/resume APIs and a raw, correlated native response tool."""

    def __init__(self, enrollment, config, setup=None):
        require(isinstance(config, dict) and set(config) == {'state'} and
                (config['state'] is None or (isinstance(config['state'], str) and config['state'].startswith('/'))),
                'Explicit controller state or repository-free mode required.', 'invalid_context')
        self.enrollment, self.store, self.ctx = enrollment, enrollment.store, enrollment.ctx
        self.config = copy.deepcopy(config)
        self.setup = setup

    def request(self, draft_id, operation, blockers, *, notification=None):
        """Host-only: retain immutable exact operation and independently scoped blockers.

        operation: operation_id/project/scope/ceiling. blockers: decision_id/cause/scope/ceiling/question.
        notification: existing attention event metadata (project/iteration/issue_number/run_id/evidence_ids).
        Empty blockers are quiet; this API neither proves readiness nor executes setup.
        """
        require(isinstance(operation, dict) and set(operation) == {'operation_id', 'project', 'scope', 'ceiling'} and
                attention.identifier(operation['operation_id']) and text(operation['project']) and
                isinstance(operation['scope'], dict) and bool(operation['scope']) and
                type(operation['ceiling']) is int and operation['ceiling'] >= 0,
                'Exact immutable setup operation required.', 'scope_mismatch')
        require(isinstance(blockers, list) and all(isinstance(b, dict) and
                set(b) == {'decision_id', 'cause', 'scope', 'ceiling', 'question'} and
                attention.identifier(b['decision_id']) and b['cause'] in CAUSES and
                isinstance(b['scope'], dict) and bool(b['scope']) and type(b['ceiling']) is int and
                0 <= b['ceiling'] <= operation['ceiling'] and text(b['question']) for b in blockers) and
                len({b['decision_id'] for b in blockers}) == len(blockers),
                'Distinct exact bounded setup decisions required.', 'scope_mismatch')
        if notification is not None:
            require(isinstance(notification, dict) and set(notification) ==
                    {'project', 'iteration', 'issue_number', 'run_id', 'evidence_ids'} and self.config['state'] is not None,
                    'Existing registered notification context required.', 'scope_mismatch')
        with self.store.locked():
            draft = self.store.read(draft_id)
            require(draft is not None, 'Retained admitted draft required.', 'not_found')
            actions = draft.setdefault('onboarding_decisions', {})
            key = operation['operation_id']
            record = {'operation': copy.deepcopy(operation), 'blockers': copy.deepcopy(blockers),
                      'notification': copy.deepcopy(notification), 'responses': {}, 'attempt': None}
            old = actions.get(key)
            if old is not None:
                require(all(old[k] == record[k] for k in ('operation', 'blockers', 'notification')),
                        'Retained operation cannot be rebound.', 'scope_mismatch')
                record = old
            else:
                actions[key] = record
                self.store.save(draft)
        self.notify(draft, record)
        return copy.deepcopy(record)

    def message(self, record, pending=None):
        blockers = record['blockers'] if pending is None else pending
        if not blockers:
            return ''
        lines = ['Project setup is held; no worker is running for this setup operation.']
        for number, b in enumerate(blockers, 1):
            cause, decision = CAUSES[b['cause']]
            lines.append(f"{number}. {cause} {b['question']} {decision} "
                         f"Maximum allowance: {b['ceiling']} units.")
        return '\n'.join(lines)

    def lifecycle(self, draft, record, action, request):
        n = record['notification']
        require(n is not None and self.config['state'] is not None, 'Registered attention context required.', 'not_found')
        with sqlite3.connect(self.config['state']) as database:
            item = attention.load_iteration(database, n['project'], n['iteration'])
            require(item.get('approval', {}).get('operator_id') == draft['operator_id'] and
                    all(draft['origin'].get(k) == v for k, v in item['origin'].items()),
                    'Registered owner/origin differs from admitted draft.', 'scope_mismatch')
            return attention.lifecycle(database, n['project'], n['iteration'], draft['operator_id'], action, request)

    def event(self, record, blocker):
        return 'setup-' + digest([record['operation'], blocker])[:48]

    def notify(self, draft, record):
        if record['notification'] is None:
            return  # Immediate questions use the existing native turn, not a new transport.
        n = record['notification']
        for b in record['blockers']:
            self.lifecycle(draft, record, 'attention', {
                'event_id': self.event(record, b), 'kind': 'decision', 'decision_id': self.event(record, b),
                'message': self.message(record, [b]), 'issue_number': n['issue_number'],
                'run_id': n['run_id'], 'evidence_ids': n['evidence_ids']})

    def current(self, draft_id, operation_id):
        draft = self.store.read(draft_id)
        require(draft is not None and operation_id in draft.get('onboarding_decisions', {}),
                'Exact retained setup operation required.', 'not_found')
        return draft, draft['onboarding_decisions'][operation_id]

    def probe(self, draft_id, record, session_id):
        """Fixed trusted producer route; direction never substitutes for host authority."""
        if self.setup is not None:
            draft = self.store.read(draft_id)
            scope = record['operation']['scope']
            require(set(scope) == {'repository', 'expected_revision'} and
                    scope['expected_revision'] == draft['revision'],
                    'Exact retained repository attachment revision required.', 'scope_mismatch')
            try:
                self.setup.capability(draft, scope['repository'])
            except (RepositoryError, OSError, ValueError):
                return {'operation': record['operation'], 'ready': False,
                        'pending': [b['decision_id'] for b in record['blockers']], 'reason': 'setup_authority_unavailable'}
            journal = self.setup.read(draft_id)
            if journal is not None:
                require(journal['repository'] == scope['repository'] and journal['draft_revision'] == scope['expected_revision'],
                        'Retained setup target differs.', 'scope_mismatch')
            pending = [b['decision_id'] for b in record['blockers'] if b['cause'] != 'permission' or
                       b['scope'] != {'repository': scope['repository']}]
            return {'operation': record['operation'], 'ready': not pending, 'pending': pending}
        args = {'draft_id': draft_id, 'operation': record['operation']}
        try:
            result = json.loads(self.ctx.dispatch_tool('factory_project_setup_status', args, session_id=session_id))
        except (ValueError, TypeError, KeyError, RuntimeError, RepositoryError):
            return {'ready': False, 'pending': [b['decision_id'] for b in record['blockers']],
                    'reason': 'setup_authority_unavailable'}
        require(isinstance(result, dict) and result.get('operation') == record['operation'] and
                type(result.get('ready')) is bool and isinstance(result.get('pending'), list) and
                set(result['pending']) <= {b['decision_id'] for b in record['blockers']} and
                (not result['ready'] or not result['pending']),
                'Exact fresh setup status required.', 'scope_mismatch')
        return result

    def status(self, draft_id, operation_id, *, session_id):
        """Read actual producer authority, not cached conversational approvals."""
        draft, record = self.current(draft_id, operation_id)
        fresh = self.probe(draft_id, record, session_id)
        pending = [b for b in record['blockers'] if b['decision_id'] in fresh['pending'] and
                   b['decision_id'] not in record['responses']]
        message = self.message(record, pending)
        if not fresh['ready'] and not message:
            message = 'Project setup remains held. Direction is retained; actual host authority or reconciliation is still required. No worker was started.'
        if any(a['direction'] == 'reject' for a in record['responses'].values()):
            message = 'Project setup is held because you declined this choice. No worker was started; choose a new supported proposal to continue.'
        return {**fresh, 'responses': copy.deepcopy(record['responses']), 'message': message}

    def respond(self, args, *, session_id='', **kwargs):
        """Retain approve/reject choices from actual input for the current displayed prompt."""
        require(isinstance(args, dict) and not args, 'No model-authored approval fields allowed.', 'scope_mismatch')
        with self.store.locked():
            draft, observed = self.enrollment.correlated(session_id)
            prompt = draft.get('decision_prompt')
            require(prompt is not None and prompt['revision'] == draft['revision'] and
                    draft.get('decision_input') == {'message_id': observed['message_id'], 'prompt': digest(prompt)},
                    'Answer the current displayed choice; stale or unrelated input is not approval.', 'approval_required')
            raw = observed['text'].strip().lower()
            if len(prompt['choices']) == 1 and raw in {'approve', 'reject'}:
                selected = [(1, raw)]
            else:
                parts = re.split(r'[,;\n]+', raw)
                require(all(re.fullmatch(r'\s*[1-9][0-9]*\s+(approve|reject)\s*', p) for p in parts),
                        'Reply approve/reject for one choice, or numbered choices such as 1 approve, 2 reject.', 'approval_required')
                selected = [(int(p.split()[0]), p.split()[1]) for p in parts]
            require(len({n for n, _ in selected}) == len(selected) and
                    all(1 <= n <= len(prompt['choices']) for n, _ in selected), 'Distinct displayed choices required.', 'approval_required')
            answers = []
            for number, direction in selected:
                choice = prompt['choices'][number - 1]
                record = draft['onboarding_decisions'][choice['operation']['operation_id']]
                b = choice['blocker']
                require(digest(record['operation']) == digest(choice['operation']) and
                        any(digest(b) == digest(current) for current in record['blockers']) and
                        b['decision_id'] not in record['responses'] and b['cause'] != 'uncertain' and
                        record['attempt'] is None, 'Choice changed or requires reconciliation.', 'approval_required')
                answers.append((record, b, {'decision_id': b['decision_id'], 'scope': b['scope'],
                    'ceiling': b['ceiling'], 'direction': direction}))
            for record, b, answer in answers:
                if record['notification'] is not None:
                    with sqlite3.connect(self.config['state']) as database:
                        item = attention.load_iteration(database, record['notification']['project'], record['notification']['iteration'])
                    self.lifecycle(draft, record, 'respond', {
                        'event_id': self.event(record, b), 'decision_id': self.event(record, b),
                        'operator_id': draft['operator_id'], 'origin': item['origin'],
                        'reference': observed['message_id'], 'response': json.dumps(answer, sort_keys=True)})
                record['responses'][answer['decision_id']] = {**answer, 'operator_id': draft['operator_id'],
                    'origin': draft['origin'], 'reference': observed['message_id']}
            draft['decision_prompt'] = None
            draft['decision_input'] = None
            self.store.save(draft)
        operations = dict.fromkeys(record['operation']['operation_id'] for record, _, _ in answers)
        return json.dumps({'results': [self.resume(draft['draft_id'], key, session_id=session_id) for key in operations]}, sort_keys=True)

    def resume(self, draft_id, operation_id, *, session_id):
        """Retry only the same retained producer operation after fresh trusted revalidation."""
        draft, record = self.current(draft_id, operation_id)
        fresh = self.status(draft_id, operation_id, session_id=session_id)
        if any(a['direction'] == 'reject' for a in record['responses'].values()):
            return {**fresh, 'resumed': False, 'reason': 'operator_declined'}
        if not fresh['ready'] or record['attempt'] is not None:
            return {**fresh, 'resumed': False, 'reason': 'reconciliation_required' if record['attempt'] else fresh.get('reason', 'prerequisite_pending')}
        if record['notification'] is not None:
            n = record['notification']
            with sqlite3.connect(self.config['state']) as database:
                controls.require_active(database, n['project'], n['iteration'])
                item = attention.load_iteration(database, n['project'], n['iteration'])
                require(item.get('status') == 'active' and not item.get('model_access', {}).get('status') == 'blocked',
                        'Independent pause or spending hold remains.', 'iteration_paused')
                model = record['operation']['scope'].get('model_operation')
                if model is not None:
                    spending.admission_preview(database, (n['project'], n['iteration'], model['provider'],
                        model['model'], model['operation']), reserve=record['operation']['ceiling'], allowance_only=False)
        with self.store.locked():
            draft, record = self.current(draft_id, operation_id)
            require(record['attempt'] is None, 'Reconcile the retained setup attempt.', 'execution_uncertain')
            record['attempt'] = {'state': 'uncertain'}
            self.store.save(draft)
        # The producer MUST re-read its exact real authority and preserve all holds at admission.
        if self.setup is not None:
            scope = record['operation']['scope']
            outcome = self.setup.attach(draft_id, scope['expected_revision'], scope['repository'], session_id=session_id)
            result = {'operation': record['operation'], 'status': 'complete' if outcome['status'] == 'ready' else 'uncertain',
                      'setup_status': outcome['status']}
        else:
            result = json.loads(self.ctx.dispatch_tool('factory_project_setup_resume',
                {'draft_id': draft_id, 'operation': record['operation']}, session_id=session_id))
        require(isinstance(result, dict) and result.get('operation') == record['operation'] and
                result.get('status') in {'complete', 'blocked', 'not_started', 'uncertain'},
                'Exact producer outcome required; reconcile before retry.', 'execution_uncertain')
        with self.store.locked():
            draft, record = self.current(draft_id, operation_id)
            record['attempt'] = None if result['status'] in {'blocked', 'not_started'} else result
            self.store.save(draft)
        return {**result, 'resumed': True}

    def inbound(self, **event):
        identity, draft_id = self.enrollment.identity(event['source'], event['platform'])
        draft = self.store.read(draft_id)
        if draft is None or not draft.get('onboarding_decisions'):
            return self.enrollment.inbound(**event)
        require(all(text(event.get(k)) for k in ('session_key', 'message_id', 'text')),
                'Actual admitted input required.', 'scope_mismatch')
        with self.store.locked():
            draft = self.store.read(draft_id)
            observation = {k: copy.deepcopy(event[k]) for k in ('session_key', 'message_id', 'text', 'source')}
            previous = next((o for o in draft['observations'] if o['message_id'] == event['message_id']), None)
            require(previous is None or previous == observation, 'Repeated input changed.', 'scope_mismatch')
            if previous is None:
                prompt = draft.get('decision_prompt')
                draft['decision_input'] = ({'message_id': event['message_id'], 'prompt': digest(prompt)}
                    if prompt is not None and prompt['anchor'] == draft['observations'][-1]['message_id'] else None)
                draft['observations'].append(observation)
            self.store.save(draft)
        return None

    def reconcile(self, draft_id, operation_id):
        """Host-only concrete setup readback; never accept a conversational attestation."""
        require(self.setup is not None, 'Trusted setup readback required.', 'execution_uncertain')
        draft, record = self.current(draft_id, operation_id)
        scope = record['operation']['scope']
        journal = self.setup.read(draft_id)
        require(journal is not None and journal['status'] == 'ready' and
                journal['repository'] == scope['repository'] and journal['draft_revision'] == scope['expected_revision'],
                'Exact completed setup readback required.', 'execution_uncertain')
        self.setup.route(draft_id)
        with self.store.locked():
            draft, record = self.current(draft_id, operation_id)
            record['attempt'] = {'operation': record['operation'], 'status': 'complete', 'reconciled': True}
            self.store.save(draft)
        return copy.deepcopy(record)

    def context(self, session_id, **kwargs):
        draft, observed = self.enrollment.correlated(session_id)
        actions = draft.get('onboarding_decisions', {})
        if not actions:
            return self.enrollment.context(session_id, **kwargs)
        messages, choices = [], []
        for key in actions:
            current = self.status(draft['draft_id'], key, session_id=session_id)
            if current['ready'] and actions[key]['attempt'] is None and not draft.get('decision_input'):
                self.resume(draft['draft_id'], key, session_id=session_id)
            if current['message']:
                messages.append(current['message'])
                choices.extend({'operation': copy.deepcopy(actions[key]['operation']), 'blocker': copy.deepcopy(b)}
                    for b in actions[key]['blockers'] if b['decision_id'] in current['pending'] and
                    b['decision_id'] not in actions[key]['responses'] and b['cause'] != 'uncertain' and actions[key]['attempt'] is None)
        if not messages:
            return None
        with self.store.locked():
            draft = self.store.read(draft['draft_id'])
            prompt = draft.get('decision_prompt')
            responding = prompt is not None and draft.get('decision_input') == {
                'message_id': observed['message_id'], 'prompt': digest(prompt)}
            if not responding:
                prompt = {'anchor': observed['message_id'], 'revision': draft['revision'], 'choices': choices}
                draft['decision_prompt'] = prompt
                draft['decision_input'] = None
                self.store.save(draft)
        displayed = ['Project setup is held; no worker is running for this setup operation.']
        for number, choice in enumerate(prompt['choices'], 1):
            b = choice['blocker']
            cause, decision = CAUSES[b['cause']]
            displayed.append(f"{number}. For {choice['operation']['project']}: {cause} {b['question']} {decision} Maximum allowance for this choice: {b['ceiling']} units; total operation maximum: {choice['operation']['ceiling']} units.")
        if not prompt['choices']:
            displayed = messages
        return {'context': '\n'.join(displayed) + '\nPresent these choices faithfully. Reply approve or reject for a single choice, '
                'or numbered choices, for example 1 approve, 2 reject. Partial answers are retained. '
                'Use factory_project_setup_response with no arguments only for that actual operator input. '
                'These are directions, not host authority. Never infer approval from yes or unrelated chat. '
                'For product choices explain the recommendation in the question; never request private configuration edits.'}

    def register(self):
        self.ctx.register_hook('post_gateway_admission', self.inbound)
        self.ctx.register_hook('pre_llm_call', self.context)
        properties = {'expected_revision': {'type': 'integer'},
                      'product': {'type': 'object', 'properties': {k: {'type': 'string'} for k in PRODUCT_FIELDS}, 'additionalProperties': False},
                      'pending_questions': {'type': 'array', 'items': {'type': 'string'}},
                      'sufficient': {'type': 'boolean'}, 'rationale': {'type': 'string'}}
        for name, handler, props in (
            ('factory_project_draft_update', self.enrollment.update, properties),
            ('factory_project_setup_response', self.respond, {}),
        ):
            self.ctx.register_tool(name=name, toolset='factory_planning', handler=handler,
                schema={'name': name, 'description': handler.__doc__, 'parameters': {'type': 'object',
                    'properties': props, 'required': list(props), 'additionalProperties': False}})
        return self
