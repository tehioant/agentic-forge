"""Native same-origin planning: host observations, installed skills and publication."""
import copy
import json
import uuid
from pathlib import Path

from . import assignments, planning, planning_publication, skills
from .planning import require, text
from .repositories import state_lock
from .service import connect


class ConversationBinding:
    """Bind one approved live native session to an existing planning assignment."""

    def __init__(self, ctx, config, github, publication_config=None):
        fields = {'state', 'operator_id', 'project', 'iteration', 'session_id', 'origin', 'reference'}
        require(isinstance(config, dict) and set(config) == fields and
                all(text(config[k]) for k in fields - {'origin'}) and
                Path(config['state']).is_absolute() and config['state'] != ':memory:' and
                isinstance(config['origin'], dict),
                'An exact host-approved durable conversation binding is required.', 'approval_required')
        self.ctx, self.config, self.github = ctx, dict(config), github
        self.publication_config = publication_config
        self.wakeup = None
        self.loaded = {}  # Legacy loader-only callers cannot use the strict publication route.

    def current(self, database, session_id):
        require(session_id == self.config['session_id'], 'Use the approved originating native session.', 'scope_mismatch')
        item = assignments.current(database, *(self.config[k] for k in ('project', 'iteration', 'operator_id')))
        require(item['origin'] == self.config['origin'] and item.get('planning') is not None and
                item['planning']['origin'] == item['origin'],
                'The native binding does not match the planning origin.', 'scope_mismatch')
        from .controls import require_active
        require_active(database, item['project_id'], item['iteration_id'])
        selected = skills.resolve_installed(item['planning']['request']['skills'])
        require(selected == item['planning']['skills'] and item['planning']['instructions'] == planning.INSTRUCTIONS,
                'Planning sources or approved adaptation changed.', 'skill_blocked')
        return item, selected

    def load(self, name, selected, session_id):
        raw = self.ctx.dispatch_tool('skill_view', {'name': name}, session_id=session_id)
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise ValueError('Native skill loader returned invalid JSON.') from error
        require(result.get('success') is True and result.get('name') == name and
                result.get('content') == selected[name]['instructions'],
                'Native loader must return the exact selected installed instructions.', 'skill_blocked')
        return result['content']

    def context(self, session_id, *, task_id='', turn_id='', user_message=None,
                conversation_history=None, sender_id='', **kwargs):
        """Observe native host input; repeated pre_llm calls preserve tool-loop correlation."""
        if session_id != self.config['session_id']:
            return None
        with connect(self.config) as database, state_lock(database):
            item, selected = self.current(database, session_id)
            work = item['planning']
            if work['status'] == 'complete':
                return None
            native = work.get('native_execution')
            from .planning_wakeup import TAG, observe_wakeup
            wakeup = isinstance(user_message, str) and TAG in user_message
            if wakeup:
                observe_wakeup(work, self.config, user_message, task_id, turn_id, sender_id, conversation_history,
                               kwargs.get('model'))
            if not wakeup and (task_id or turn_id):
                require(text(task_id) and text(turn_id) and sender_id == self.config['operator_id'] and text(user_message),
                        'Native host task/turn/operator/input correlation required.', 'scope_mismatch')
            if not wakeup and text(task_id) and text(turn_id) and sender_id == self.config['operator_id'] and text(user_message):
                identity = {'assignment_id': work['assignment_id'], 'session_id': session_id,
                            'task_id': task_id, 'turn_id': turn_id}
                if native is None or any(native.get(k) != v for k, v in identity.items()):
                    require(not work.get('host_publication'), 'Resolve the existing publication before a new run.', 'publication_uncertain')
                    messages = [m for m in (conversation_history or []) if isinstance(m, dict) and
                                m.get('role') in {'user', 'assistant'} and text(m.get('content')) and
                                TAG not in m['content']]
                    observations = [{'reference': 'native-history:' + str(i), 'role': m['role'], 'content': m['content']}
                                    for i, m in enumerate(messages)]
                    observations.append({'reference': 'native-turn:' + turn_id, 'role': 'user', 'content': user_message})
                    native = {**identity, 'run_id': str(uuid.uuid4()), 'origin': copy.deepcopy(work['origin']),
                              'operator_id': sender_id, 'binding_reference': self.config['reference'],
                              'sources': copy.deepcopy(selected), 'adaptation': work['instructions'],
                              'observations': observations, 'loads': [], 'sufficient': False}
                    work['native_execution'] = native
                else:
                    require(native['observations'][-1]['content'] == user_message,
                            'Native input changed within the same correlated turn.', 'scope_mismatch')
            instructions = []
            for name in ('grill-me', 'grilling'):
                if native is None or name not in native['loads']:
                    instructions.append(self.load(name, selected, session_id))
                    if native is not None:
                        native['loads'].append(name)
            work['native_binding'] = {'assignment_id': work['assignment_id'], 'session_id': session_id,
                                      'reference': self.config['reference']}
            planning.checkpoint(database, item)
            if self.loaded.get('assignment_id') != work['assignment_id']:
                self.loaded = {'assignment_id': work['assignment_id'], 'session_id': session_id, 'sufficient': False}
            return {'context': '\n\n'.join([work['instructions'], *instructions,
                'Preserve genuine original answers, unresolved decisions and message references. '
                'When sufficient call factory_planning_sufficient, execute its to-spec instructions now, then '
                'factory_planning_publish with the three actual Markdown documents, native run_id, '
                'answers (reference/answer copied verbatim from host observations) and ordered skill work '
                '(skill/result). This HOST tool publishes and completes the immutable handoff. '
                'Never fabricate pointers or run to-tickets here.',
                'Assignment: ' + json.dumps({'assignment_id': work['assignment_id'], 'origin': work['origin'],
                    'docs_path': work['docs_path'], 'native': native}, sort_keys=True)])}

    def sufficient(self, args, *, session_id='', **kwargs):
        """Return installed to-spec to this same running native agent tool loop."""
        with connect(self.config) as database, state_lock(database):
            item, selected = self.current(database, session_id)
            from .planning_wakeup import execution_allowed
            execution_allowed(item)
            work = item['planning']
            native = work.get('native_execution')
            require((native is not None and native['session_id'] == session_id) or
                    (self.loaded.get('assignment_id') == work['assignment_id'] and self.loaded.get('session_id') == session_id),
                    'Load this interview in the same native turn first.', 'execution_required')
            require(isinstance(args, dict) and set(args) == {'assignment_id', 'rationale', 'pending_questions'} and
                    args['assignment_id'] == work['assignment_id'] and text(args['rationale']) and args['pending_questions'] == [],
                    'Unanswered decisions remain in the origin conversation.', 'planning_conflict')
            instructions = self.load('to-spec', selected, session_id)
            if native is not None:
                if 'to-spec' not in native['loads']:
                    native['loads'].append('to-spec')
                native.update(sufficient=True, rationale=args['rationale'])
                planning.checkpoint(database, item)
            self.loaded['sufficient'] = True
            return json.dumps({'instructions': instructions, 'adaptation': work['instructions'],
                               'next': 'Execute to-spec now; call factory_planning_publish with actual output.'})

    def publish(self, args, *, session_id='', **kwargs):
        """Publish actual same-origin synthesis through the exact approved HOST capability."""
        with connect(self.config) as database, state_lock(database):
            item, _ = self.current(database, session_id)
            from .planning_wakeup import execution_allowed
            execution_allowed(item)
            work = item['planning']
            native = work.get('native_execution')
            require(native is not None and native['session_id'] == session_id and native['sufficient'] is True and
                    native['loads'] == ['grill-me', 'grilling', 'to-spec'],
                    'Host-observed native interview and synthesis loads required.', 'execution_required')
            fields = {'assignment_id', 'run_id', 'documents', 'answers', 'work', 'reference'}
            require(isinstance(args, dict) and set(args) == fields and args['assignment_id'] == work['assignment_id'] and
                    args['run_id'] == native['run_id'] and text(args['reference']), 'Exact native run output required.', 'execution_required')
            answers = args['answers']
            observed = {m['reference']: m['content'] for m in native['observations'] if m['role'] == 'user'}
            require(isinstance(answers, list) and len(answers) == len(observed) and all(isinstance(a, dict) and
                    set(a) == {'reference', 'answer'} and text(a['reference']) and a['reference'] in observed and
                    a['answer'] == observed[a['reference']] for a in answers) and
                    {a['reference'] for a in answers} == set(observed),
                    'Preserve every host-observed original answer, not model-authored substitutes.', 'execution_required')
            planning.validate_documents(args['documents'])
            require(all(a['answer'] in args['documents']['provenance.md'] and a['reference'] in args['documents']['provenance.md']
                        for a in answers), 'Provenance must retain actual original answers/references.', 'execution_required')
            claims = args['work']
            require(isinstance(claims, list) and len(claims) == 3 and all(isinstance(row, dict) and
                    set(row) == {'skill', 'result'} and row['skill'] == name and text(row['result'])
                    for row, name in zip(claims, native['loads'])),
                    'Retain ordered synthesis work separately from host observations.', 'execution_required')
            require(self.publication_config is not None, 'Configure the separately approved HOST publisher.', 'approval_required')
            config = planning_publication.configuration(self.publication_config, self.config['operator_id'])
            execution = {'session_id': session_id, 'native': copy.deepcopy(native), 'answers': answers,
                         'model_work': claims, 'reference': args['reference']}

            verified_publication = None

            def guard():
                fresh, _ = self.current(database, session_id)
                execution_allowed(fresh)
                require(fresh['planning'].get('native_execution') == native and
                        planning_publication.configuration(self.publication_config, self.config['operator_id']) == config,
                        'Native execution or HOST authority changed.', 'execution_required')
                require(verified_publication is None or fresh['planning'].get('host_publication') == verified_publication,
                        'Verified HOST execution/publication was revoked or changed.', 'execution_required')

            from .repositories import GitHub
            github = GitHub(config['api_base'], config['bearer'], 10)
            record = planning_publication.publish(database, item, args['documents'], execution, github, config, guard)
            verified_publication = copy.deepcopy(record)
            guard()
            request = {'assignment_id': work['assignment_id'], 'origin': item['origin'], 'sufficient': True,
                       'rationale': native['rationale'], 'reference': args['reference'], 'pending_questions': [],
                       'documents': args['documents'], 'commit': record['commit'], 'issue_number': record['issue']['number']}
            result = planning.complete(database, item, request, github, native_execution=execution, native_guard=guard)
            return json.dumps({'status': result['planning']['status'], 'stage': result['stage'],
                               'run_id': native['run_id'], 'handoff': result['handoff']})

    def complete(self, args, *, session_id='', **kwargs):
        """Use factory_planning_publish; supplied pointers alone cannot complete native work."""
        with connect(self.config) as database, state_lock(database):
            item, _ = self.current(database, session_id)
            require(False, 'Use factory_planning_publish; loader-only or supplied pointers are not native execution.', 'execution_required')

    def register(self):
        self.ctx.register_hook('pre_llm_call', self.context)
        for name, handler in (('factory_planning_sufficient', self.sufficient), ('factory_planning_publish', self.publish),
                              ('factory_planning_complete', self.complete)):
            properties = {}
            if name == 'factory_planning_sufficient':
                properties = {'assignment_id': {'type': 'string'}, 'rationale': {'type': 'string'},
                              'pending_questions': {'type': 'array', 'items': {'type': 'string'}}}
            elif name == 'factory_planning_publish':
                properties = {'assignment_id': {'type': 'string'}, 'run_id': {'type': 'string'},
                              'documents': {'type': 'object'}, 'answers': {'type': 'array', 'items': {'type': 'object'}},
                              'work': {'type': 'array', 'items': {'type': 'object'}}, 'reference': {'type': 'string'}}
            self.ctx.register_tool(name=name, toolset='factory_planning', handler=handler,
                schema={'name': name, 'description': handler.__doc__,
                        'parameters': {'type': 'object', 'properties': properties, 'required': list(properties),
                                       'additionalProperties': name == 'factory_planning_complete'}})
        return self
