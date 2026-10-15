"""Native Hermes same-session planning tools; installing/enabling is host-authorized.

This adapter runs inside the originating agent, never a substitute worker. Native
skill_view supplies instructions to that agent's next tool-loop step. A load is
not completion: only the existing immutable publication verifier admits output.
"""
import json
from pathlib import Path

from . import assignments, planning, skills
from .planning import require, text
from .repositories import state_lock
from .service import connect


class ConversationBinding:
    """Bind one approved live native session to an existing planning assignment."""

    def __init__(self, ctx, config, github):
        fields = {'state', 'operator_id', 'project', 'iteration', 'session_id', 'origin', 'reference'}
        require(isinstance(config, dict) and set(config) == fields and
                all(text(config[k]) for k in fields - {'origin'}) and
                Path(config['state']).is_absolute() and config['state'] != ':memory:' and
                isinstance(config['origin'], dict),
                'An exact host-approved durable conversation binding is required.', 'approval_required')
        self.ctx, self.config, self.github = ctx, dict(config), github
        self.loaded = {}

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
        # dispatch_tool is the documented native tool bridge, not a model call.
        raw = self.ctx.dispatch_tool('skill_view', {'name': name}, session_id=session_id)
        try:
            result = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise ValueError('Native skill loader returned invalid JSON.') from error
        require(result.get('success') is True and result.get('name') == name and
                result.get('content') == selected[name]['instructions'],
                'Native loader must return the exact selected installed instructions.', 'skill_blocked')
        return result['content']

    def context(self, session_id, **kwargs):
        """Native pre_llm_call: bind actual origin turns without starting an agent."""
        if session_id != self.config['session_id']:
            return None
        with connect(self.config) as database, state_lock(database):
            item, selected = self.current(database, session_id)
            work = item['planning']
            if work['status'] == 'complete':
                return None
            instructions = [self.load(name, selected, session_id) for name in ('grill-me', 'grilling')]
            self.loaded = {'assignment_id': work['assignment_id'], 'session_id': session_id, 'sufficient': False}
            return {'context': '\n\n'.join([work['instructions'], *instructions,
                'This is the existing originating interview. Preserve genuine user answers and message references. '
                'When clarification is sufficient call factory_planning_sufficient; continue immediately with its '
                'installed to-spec instructions, without another approval or asking the operator to advance stages. '
                'After controlled HOST publication call factory_planning_complete with the exact existing '
                'complete-planning request. Never fabricate publication pointers or execute to-tickets here.',
                'Assignment: ' + json.dumps({'assignment_id': work['assignment_id'], 'origin': work['origin'],
                                             'docs_path': work['docs_path']}, sort_keys=True)])}

    def sufficient(self, args, *, session_id='', **kwargs):
        """Return installed to-spec to the same running native agent tool loop."""
        with connect(self.config) as database, state_lock(database):
            item, selected = self.current(database, session_id)
            work = item['planning']
            require(self.loaded.get('assignment_id') == work['assignment_id'] and
                    self.loaded.get('session_id') == session_id,
                    'Load this interview in the same native turn first.', 'execution_required')
            require(isinstance(args, dict) and set(args) == {'assignment_id', 'rationale', 'pending_questions'} and
                    args['assignment_id'] == work['assignment_id'] and text(args['rationale']) and
                    args['pending_questions'] == [],
                    'Unanswered decisions remain in the origin conversation.', 'planning_conflict')
            instructions = self.load('to-spec', selected, session_id)
            self.loaded['sufficient'] = True
            return json.dumps({'instructions': instructions, 'adaptation': work['instructions'],
                               'next': 'Execute to-spec now using the genuine conversation; HOST publication remains required.'})

    def complete(self, args, *, session_id='', **kwargs):
        with connect(self.config) as database, state_lock(database):
            item, _ = self.current(database, session_id)
            require(self.loaded == {'assignment_id': item['planning']['assignment_id'],
                                    'session_id': session_id, 'sufficient': True},
                    'Execute the installed to-spec handoff in this native turn first.', 'execution_required')
            require(isinstance(args, dict) and args.get('sufficient') is True,
                    'Only synthesized sufficient output can complete planning.', 'invalid_planning')
            result = planning.complete(database, item, args, self.github)
            return json.dumps({'status': result['planning']['status'], 'stage': result['stage']})

    def register(self):
        self.ctx.register_hook('pre_llm_call', self.context)
        for name, handler, properties, required in (
            ('factory_planning_sufficient', self.sufficient,
             {'assignment_id': {'type': 'string'}, 'rationale': {'type': 'string'},
              'pending_questions': {'type': 'array', 'items': {'type': 'string'}}},
             ['assignment_id', 'rationale', 'pending_questions']),
            ('factory_planning_complete', self.complete, {}, []),
        ):
            self.ctx.register_tool(name=name, toolset='factory_planning', handler=handler,
                schema={'name': name, 'description': handler.__doc__ or 'Verify actual immutable planning publication.',
                        'parameters': {'type': 'object', 'properties': properties, 'required': required,
                                       'additionalProperties': name == 'factory_planning_complete'}})
        return self
