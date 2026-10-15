"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from factory_v1.tests import test_tickets

SOURCE_ROOT = Path(os.environ.get('FACTORY_ROLE_SKILL_FIXTURES', str(Path(__file__).parent / 'role_skill_snapshots')))
PINS = {
    'implement': '136a8987143241a0229952a56d9c41042646073769fcad466d808bd5e40bb4f0',
    'tdd': '93ea419b76e9caaf26153b828e984f7c3fb136f4caa67b14af95f32ea965a1cc',
    'codebase-design': '2c20617f87ec8af6a434859f381b2f061a69b530444e74eb39e78bb016a6d1e2',
    'code-review': '1ff8f642af17fe58110d991738b8d881cb8ae3a1b5bc6b1abddc380f8b26212e',
    'code-review/references/structured-text-contracts.md': '3f38253626d8b1a828d2511e2dfe1ac6937a5a25544138c0530ddfa0031b02a8',
    'simplify-code': '9ee977c3362a1c07f2e327198e066ddb3c0ed67709706fc410165ee820b2ca01',
    'diagnosing-bugs': '9168404abda0967a5d32977e3498cd95fda6807018852f3de736a78357c82b40',
    'tdd/tests.md': '859f9e592c188fda4fc7277dd180e4ce9c7a2e13f6efe1f6f29eccc9d28c106a',
    'tdd/mocking.md': '3ceb807fdf4a47d6a93d4d9a891e5ba6d362a6247bd08adc451feebfc17361ef',
}
ENGINEERING = ['implement']
REVIEW = ['code-review', 'code-review/references/structured-text-contracts.md']


def artifact(name, content):
    return {'name': name, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}


class AssignmentTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((SOURCE_ROOT / 'implement/SKILL.md').is_file(), 'Actual selected immutable skill test snapshots required.')
        self.tracker = test_tickets.TicketTests()
        self.tracker.setUp()
        self.addCleanup(self.tracker.doCleanups)
        self.item = self.tracker.publish()
        self.fixture = self.tracker.fixture
        self.root = self.fixture.root
        self.skills = self.root / 'selected-inputs'
        self.skills.mkdir()
        # Copy only selected bytes into disposable test inputs; never alter installed skills.
        for name in PINS:
            source = SOURCE_ROOT / (name if name.endswith('.md') else name + '/SKILL.md')
            target = self.skills / (name if name.endswith('.md') else name + '/SKILL.md')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        self.grant()
        self.request = self.configuration()
        self.fixture.calls.clear()

    def grant(self):
        response = self.fixture.cli('spend-grant', '--project', 'product', '--iteration', 'm1',
            '--provider', 'openai-codex', '--model', 'gpt-6.1-sol', '--operation', 'responses',
            '--kind', 'allowance', '--ceiling', '10', '--expires', str(int(time.time()) + 600), '--reference', 'fixture-approved-subscription')
        self.assertEqual(response.returncode, 0, response.stderr)

    def descriptors(self, names):
        result = []
        for name in names:
            suffix = name if name.endswith('.md') else name + '/SKILL.md'
            source_suffix = 'software-development/' + suffix if name == 'simplify-code' else suffix
            result.append({'name': name, 'source': '/home/ops/.hermes/skills/' + source_suffix,
                           'path': str(self.skills / suffix), 'sha256': PINS[name],
                           'dependencies': {'tdd': ['codebase-design', 'tdd/tests.md', 'tdd/mocking.md'],
                                            'code-review': ['code-review/references/structured-text-contracts.md']}.get(name, [])})
        return result

    def configuration(self, number=10, stage='implementation'):
        names, role, preceding = {
            'implementation': (ENGINEERING, 'implementation', []),
            'corrections': (ENGINEERING, 'implementation', ['candidate', 'findings']),
            'simplify': (['simplify-code'], 'simplification', ['candidate', 'implementation-evidence']),
            'review-standards': (REVIEW, 'review', ['candidate', 'simplification-evidence']),
            'review-spec': (REVIEW, 'review', ['candidate', 'simplification-evidence']),
            'diagnosis': (['diagnosing-bugs'], 'debug', ['failure-evidence']),
            'repair': (ENGINEERING + ['diagnosing-bugs'], 'repair', ['incident-evidence']),
        }[stage]
        issue = self.tracker.issues[number]
        candidate = 'b' * 40 if 'candidate' in preceding else None
        capabilities = ['read_workspace', 'scratch', 'model']
        if stage not in {'review-standards', 'review-spec', 'diagnosis'}:
            capabilities.append('write_workspace')
        return {'stage': stage, 'repository': 'example/product', 'repository_id': 123,
            'workspace': str(self.root / 'assigned-workspace'),
            'issue': {'number': number, 'id': issue['id'], 'node_id': issue['node_id'],
                      'body_sha256': hashlib.sha256(issue['body'].encode()).hexdigest()},
            'spec_commit': self.item['handoff']['commit'], 'baseline': 'a' * 40, 'candidate': candidate,
            'standards': [artifact('AGENTS.md', 'Fixture standards: meaningful tests; no direct publication.')],
            'preceding': [artifact(name, candidate if name == 'candidate' else 'Labeled preceding-stage fixture evidence') for name in preceding],
            'profile': {'name': 'factory-' + stage, 'home': str(self.root / ('profile-' + stage)), 'role': role,
                        'provider': 'openai-codex', 'model': 'gpt-6.1-sol', 'operation': 'responses', 'reasoning': 'high'},
            'skills': self.descriptors(names), 'capabilities': capabilities,
            'result_contract': 'factory-bounded-result-v1', 'claim_id': 'fixture-claim-' + stage}

    def command(self, command='prepare-assignment', request=None, assignment=None, dry_run=False, extra=()):
        args = [command, '--project', 'product', '--iteration', 'm1']
        if command in {'prepare-assignment', 'assignment-result'}:
            path = self.root / ('request-' + str(time.monotonic_ns()) + '.json')
            path.write_text(json.dumps(self.request if request is None else request))
            args += ['--request', str(path), '--api-base', self.fixture.base]
        if assignment:
            args += ['--assignment', assignment]
        if dry_run:
            args.append('--dry-run')
        return self.fixture.cli(*args, *extra)

    def ok(self, response):
        self.assertEqual(response.returncode, 0, response.stderr)
        return json.loads(response.stdout)

    def refused(self, response, code):
        self.assertEqual(response.returncode, 2, response.stdout + response.stderr)
        self.assertEqual(json.loads(response.stderr)['error'], code)

    def inspect(self, assignment):
        return self.command('inspect-assignment', assignment=assignment['assignment_id'])

    def result(self, assignment):
        return {'assignment_id': assignment['assignment_id'], 'handoff_digest': assignment['handoff_digest'],
            'claim_id': assignment['claim_id'], 'run_id': 'fixture-worker-run', 'status': 'done',
            'loads': ([{'source': s['source'], 'sha256': s['sha256'], 'tool_reference': 'fixture:read-selected-bytes'} for s in assignment['handoff']['skills']] +
                      [{'source': source, 'sha256': pin, 'tool_reference': 'fixture:read-pinned-input'} for source, pin in assignment['handoff']['input_loads'].items()]),
            'work': ['Labeled fixture exercises meaningful public CLI tests; not real worker/model execution.'],
            'artifacts': [artifact('stage-evidence', 'Labeled deterministic fixture, no isolation or live delivery proof.')],
            'tests': [{'command': 'fixture-test-command', 'result': 'Labeled deterministic pass, not live worker evidence'}]}

    def mutate_iteration(self, change):
        # Fault injection at persistence boundary, never the verification seam.
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            item = json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])
            change(item)
            database.execute('UPDATE iterations SET payload=?', (json.dumps(item),))

    def assert_no_external_writes(self):
        mutations = [c for c in self.fixture.calls if c[0] != 'GET' and
                     not (c[1] == '/graphql' and c[2]['query'].lstrip().startswith('query '))]
        self.assertEqual(mutations, [])

from unittest.mock import Mock, patch
from factory_v1 import assignments, attention, controls, spending, requirements
from factory_v1.repositories import RepositoryError


class AssignmentComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                             'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
            'status': 'active', 'approval': {'operator_id': '42', 'reference': 'local component approval'},
            'execution_allowed': False, 'handoff': {'commit': 'c' * 40},
            'repository_onboarding': {'status': 'verified', 'metadata': {'id': 123}},
            'ticket_work': {'assignment_id': 'ticket-input', 'input_digest': 'd' * 64,
                            'inputs': {'documents': {}}, 'board': {}, 'publication_complete': True}}
        self.save_item()
        spending.initialize(self.db)
        spending.add_grant(self.db, 'product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses',
                           'allowance', 10, 2**62, 'local fixture allowance', now=1)
        self.db.commit()
        self.request = self.configuration()
        self.github = Mock(base='https://api.github.com')
        # External tracker reads only: no HTTP, CLI or synthesis precursor.
        self.frontier = self.enterContext(patch.object(assignments.tickets, 'frontier', side_effect=self.observation))
        self.read_issue = self.enterContext(patch.object(assignments.tickets.gh, 'read_issue',
            return_value={'body': 'Local issue', 'html_url': 'https://example.invalid/issues/10'}))

    def save_item(self):
        self.db.execute('DELETE FROM iterations')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
        self.db.commit()

    def load_item(self, database=None):
        database = self.db if database is None else database
        return json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])

    def assert_refused(self, code, operation, *args, **kwargs):
        with self.assertRaises(RepositoryError) as refusal:
            operation(*args, **kwargs)
        self.assertEqual(refusal.exception.code, code)

    def copied_skills(self, request):
        request = copy.deepcopy(request)
        for selected in request['skills']:
            source = Path(selected['path'])
            target = self.root / 'selected-inputs' / selected['name']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            selected['path'] = str(target)
        return request

    def configuration(self, stage='implementation'):
        names = {'implementation': ENGINEERING, 'corrections': ENGINEERING, 'simplify': ['simplify-code'],
                 'review-standards': REVIEW, 'review-spec': REVIEW, 'diagnosis': ['diagnosing-bugs'],
                 'repair': ENGINEERING + ['diagnosing-bugs']}[stage]
        selected = []
        for name in names:
            suffix = name if name.endswith('.md') else name + '/SKILL.md'
            selected.append({'name': name, 'source': '/home/ops/.hermes/skills/' +
                ('software-development/' if name == 'simplify-code' else '') + suffix,
                'path': str(SOURCE_ROOT / suffix), 'sha256': PINS[name],
                'dependencies': [REVIEW[1]] if name == 'code-review' else []})
        candidate = 'b' * 40 if stage in {'corrections', 'simplify', 'review-standards', 'review-spec'} else None
        return {'stage': stage, 'repository': 'example/product', 'repository_id': 123,
            'workspace': str(self.root / 'workspace'), 'issue': {'number': 10, 'id': 100,
                'node_id': 'ISSUE10', 'body_sha256': hashlib.sha256(b'Local issue').hexdigest()},
            'spec_commit': 'c' * 40, 'baseline': 'a' * 40, 'candidate': candidate,
            'standards': [artifact('AGENTS.md', 'Local immutable standards')],
            'preceding': [artifact(n, candidate if n == 'candidate' else 'Local evidence')
                          for n in sorted(assignments.PRECEDING[stage])],
            'profile': {'name': 'component-' + stage, 'home': str(self.root / ('profile-' + stage)),
                'role': assignments.ROLES[stage], 'provider': 'openai-codex', 'model': 'gpt-6.1-sol',
                'operation': 'responses', 'reasoning': 'high'}, 'skills': selected,
            'capabilities': ['read_workspace', 'scratch', 'model'] +
                ([] if stage in {'review-standards', 'review-spec', 'diagnosis'} else ['write_workspace']),
            'result_contract': assignments.CONTRACT, 'claim_id': 'component-' + stage}

    def observation(self, db, item, github):
        observed = copy.deepcopy(item)
        observed['ticket_work']['frontier'] = {'foreign_active': [], 'active': [],
            'items': [{'number': 10, 'id': 100, 'node_id': 'ISSUE10', 'admissible': True}]}
        return observed

    def prepare(self, request=None, dry=False):
        return assignments.prepare(self.db, 'product', 'm1', '42', request or self.request, self.github, dry)

    def seed_assignment(self, request=None, issue=None):
        """Persist normalized claim input, without exercising claim admission."""
        issue = issue or {'body': 'Local issue', 'html_url': 'https://example.invalid/issues/10'}
        request = copy.deepcopy(request or self.request)
        selected = assignments.validate(request)
        request['skills'] = [{k: v for k, v in s.items() if k != 'instructions'} for s in selected]
        scope = {'project': 'product', 'iteration': 'm1', 'repository': request['repository'],
                 'repository_id': request['repository_id'], 'issue_number': request['issue']['number'],
                 'issue_id': request['issue']['id'], 'workspace': request['workspace']}
        inputs = requirements.inputs(self.item, scope['issue_number'])
        loads = {issue['html_url']: request['issue']['body_sha256']}
        loads.update({d['url']: d['sha256'] for d in inputs['documents'].values()})
        for group in ('standards', 'preceding'):
            loads.update({'handoff:' + group + ':' + a['name']: a['sha256'] for a in request[group]})
        from factory_v1 import role_skills
        support = {'required', 'repository-inputs', 'implement-review'}
        handoff = {**request, 'skills': selected, 'issue_body': issue['body'],
                   'specification': inputs, 'input_loads': loads,
                   'adaptation': role_skills.ADAPTATIONS[request['stage']],
                   'stage_rules': role_skills.STAGE_RULES[request['stage']],
                   'support_policy': {k: role_skills.SUPPORT_POLICY[k] for k in sorted(support)},
                   'skill_entry_points': list(role_skills.STAGES[request['stage']]),
                   'tracker': {'api_base': self.github.base, 'board': self.item['ticket_work']['board'],
                               'issue_url': issue['html_url']},
                   'fresh_context': True, 'inherited_memory': False, 'inherited_sessions': False,
                   'credentials': [], 'model_policy': {'scope': ['product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses'],
                       'reservation': 1, 'recheck_at_call': True},
                   'result_requirements': {'identity': ['assignment_id', 'handoff_digest', 'claim_id', 'run_id'],
                       'required_evidence': ['actual-selected-instruction-loads', 'observable-skill-guided-work',
                           'artifacts', 'tests', 'trusted-whole-process-execution'],
                       'advancement': 'disabled-without-trusted-whole-process-evidence'}}
        row = {'assignment_id': assignments.digest({'request': request, 'scope': scope}),
               'claim_id': request['claim_id'], 'ticket_scope': scope, 'handoff': handoff,
               'handoff_digest': assignments.digest(handoff), 'revision': requirements.authority(self.item, scope['issue_number']),
               'assignment_ready': True, 'launchable': False, 'execution_allowed': False,
               'status': 'prepared', 'refusal': 'whole_process_isolation_unavailable'}
        self.db.execute('CREATE TABLE IF NOT EXISTS role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.db.commit()
        return row

    def result(self, row):
        expected = {s['source']: s['sha256'] for s in row['handoff']['skills']} | row['handoff']['input_loads']
        return {'assignment_id': row['assignment_id'], 'handoff_digest': row['handoff_digest'],
            'claim_id': row['claim_id'], 'run_id': 'component-run', 'status': 'done',
            'loads': [{'source': s, 'sha256': p, 'tool_reference': 'local read fixture'} for s, p in expected.items()],
            'work': ['Local evidence, not native execution'], 'artifacts': [artifact('stage-evidence', 'Local input')],
            'tests': [{'command': 'local check', 'result': 'Local input, not executed check'}]}

    def store(self, row, result):
        return assignments.store_result(self.db, 'product', 'm1', '42', row['assignment_id'], result, self.github)

    def test_real_claim_dry_replay_restart_and_repair_order(self):
        before = self.db.total_changes
        dry = self.prepare(dry=True)
        self.assertEqual(self.prepare(dry=True), dry)
        self.assertEqual(self.db.total_changes, before)
        self.assertFalse(dry['claim_persisted'])
        self.assert_refused('not_found', assignments.inspect, self.db, 'product', 'm1', '42', dry['assignment_id'])
        row = self.prepare()
        self.assertEqual(row['assignment_id'], dry['assignment_id'])
        self.assertEqual(self.prepare()['handoff_digest'], row['handoff_digest'])
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            self.assertEqual(assignments.inspect(restarted, 'product', 'm1', '42', row['assignment_id'])['handoff'], row['handoff'])
        self.assertFalse(row['execution_allowed'])
        self.assertTrue(row['assignment_ready'])
        self.assertTrue(row['claim_persisted'])
        self.assertFalse(row['launchable'])
        self.assertFalse(row['spending_admission']['reserved'])
        self.assertEqual(row['handoff']['credentials'], [])
        self.assertFalse(row['handoff']['inherited_memory'])
        request = self.configuration('repair')
        # Labeled registered-scope input; this test qualifies claims/order, not host registration.
        from factory_v1 import recovery
        config = {'operator_id': '42', 'reference': 'LABELED registered authority fixture',
                  'incident_id': 'local-incident', 'issue': request['issue'], 'paths': ['app.py'],
                  'removable_assignments': [], 'evidence_id': 'e' * 64}
        record = {'config': config, 'evidence': {'main': request['baseline']}, 'assignment': None,
                  'status': 'registered', 'parked': [row['assignment_id']], 'removable': {}}
        self.item.update(main_recoveries={'local-incident': record}, delivery_monitor={'config': {}},
            main_incident={'incident_id': 'local-incident', 'status': 'active', 'monitor_sha256': assignments.digest({}),
                'first_evidence': 'e' * 64, 'latest_evidence': 'e' * 64,
                'recovery_assignment': assignments.digest(config), 'recovery_issue': 10})
        self.save_item()
        with self.db:
            self.db.execute('CREATE TABLE delivery_incidents (project TEXT, iteration TEXT, id TEXT, payload TEXT)')
            self.db.execute('INSERT INTO delivery_incidents VALUES (?,?,?,?)',
                ('product', 'm1', 'local-incident', json.dumps(self.item['main_incident'])))
        request['preceding'] = recovery.preceding(record)
        first = self.prepare(request, dry=True)
        request['skills'].reverse()
        self.assertEqual(self.prepare(request, dry=True)['assignment_id'], first['assignment_id'])
        self.assertEqual(first['handoff']['skill_entry_points'], ['diagnosing-bugs', 'implement'])
        # Repair owns a fresh profile but shares the same eligible ticket.
        # Independent restart inventory is a mocked precursor here; the real
        # registered-health/authority boundary has dedicated integration coverage.
        with patch('factory_v1.restart.before_dispatch'):
            repaired = self.prepare(request)
            replay = self.prepare({**request, 'skills': list(reversed(request['skills']))})
        self.assertEqual(replay['assignment_id'], repaired['assignment_id'])
        self.assertEqual(repaired['assignment_id'], first['assignment_id'])
        self.assertEqual(replay['handoff_digest'], repaired['handoff_digest'])
        self.assertEqual(len(assignments.rows(self.db)), 2)
        self.assertEqual([s['name'] for s in replay['handoff']['skills']], ['diagnosing-bugs', 'implement'])
        self.github.request.assert_not_called()

    def test_malformed_request_and_path_pre_read_guards_matrix(self):
        assignments.validate(self.request)
        for field, value, code in [('workspace', 'relative', 'invalid_assignment'),
            ('workspace', '/workspace/../other', 'invalid_assignment'),
            ('baseline', 'main', 'invalid_assignment'), ('repository_id', True, 'invalid_assignment'),
            ('capabilities', ['merge'], 'capability_blocked'), ('standards', [], 'invalid_assignment'),
            ('result_contract', 'success-is-authority', 'capability_blocked'),
            ('memory', 'ambient-session', 'invalid_assignment'), ('stage', 'unknown', 'invalid_assignment')]:
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.prepare({**self.request, field: value})
                self.assertEqual(refusal.exception.code, code)
        for field, value, code in [('name', 'default', 'profile_blocked'),
                                  ('role', 'review', 'profile_blocked'), ('model', 'fallback', 'model_blocked'),
                                  ('provider', 'paid-api', 'model_blocked'), ('reasoning', None, 'model_blocked'),
                                  ('operation', 'fallback', 'model_blocked')]:
            request = copy.deepcopy(self.request)
            request['profile'][field] = value
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.prepare(request)
                self.assertEqual(refusal.exception.code, code)
        self.frontier.assert_not_called()

        for stage in assignments.ROLES:
            request = self.configuration(stage)
            selected = assignments.validate(request)
            self.assertEqual({s['name'] for s in selected}, {s['name'] for s in request['skills']})
            for skill in selected:
                self.assertEqual(hashlib.sha256(skill['instructions'].encode()).hexdigest(), PINS[skill['name']])
                self.assertEqual(skill['instructions'].encode(), Path(skill['path']).read_bytes())
            if stage in {'review-spec', 'review-standards', 'diagnosis'}:
                self.assertNotIn('write_workspace', request['capabilities'])
            if assignments.PRECEDING[stage]:
                self.assert_refused('invalid_assignment', self.prepare, {**request, 'preceding': []}, True)
        for change in ('pin', 'dependencies', 'source', 'missing', 'duplicate', 'unknown', 'relative', 'absent'):
            request = copy.deepcopy(self.request)
            if change == 'pin': request['skills'][0]['sha256'] = '0' * 64
            if change == 'dependencies': request['skills'][0]['dependencies'] = ['unknown']
            if change == 'source': request['skills'][0]['source'] = '/wrong/SKILL.md'
            if change == 'missing': request['skills'] = []
            if change == 'duplicate': request['skills'] *= 2
            if change == 'unknown': request['skills'][0]['name'] = 'beta-role'
            if change == 'relative': request['skills'][0]['path'] = 'SKILL.md'
            if change == 'absent': request['skills'][0]['path'] = str(self.root / 'absent.md')
            with self.subTest(change=change):
                with self.assertRaises(RepositoryError) as refusal:
                    assignments.validate(request)
                self.assertEqual(refusal.exception.code, 'skill_blocked')

        request = self.copied_skills(self.request)
        selected = Path(request['skills'][0]['path'])
        raw = selected.read_bytes()
        selected.write_bytes(raw + b'\nchanged')
        self.assert_refused('skill_blocked', assignments.validate, request)
        selected.write_bytes(raw)
        alias = self.root / 'alias.md'
        alias.symlink_to(selected)
        request['skills'][0]['path'] = str(alias)
        self.assert_refused('skill_blocked', assignments.validate, request)

    def test_scope_and_frontier_refuse_before_claim(self):
        for field, value in [('repository', 'other/product'), ('repository_id', 124), ('spec_commit', 'f' * 40)]:
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.prepare({**self.request, field: value})
                self.assertEqual(refusal.exception.code, 'scope_mismatch')
        for field, value, code in [('id', 999, 'ticket_ineligible'), ('node_id', 'REPLACED', 'ticket_ineligible'),
                                   ('body_sha256', '0' * 64, 'scope_mismatch')]:
            request = copy.deepcopy(self.request)
            request['issue'][field] = value
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.prepare(request)
                self.assertEqual(refusal.exception.code, code)
        for change in ({'publication_complete': False}, {'frontier': {'foreign_active': [12], 'active': [], 'items': []}},
                       {'frontier': {'foreign_active': [], 'active': [12], 'items': []}},
                       {'frontier': {'foreign_active': [], 'active': [], 'items': [
                           {'number': 10, 'id': 100, 'node_id': 'ISSUE10', 'admissible': False}]}}):
            observed = self.observation(None, self.item, None)
            observed['ticket_work'].update(change)
            self.frontier.side_effect = None
            self.frontier.return_value = observed
            with self.assertRaises(RepositoryError) as refusal:
                self.prepare()
            self.assertEqual(refusal.exception.code, 'ticket_ineligible')
        self.assertEqual(assignments.rows(self.db), [])

        self.frontier.side_effect = self.observation
        self.item['reservation'] = {'issue_number': 12, 'assignment_id': 'ticket-input',
                                    'input_digest': 'd' * 64, 'status': 'reserved'}
        self.save_item()
        before = self.load_item()
        for dry in (False, True):
            self.frontier.reset_mock()
            self.read_issue.reset_mock()
            self.assert_refused('claim_conflict', self.prepare, dry=dry)
            self.assertEqual(self.load_item(), before)
            self.assertEqual(assignments.rows(self.db), [])
            self.frontier.assert_not_called()
            self.read_issue.assert_not_called()
        self.item.pop('reservation')
        self.save_item()
        row = self.seed_assignment()
        result = self.result(row)
        before = assignments.rows(self.db)
        for operator, identity, code in (('other', row['assignment_id'], 'approval_required'),
                                         ('42', 'unknown', 'not_found')):
            with self.subTest(operator=operator, identity=identity):
                self.assert_refused(code, assignments.inspect, self.db, 'product', 'm1', operator, identity)
                self.assert_refused(code, assignments.store_result, self.db, 'product', 'm1', operator, identity, result, self.github)
        self.assertEqual(assignments.rows(self.db), before)
        self.frontier.assert_not_called()
        self.read_issue.assert_not_called()
        self.github.request.assert_not_called()

    def test_grant_capacity_and_claim_independence_matrix(self):
        row = self.seed_assignment()
        request = copy.deepcopy(self.request)
        scope = row['ticket_scope']
        assignments.claims(self.db, request, scope, row['assignment_id'])
        with self.assertRaises(RepositoryError) as refusal:
            assignments.ticket_claims(self.db, {**scope, 'issue_number': 12})
        self.assertEqual(refusal.exception.code, 'claim_conflict')
        request['claim_id'] = 'other'
        with self.assertRaises(RepositoryError) as refusal:
            assignments.claims(self.db, request, scope, 'e' * 64)
        self.assertEqual(refusal.exception.code, 'profile_claim_conflict')
        request['profile']['name'] = 'different-name-same-home'
        self.assert_refused('profile_claim_conflict', assignments.claims, self.db, request, scope, 'e' * 64)
        self.item['reservation'] = {'issue_number': 10, 'assignment_id': 'ticket-input', 'input_digest': 'd' * 64}
        self.save_item()
        assignments.claims(self.db, self.request, scope, row['assignment_id'])
        reservation = copy.deepcopy(self.item['reservation'])
        self.assertEqual(self.prepare()['assignment_id'], row['assignment_id'])
        self.assertEqual(self.load_item()['reservation'], reservation)
        self.item['reservation']['status'] = 'invalidated'
        self.save_item()
        with self.assertRaises(RepositoryError):
            assignments.claims(self.db, self.request, scope, row['assignment_id'])
        debug = self.configuration('diagnosis')
        assignments.claims(self.db, debug, {**scope, 'issue_number': 12}, 'f' * 64)
        self.assertEqual(len(assignments.rows(self.db)), 1)
        self.assertEqual(assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])['claim_id'], self.request['claim_id'])

        self.frontier.reset_mock()
        scope = ('product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses')
        self.assertFalse(spending.admission_preview(self.db, scope)['reserved'])
        for sql in ["UPDATE model_grants SET remaining=0", "UPDATE model_grants SET remaining=10,expires=1",
                    "UPDATE model_grants SET expires=4611686018427387904,model='wrong'",
                    "UPDATE model_grants SET model='gpt-6.1-sol',kind='approval'", "DELETE FROM model_grants"]:
            with self.subTest(capacity=sql):
                self.db.execute(sql)
                self.db.commit()
                self.assert_refused('spending_blocked', self.prepare)
        self.db.execute('INSERT INTO model_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('uncertain', *scope, None, 1, 'pending', '0' * 64, None, 1, None))
        self.db.commit()
        self.assert_refused('outcome_uncertain', self.prepare)
        self.db.execute('DELETE FROM model_operations')
        self.db.commit()
        self.item['model_access'] = {'reason': 'quota_exhausted'}
        self.save_item()
        self.assert_refused('quota_exhausted', self.prepare)
        self.frontier.assert_not_called()

    def test_result_identity_truth_and_drift_matrix(self):
        request = self.copied_skills(self.request)
        row = self.seed_assignment(request)
        result = self.result(row)
        for field, value in [('assignment_id', 'wrong'), ('claim_id', 'wrong'), ('handoff_digest', '0' * 64),
            ('loads', []), ('artifacts', []), ('work', []), ('tests', []), ('status', 'success')]:
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.store(row, {**result, field: value})
                self.assertEqual(refusal.exception.code, 'invalid_result')
        changed = copy.deepcopy(result)
        changed['loads'][0]['sha256'] = '0' * 64
        self.assert_refused('invalid_result', self.store, row, changed)
        self.assert_refused('invalid_result', self.store, row, {'status': 'done'})
        self.assertNotIn('submitted_result', assignments.rows(self.db)[0])

        before = assignments.rows(self.db)
        self.read_issue.return_value = {'body': 'Local issue\nChanged scope',
                                      'html_url': 'https://example.invalid/issues/10'}
        self.assert_refused('scope_mismatch', self.store, row, result)
        self.assertEqual(assignments.rows(self.db), before)
        changed = copy.deepcopy(request)
        changed['issue']['body_sha256'] = hashlib.sha256(b'Local issue\nChanged scope').hexdigest()
        self.assert_refused('claim_conflict', self.prepare, changed)
        self.read_issue.return_value = {'body': 'Local issue', 'html_url': 'https://example.invalid/issues/10'}
        selected = Path(request['skills'][0]['path'])
        raw = selected.read_bytes()
        selected.write_text('Changed selected input')
        self.assert_refused('skill_blocked', self.store, row, result)
        self.assertNotIn('submitted_result', assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id']))
        self.assertEqual(assignments.rows(self.db), before)
        selected.write_bytes(raw)
        self.github.request.assert_not_called()

        stored = self.store(row, result)
        self.assertTrue(stored['result_disposition']['structurally_valid'])
        for flag in ('trusted_execution', 'advance_allowed', 'close_allowed'):
            self.assertFalse(stored['result_disposition'][flag])
        self.assertEqual(self.store(row, result)['submitted_result'], result)
        self.assert_refused('result_conflict', self.store, row, {**result, 'work': ['replacement']})
        self.assertEqual(assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])['submitted_result'], result)

    def test_late_pause_blocks_claim_and_result_but_keeps_inspection(self):
        row = self.seed_assignment()
        result = self.result(row)
        def pause_read(*args, **kwargs):
            controls.request(self.db, 'product', 'm1', '42', 'pause', 'pause at read-return')
            return {'body': 'Local issue', 'html_url': 'https://example.invalid/issues/10'}
        self.read_issue.side_effect = pause_read
        with self.assertRaises(RepositoryError) as refusal:
            self.store(row, result)
        self.assertEqual(refusal.exception.code, 'iteration_paused')
        self.assertNotIn('submitted_result', assignments.rows(self.db)[0])
        self.assertEqual(assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])['handoff_digest'], row['handoff_digest'])
        control_root, control_path = controls.paths(self.db, 'product', 'm1')
        assert control_root is not None and control_path is not None
        self.assertEqual(control_root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(control_path.stat().st_mode & 0o777, 0o600)
        record = controls.read(self.db, 'product', 'm1')
        assert record is not None
        self.assertEqual(record['action'], 'pause')
        self.assert_refused('iteration_paused', self.prepare)
        control_path.unlink()  # Isolate stored-status refusal from the sidecar guard.
        self.assertIsNone(controls.read(self.db, 'product', 'm1'))
        self.item['status'] = 'paused'
        self.save_item()
        self.assert_refused('iteration_paused', self.prepare)
        self.assert_refused('iteration_paused', self.store, row, result)
        self.assertEqual(assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])['handoff_digest'], row['handoff_digest'])

        for field, code in [('requirement_disposition', 'requirements_held'), ('stuck_disposition', 'stuck_held')]:
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    assignments.require_result_active({**row, field: 'held'})
                self.assertEqual(refusal.exception.code, code)
        self.item['handoff']['commit'] = 'f' * 40
        self.save_item()
        with self.assertRaises(RepositoryError) as refusal:
            assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])
        self.assertEqual(refusal.exception.code, 'stale_assignment')
        with self.assertRaises(RepositoryError) as refusal:
            assignments.launch()
        self.assertEqual(refusal.exception.code, 'isolation_unavailable')

    def test_direct_concurrent_claims_share_real_state_lock_without_workers(self):
        from factory_v1.repositories import state_lock
        requests = [copy.deepcopy(self.request), copy.deepcopy(self.request)]
        requests[1]['issue'].update(number=12, id=112, node_id='ISSUE12')
        requests[1]['profile'].update(name='other-profile', home=str(self.root / 'other-home'))
        requests[1]['claim_id'] = 'other-claim'
        observed = self.observation(None, self.item, None)
        observed['ticket_work']['frontier']['items'].append({'number': 12, 'id': 112, 'node_id': 'ISSUE12', 'admissible': True})
        self.frontier.side_effect = None
        self.frontier.return_value = observed
        def prepare(request):
            with closing(sqlite3.connect(self.root / 'state.sqlite')) as database, state_lock(database):
                try:
                    return assignments.prepare(database, 'product', 'm1', '42', request, self.github)['assignment_id']
                except RepositoryError as refusal:
                    return refusal.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(prepare, requests))
        self.assertEqual(responses.count('claim_conflict'), 1)
        self.assertEqual(len(assignments.rows(self.db)), 1)
        self.github.request.assert_not_called()

    def test_json_cli_and_path_pre_read_guards_matrix(self):
        from factory_v1.__main__ import unique_fields, IntakeError
        with self.assertRaises(IntakeError):
            json.loads('{"stage":"implementation","stage":"diagnosis"}', object_pairs_hook=unique_fields)
        for field in ('issue', 'profile', 'skills', 'standards', 'preceding', 'stage'):
            for value in (None, [], {}, 3):
                if field == 'preceding' and value == []:
                    continue
                with self.subTest(field=field, value=value):
                    with self.assertRaises(RepositoryError):
                        assignments.validate({**self.request, field: value})
        self.assertEqual(assignments.rows(self.db), [])
        self.frontier.assert_not_called()

        import io
        import sys
        from contextlib import redirect_stderr, redirect_stdout
        from factory_v1 import __main__ as cli
        request_path = self.root / 'malformed.json'
        lock = self.root / 'state.sqlite.onboarding.lock'
        lock.touch()
        entrypoint = compile(Path(cli.__file__).read_text(), cli.__file__, 'exec')
        argv = ['factory_v1', '--state', str(self.root / 'state.sqlite'), '--operator-id', '42',
                'prepare-assignment', '--project', 'product', '--iteration', 'm1',
                '--request', str(request_path), '--api-base', 'https://api.github.com']
        for raw in ('{"stage":"implementation","stage":"diagnosis"}', '{',
                    json.dumps({**self.request, 'preceding': None})):
            with self.subTest(raw=raw[:40]):
                request_path.write_text(raw)
                before = self.load_item()
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(sys, 'argv', argv), patch.object(cli.os, 'umask'), \
                        redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as exit:
                        # Execute the actual CLI envelope in-process, with no launcher.
                        exec(entrypoint, {'__name__': '__main__', '__package__': 'factory_v1'})
                self.assertEqual(exit.exception.code, 2)
                self.assertEqual(json.loads(stderr.getvalue())['error'], 'invalid_assignment')
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(self.load_item(), before)
                self.assertEqual(assignments.rows(self.db), [])
        self.frontier.assert_not_called()
        self.read_issue.assert_not_called()
        self.github.request.assert_not_called()

        for existing in (False, True):
            row = self.prepare() if existing else None
            for field in ('workspace', 'home'):
                for malformed in ('nul', 'double-leading-slash'):
                    for dry in (False, True):
                        with self.subTest(existing=existing, field=field, malformed=malformed, dry=dry):
                            request = copy.deepcopy(self.request)
                            holder = request if field == 'workspace' else request['profile']
                            holder[field] = holder[field] + '\x00suffix' if malformed == 'nul' else '/' + holder[field]
                            before = self.db.total_changes
                            self.frontier.reset_mock()
                            self.read_issue.reset_mock()
                            self.assert_refused('invalid_assignment' if field == 'workspace' else 'profile_blocked',
                                                self.prepare, request, dry)
                            self.assertEqual(self.db.total_changes, before)
                            self.frontier.assert_not_called()
                            self.read_issue.assert_not_called()
            if row:
                self.assertEqual(self.prepare()['handoff_digest'], row['handoff_digest'])


    def reservation_boundary(self):
        # Fixture tracker rows and outbound status writes, not reservation guards.
        statuses = {10: 'ready', 12: 'ready'}
        self.item['correlation'] = {'project_id': 'product', 'iteration_id': 'm1',
            'stage': 'tickets_published', 'control_run_id': 'local-control', 'evidence_id': 'local-evidence'}
        self.item['ticket_work'].update(board={'project_id': 'BOARD', 'status': {
            'id': 'STATUS', 'options': {'Active': 'ACTIVE'}}},
            request={'tracker': {'statuses': {'active': 'Active'}}})
        self.save_item()
        def observed(database, item, github):
            item = copy.deepcopy(item)
            item['ticket_work']['frontier'] = {'foreign_active': [],
                'active': [n for n, s in statuses.items() if s == 'active'],
                'eligible': [n for n, s in statuses.items() if s == 'ready'],
                'items': [{'number': n, 'id': 100 if n == 10 else 112, 'node_id': f'ISSUE{n}',
                           'membership_id': str(n), 'admissible': True, 'status': s} for n, s in statuses.items()]}
            assignments.tickets.checkpoint(database, item)
            return item
        def set_status(github, board, member, field, option):
            self.assertEqual((board, field, option), ('BOARD', 'STATUS', 'ACTIVE'))
            statuses[int(member)] = 'active'
        self.frontier.side_effect = observed
        writes = self.enterContext(patch.object(assignments.tickets.gh, 'set_field', side_effect=set_status))
        return statuses, writes

    def test_prepare_and_reserve_real_lock_interleavings_share_or_refuse(self):
        from factory_v1.repositories import state_lock
        for number in (10, 12):
            for prepare_first in (False, True):
                with self.subTest(number=number, prepare_first=prepare_first):
                    self.db.execute('DELETE FROM role_assignments')
                    self.db.commit()
                    statuses, writes = self.reservation_boundary()
                    acquired, contender = threading.Event(), threading.Event()
                    def operation(kind, first):
                        with closing(sqlite3.connect(self.root / 'state.sqlite')) as database:
                            if not first:
                                self.assertTrue(acquired.wait(2), 'first writer never acquired lock')
                                contender.set()
                            with state_lock(database):
                                if first:
                                    acquired.set()
                                    self.assertTrue(contender.wait(2), 'second writer never attempted admission')
                                try:
                                    if kind == 'prepare':
                                        return assignments.prepare(database, 'product', 'm1', '42', self.request, self.github)
                                    # Intentionally stale caller item: reserve must reload under lock.
                                    return assignments.tickets.reserve(database, self.item, self.github, number)
                                except RepositoryError as refusal:
                                    return refusal.code
                                finally:
                                    database.rollback()
                    # No lock is held by the thread that joins these futures.
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        prepare = pool.submit(operation, 'prepare', prepare_first)
                        reserve = pool.submit(operation, 'reserve', not prepare_first)
                        prepared, reserved = prepare.result(timeout=5), reserve.result(timeout=5)
                    observed = self.load_item()
                    if number == 10:
                        self.assertIsInstance(prepared, dict)
                        self.assertIsInstance(reserved, dict)
                        self.assertEqual(observed['reservation'], reserved['reservation'])
                        self.assertEqual(observed['ticket_work']['frontier']['active'], [10])
                        self.assertEqual(prepared['ticket_scope']['issue_number'], 10)
                        self.assertEqual(self.prepare()['assignment_id'], prepared['assignment_id'])
                        replay = assignments.tickets.reserve(self.db, self.item, self.github, 10)
                        self.assertEqual(replay['reservation'], reserved['reservation'])
                        self.assertEqual(writes.call_count, 1)
                        self.assertEqual(len(assignments.rows(self.db)), 1)
                    elif prepare_first:
                        self.assertEqual(reserved, 'claim_conflict')
                        self.assertEqual(prepared['ticket_scope']['issue_number'], 10)
                        self.assertNotIn('reservation', observed)
                        self.assertEqual(observed, self.item)
                        self.assertEqual(statuses, {10: 'ready', 12: 'ready'})
                        writes.assert_not_called()
                    else:
                        self.assertEqual(prepared, 'claim_conflict')
                        self.assertEqual(observed['reservation']['issue_number'], 12)
                        self.assertEqual(observed['ticket_work']['frontier']['active'], [12])
                        self.assertEqual(statuses[10], 'ready')
                        self.assertEqual(assignments.rows(self.db), [])
                        self.assertEqual(writes.call_count, 1)
                        self.assert_refused('claim_conflict', self.prepare)
                    self.github.request.assert_not_called()


    def test_generic_prepare_refuses_placeholder_stage_precursors(self):
        for stage in ('review-standards', 'review-spec', 'simplify', 'corrections', 'diagnosis'):
            with self.subTest(stage=stage):
                request = self.configuration(stage)
                self.assert_refused('implementation_unverified', self.prepare, request)
                self.assert_refused('implementation_unverified', self.prepare, request, True)
        self.assertEqual(assignments.rows(self.db), [])
        self.frontier.assert_not_called()
        self.read_issue.assert_not_called()
        self.github.request.assert_not_called()


    def test_attention_read_return_preserves_other_writer_checkpoint(self):
        from factory_v1.repositories import state_lock
        self.item['origin'] = {'platform': 'discord', 'chat_id': '123', 'thread_id': '123',
                               'parent_chat_id': '456', 'scope_id': '789'}
        self.item['correlation'] = {'project_id': 'product', 'iteration_id': 'm1',
            'stage': 'tickets_published', 'control_run_id': 'local-control', 'evidence_id': 'local-evidence'}
        self.save_item()
        event = {'event_id': 'local-attention', 'kind': 'incident', 'message': 'Local incident',
                 'issue_number': 10, 'run_id': 'local-run', 'evidence_ids': ['local-evidence']}
        saved = []
        def observe(database, stale, github):
            with closing(sqlite3.connect(self.root / 'state.sqlite')) as writer:
                record = attention.lifecycle(writer, 'product', 'm1', '42', 'attention', event)
                with state_lock(writer):
                    fresh = self.load_item(writer)
                    fresh['ticket_work']['other_writer_checkpoint'] = 'must survive stale observation'
                    assignments.tickets.checkpoint(writer, fresh)
                saved.append((fresh, record))
            # The real checkpoint uses the supplied observation connection. Passing
            # the main connection here would overwrite the other writer's change.
            observed = self.observation(database, stale, github)
            assignments.tickets.checkpoint(database, observed)
            return observed
        self.frontier.side_effect = observe
        row = self.prepare()
        self.assertEqual(self.load_item(), saved[0][0])
        self.assertEqual(attention.records(self.db, 'product', 'm1', self.load_item()), [saved[0][1]])
        self.assertEqual(len(saved), 1)
        self.assertEqual(assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])['ticket_scope']['issue_number'], 10)
        self.github.request.assert_not_called()

    def test_released_direction_binds_each_affected_identity_and_body(self):
        response = {'response': 'Local explicit retry'}
        issues = {'10': self.request['issue'], '11': {'number': 11, 'id': 111, 'node_id': 'ISSUE11',
                  'body_sha256': hashlib.sha256(b'Own issue 11 body').hexdigest()}}
        for number, body in ((10, 'Local issue'), (11, 'Own issue 11 body')):
            request = {**self.request, 'issue': issues[str(number)]}
            self.read_issue.return_value = {'body': body, 'html_url': f'https://example.invalid/issues/{number}'}
            observed = self.observation(None, self.item, None)
            observed['ticket_work']['frontier']['items'].append({'number': 11, 'id': 111, 'node_id': 'ISSUE11', 'admissible': True})
            self.frontier.side_effect = None
            self.frontier.return_value = observed
            for change in ({}, {'id': 999}, {'node_id': 'wrong'}, {'body_sha256': '0' * 64}):
                with self.subTest(number=number, change=change):
                    retained = copy.deepcopy(issues)
                    retained[str(number)].update(change)
                    self.item['stuck_work'] = {'old': {'status': 'released', 'sequence': 1, 'affected': [10, 11],
                        'issues': retained, 'applied_response': response}}
                    self.save_item()
                    before = self.db.total_changes
                    row = self.prepare(request, dry=True)
                    self.assertEqual(row['handoff'].get('operator_direction'), None if change else response)
                    self.assertEqual(self.db.total_changes, before)


if __name__ == "__main__":
    unittest.main()
