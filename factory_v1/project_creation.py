"""Explicit private new-product creation, minimal baseline, then ordinary T2 attachment."""
import base64
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3

from . import controls, repositories
from .__main__ import read_iteration, register_iteration, validate_intake
from .assignments import commit
from .errors import IntakeError
from .planning import require, text
from .sandbox_source import physical
from .service import atomic, connect, isolated_root

INITIAL_STANDARDS = ('# Product engineering standards\n\n'
    '- Implement only the agreed product scope.\n'
    '- Use focused, deterministic unit/component tests with external boundaries mocked.\n'
    '- Keep credentials and private controller configuration out of product source.\n'
    '- Require explicit human approval for merge and production actions.\n')


class ProjectCreation:
    """Host-owned creation authority; no credentials or internal IDs come from chat."""

    def __init__(self, setup, config):
        require(isinstance(config, dict) and set(config) == {'root', 'state', 'account', 'reference'} and
                text(config['account']) and text(config['reference']),
                'Reviewed private creation configuration required.', 'approval_required')
        self.setup, self.enrollment, self.config = setup, setup.enrollment, copy.deepcopy(config)
        self.root = physical(config['root'], directory=True)
        controls.private(self.root, directory=True)
        isolated_root(self.root)
        state = Path(config['state'])
        require(state.is_absolute() and state.parent == self.root and state != Path(setup.config['state']) and
                not any(self.root.is_relative_to(p) or p.is_relative_to(self.root) for p in
                        (setup.root, self.enrollment.store.root, setup.workspaces,
                         Path(setup.config['state']).parent, Path(setup.config['capability_config']).parent,
                         Path(__file__).resolve().parent.parent, Path.home() / '.hermes/profiles')),
                'Separate private creation journal/state required.', 'workspace_conflict')

    def path(self, draft_id):
        self.enrollment.store.path(draft_id)
        return self.root / (draft_id + '.json')

    def read(self, draft_id):
        """Retained intent, verified identities and blockers; never host credentials."""
        path = self.path(draft_id)
        if not path.exists():
            return None
        controls.private(path)
        record = json.loads(path.read_text())
        require(record['draft_id'] == draft_id, 'Creation scope changed.', 'scope_mismatch')
        return record

    def save(self, record):
        atomic(self.path(record['draft_id']), record)

    def guard_attachment(self, draft_id):
        existing = self.setup.read(draft_id)
        if existing is not None and Path(self.setup.config['state']).exists():
            with connect(self.setup.config) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='iterations'").fetchone() is None:
                    return
                item = read_iteration(db, existing['project'], existing['iteration'])
                if item is not None:
                    self.setup.guard(db, item)

    def create(self, draft_id, expected_revision, repository, *, session_id):
        """Create only the explicitly requested approved target, initialize, and return T2 setup."""
        require(repositories.valid_repository(repository) and type(expected_revision) is int and expected_revision >= 0 and text(session_id),
                'Exact new repository, revision and native session required.', 'scope_mismatch')
        with self.enrollment.store.locked():
            draft = self.enrollment.store.read(draft_id)
            require(draft is not None and draft['revision'] == expected_revision and draft['sufficient'] and
                    not draft['pending_questions'] and draft.get('native', {}).get('session_id') == session_id,
                    'Current sufficient native project interview required.', 'planning_conflict')
            assert draft is not None
            record = self.read(draft_id)
            if record is not None and (record['repository'] != repository or record['draft_revision'] != expected_revision):
                product = draft['product']
                selected = product.get('repository') or self.config['account'] + '/' + re.sub(
                    r'[^a-z0-9._-]+', '-', product['name'].lower()).strip('-')
                require(record.get('no_creation_effect') is True and expected_revision > record['draft_revision'] and
                        repository != record['repository'] and product.get('repository_mode', 'new') == 'new' and
                        selected == repository and not draft.get('transfer') and self.setup.read(draft_id) is None and
                        not any(k in record for k in ('repository_id', 'baseline_intent', 'baseline', 'standards_intent')),
                        'Reconcile existing creation effects before changing target.', 'scope_mismatch')
                self.setup.capability(draft, repository)  # Fresh exact-target approval, never inherited.
                with connect(self.config) as db:
                    prior = read_iteration(db, record['project'], record['iteration'])
                    require(prior is not None and not prior.get('repository_onboarding'),
                            'Creation intent/effect is not known absent.', 'scope_mismatch')
                history = record.get('history', []) + [{k: v for k, v in record.items() if k != 'history'}]
                record = None
            else:
                history = []
            if record is None:
                record = {'version': 1, 'draft_id': draft_id, 'draft_revision': expected_revision,
                          'repository': repository, 'project': 'creation-' + draft_id, 'iteration': 'm1',
                          'marker': 'forge:new-product:' + draft_id + ':' + str(expected_revision),
                          'status': 'pending', 'blocker': None}
                if history:
                    record['history'] = history
                    record['iteration'] = 'm1-r' + str(expected_revision)
                self.save(record)
            require(record['repository'] == repository and record['draft_revision'] == expected_revision,
                    'Creation already targets another repository/revision.', 'scope_mismatch')
            try:
                capability = self.setup.capability(draft, repository)
                require(repository.split('/')[0] == self.config['account'] and capability['reference'] == self.config['reference'],
                        'Exact reviewed account and creation/baseline approval required.', 'approval_required')
                self.guard_attachment(draft_id)
                self.prepare(draft, record, capability)
                attachment = self.setup.read(draft_id)
                if attachment is None:
                    attachment = {'version': 1, 'draft_id': draft_id, 'draft_revision': expected_revision,
                                  'repository': repository, 'project': 'project-' + draft_id, 'iteration': 'm1',
                                  'workspace': str(self.setup.workspaces / draft_id), 'repository_id': record['repository_id'],
                                  'commit': record['baseline']['commit'], 'status': 'pending', 'blocker': None}
                    self.setup.save(attachment)
                require(attachment['repository'] == repository and attachment['draft_revision'] == expected_revision and
                        attachment.get('repository_id') == record['repository_id'] and attachment.get('commit') == record['baseline']['commit'],
                        'Existing attachment conflicts with created repository/baseline.', 'scope_mismatch')
            except (repositories.RepositoryError, IntakeError, OSError, ValueError, sqlite3.Error) as error:
                record.update(status='blocked', blocker={'code': getattr(error, 'code', 'creation_unavailable'),
                    'operation': 'create-private', 'consequence': 'Creation progress retained; no worker dispatched.',
                    'next_decision': 'For a known naming conflict, answer with another product name in this conversation; new target approval is still required. Otherwise restore the exact prerequisite or reconcile the retained effect.'})
                self.save(record)
                return copy.deepcopy(record)
        result = self.setup.attach(draft_id, expected_revision, repository, session_id=session_id)
        with self.enrollment.store.locked():
            record.update(status=result['status'], blocker=result['blocker'])
            self.save(record)
        return result

    def prepare(self, draft, record, capability):
        state = Path(self.config['state'])
        if not state.exists():
            fd = os.open(state, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        controls.private(physical(state))
        origin = {k: draft['origin'][k] for k in ('platform', 'chat_id', 'thread_id', 'parent_chat_id', 'scope_id')}
        request = {'project_id': record['project'], 'iteration_id': record['iteration'], 'repository': record['repository'],
                   'repository_intent': {'mode': 'new', 'marker': record['marker']},
                   'idea': draft['observations'][0]['text'], 'origin': origin,
                   'approval': {'operator_id': draft['operator_id'], 'reference': capability['reference']}}
        validate_intake(request, draft['operator_id'])
        github = repositories.GitHub(capability['api_base'], capability['bearer'], 10)
        with connect(self.config) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS iterations (project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
                item = read_iteration(db, record['project'], record['iteration'])
                if item is None:
                    if record.get('history'):
                        previous = record['history'][-1]
                        prior = read_iteration(db, previous['project'], previous['iteration'])
                        require(prior is not None and not prior.get('repository_onboarding'),
                                'Prior creation outcome changed.', 'scope_mismatch')
                        assert prior is not None
                        if prior.get('status') == 'superseded-no-effect':
                            require(prior.get('superseded_by') == record['marker'],
                                    'Predecessor retired for another attempt.', 'scope_mismatch')
                            controls.require_active(db, prior['project_id'], prior['iteration_id'])
                        else:
                            self.setup.guard(db, prior)
                            prior.update(status='superseded-no-effect', superseded_by=record['marker'])
                            repositories.save_iteration(db, prior)
                            db.commit()  # Retain retirement across death before new intake registration.
                    item = register_iteration(db, copy.deepcopy(request))
                else:
                    require(all(item.get(k) == v for k, v in request.items()),
                            'Creation intake scope changed.', 'scope_mismatch')
            self.setup.guard(db, item)
            try:
                item = repositories.onboard(db, item, github)
            except repositories.RepositoryError as error:
                if error.code == 'repository_collision' and not item.get('repository_onboarding'):
                    record['no_creation_effect'] = True
                    self.save(record)
                raise
            metadata = item['repository_onboarding']['metadata']
            require(metadata['private'] is True and record.get('repository_id', metadata['id']) == metadata['id'],
                    'Created private repository identity changed.', 'repository_mismatch')
            record['repository_id'] = metadata['id']
            self.save(record)
            self.setup.guard(db, item)
            self.baseline(record, draft, github, lambda: (self.setup.guard(db, item), self.guard_attachment(draft['draft_id'])))

    def readme_baseline(self, record, draft, github, guard):
        """Only create README on a verified empty target; uncertain writes are read-only reconciled."""
        content = ('# ' + draft['product']['name'] + '\n\n' + draft['observations'][0]['text'] + '\n').encode()
        blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content, usedforsecurity=False).hexdigest()
        intent = {'path': 'README.md', 'blob': blob, 'message': 'Initialize product baseline [' + record['marker'] + ']'}
        require(record.get('baseline_intent', intent) == intent, 'Agreed baseline changed.', 'scope_mismatch')
        base = '/repos/' + record['repository']
        if 'baseline_intent' not in record:
            require(github.request(base + '/branches?per_page=1') == [] and github.request(base + '/contents/README.md') is None,
                    'New repository is not empty; do not overwrite or adopt its contents.', 'baseline_conflict')
            guard()
            record['baseline_intent'] = intent
            self.save(record)
            response = github.request(base + '/contents/README.md',
                {'message': intent['message'], 'content': base64.b64encode(content).decode()}, method='PUT')
            require(isinstance(response, dict) and isinstance(response.get('commit'), dict) and
                    commit(response['commit'].get('sha')) and isinstance(response.get('content'), dict) and response['content'].get('sha') == blob,
                    'Initial commit response uncertain; reconcile without rewriting.', 'baseline_uncertain')
            record['baseline_returned_commit'] = response['commit']['sha']
            self.save(record)
        head = github.request(base + '/commits/' + record.get('readme_baseline', {}).get('commit', 'HEAD'))
        require(isinstance(head, dict) and commit(head.get('sha')) and head.get('parents') == [] and
                head.get('commit', {}).get('message') == intent['message'] and
                [(f.get('filename'), f.get('sha'), f.get('status')) for f in head.get('files', [])] == [('README.md', blob, 'added')] and
                record.get('baseline_returned_commit', head['sha']) == head['sha'] and
                record.get('readme_baseline', {}).get('commit', head['sha']) == head['sha'],
                'Initial commit identity/content uncertain; reconciliation required.', 'baseline_uncertain')
        file = github.request(base + '/contents/README.md?ref=' + head['sha'])
        require(isinstance(file, dict) and file.get('type') == 'file' and file.get('path') == 'README.md' and
                file.get('sha') == blob and file.get('encoding') == 'base64' and
                base64.b64decode(file.get('content', '')) == content,
                'Initial baseline readback differs.', 'baseline_uncertain')
        record['readme_baseline'] = {'commit': head['sha'], 'path': 'README.md', 'blob': blob}
        self.save(record)

    def baseline(self, record, draft, github, guard):
        """Initialize README then product-local standards with separately retained intents."""
        self.readme_baseline(record, draft, github, guard)
        parent = record['readme_baseline']['commit']
        content = INITIAL_STANDARDS.encode()
        blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content, usedforsecurity=False).hexdigest()
        intent = {'path': 'AGENTS.md', 'blob': blob, 'parent': parent,
                  'message': 'Initialize product standards [' + record['marker'] + ']'}
        require(record.get('standards_intent', intent) == intent, 'Standards intent changed.', 'scope_mismatch')
        base = '/repos/' + record['repository']
        if 'standards_intent' not in record:
            head = github.request(base + '/commits/HEAD')
            require(isinstance(head, dict) and head.get('sha') == parent and github.request(base + '/contents/AGENTS.md') is None,
                    'Do not overwrite or adopt another baseline.', 'baseline_conflict')
            guard()
            record['standards_intent'] = intent
            self.save(record)
            response = github.request(base + '/contents/AGENTS.md',
                {'message': intent['message'], 'content': base64.b64encode(content).decode()}, method='PUT')
            require(isinstance(response, dict) and commit(response.get('commit', {}).get('sha')) and
                    response.get('content', {}).get('sha') == blob,
                    'Standards write uncertain; reconcile without retry.', 'baseline_uncertain')
            record['standards_returned_commit'] = response['commit']['sha']
            self.save(record)
        head = github.request(base + '/commits/HEAD')
        require(isinstance(head, dict) and commit(head.get('sha')) and
                [p.get('sha') for p in head.get('parents', [])] == [parent] and
                head.get('commit', {}).get('message') == intent['message'] and
                [(f.get('filename'), f.get('sha'), f.get('status')) for f in head.get('files', [])] == [('AGENTS.md', blob, 'added')] and
                record.get('standards_returned_commit', head['sha']) == head['sha'] and
                record.get('baseline', {}).get('commit', head['sha']) == head['sha'],
                'Standards commit differs; reconcile exact effect.', 'baseline_uncertain')
        file = github.request(base + '/contents/AGENTS.md?ref=' + head['sha'])
        require(isinstance(file, dict) and file.get('type') == 'file' and file.get('path') == 'AGENTS.md' and
                file.get('sha') == blob and file.get('encoding') == 'base64' and
                base64.b64decode(file.get('content', '')) == content,
                'Standards readback differs.', 'baseline_uncertain')
        record['baseline'] = {'commit': head['sha'], 'path': 'AGENTS.md', 'blob': blob}
        self.save(record)

    def tool(self, args, *, session_id='', **kwargs):
        """Create the explicitly selected NEW private repository with README and product-local standards."""
        require(isinstance(args, dict) and set(args) == {'repository', 'expected_revision'},
                'Exact new repository choice and revision required.', 'scope_mismatch')
        draft, observed = self.enrollment.correlated(session_id)
        require(draft.get('native', {}).get('message_id') == observed['message_id'], 'Current native interview turn required.', 'scope_mismatch')
        return json.dumps(self.create(draft['draft_id'], args['expected_revision'], args['repository'], session_id=session_id), sort_keys=True)

    def register(self):
        """Explicit host registration seam; returns the creator for T7 composition."""
        properties = {'repository': {'type': 'string'}, 'expected_revision': {'type': 'integer'}}
        self.enrollment.ctx.register_tool(name='factory_project_create', toolset='factory_planning', handler=self.tool,
            schema={'name': 'factory_project_create', 'description': self.tool.__doc__, 'parameters': {
                'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}})
        return self
