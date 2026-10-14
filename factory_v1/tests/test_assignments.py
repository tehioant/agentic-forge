"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import time
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
from factory_v1 import assignments, controls, spending, requirements
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
        self.assertEqual(self.db.total_changes, before)
        self.assertFalse(dry['claim_persisted'])
        row = self.prepare()
        self.assertEqual(row['assignment_id'], dry['assignment_id'])
        self.assertEqual(self.prepare()['handoff_digest'], row['handoff_digest'])
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            self.assertEqual(assignments.inspect(restarted, 'product', 'm1', '42', row['assignment_id'])['handoff'], row['handoff'])
        self.assertFalse(row['execution_allowed'])
        request = self.configuration('repair')
        first = self.prepare(request, dry=True)
        request['skills'].reverse()
        self.assertEqual(self.prepare(request, dry=True)['assignment_id'], first['assignment_id'])
        self.assertEqual(first['handoff']['skill_entry_points'], ['diagnosing-bugs', 'implement'])
        self.github.request.assert_not_called()

    def test_validation_identity_profiles_paths_and_capabilities(self):
        assignments.validate(self.request)
        for field, value, code in [('workspace', 'relative', 'invalid_assignment'),
            ('workspace', str(self.root) + '\x00bad', 'invalid_assignment'),
            ('baseline', 'main', 'invalid_assignment'), ('repository_id', True, 'invalid_assignment'),
            ('capabilities', ['merge'], 'capability_blocked'), ('standards', [], 'invalid_assignment')]:
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    assignments.validate({**self.request, field: value})
                self.assertEqual(refusal.exception.code, code)
        for field, value, code in [('name', 'default', 'profile_blocked'), ('home', '//aliased', 'profile_blocked'),
                                  ('role', 'review', 'profile_blocked'), ('model', 'fallback', 'model_blocked')]:
            request = copy.deepcopy(self.request)
            request['profile'][field] = value
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    assignments.validate(request)
                self.assertEqual(refusal.exception.code, code)
        self.frontier.assert_not_called()

    def test_real_skill_closure_and_stage_bindings(self):
        for stage in assignments.ROLES:
            request = self.configuration(stage)
            selected = assignments.validate(request)
            self.assertEqual({s['name'] for s in selected}, {s['name'] for s in request['skills']})
            for skill in selected:
                self.assertEqual(hashlib.sha256(skill['instructions'].encode()).hexdigest(), PINS[skill['name']])
            if stage in {'review-spec', 'review-standards', 'diagnosis'}:
                self.assertNotIn('write_workspace', request['capabilities'])
        for change in ('pin', 'dependencies', 'source', 'missing', 'duplicate'):
            request = copy.deepcopy(self.request)
            if change == 'pin': request['skills'][0]['sha256'] = '0' * 64
            if change == 'dependencies': request['skills'][0]['dependencies'] = ['unknown']
            if change == 'source': request['skills'][0]['source'] = '/wrong/SKILL.md'
            if change == 'missing': request['skills'] = []
            if change == 'duplicate': request['skills'] *= 2
            with self.subTest(change=change):
                with self.assertRaises(RepositoryError) as refusal:
                    assignments.validate(request)
                self.assertEqual(refusal.exception.code, 'skill_blocked')

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

    def test_grants_exact_scope_expiry_exhaustion_and_uncertainty(self):
        scope = ('product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses')
        self.assertFalse(spending.admission_preview(self.db, scope)['reserved'])
        for sql in ["UPDATE model_grants SET remaining=0", "UPDATE model_grants SET remaining=10,expires=1",
                    "UPDATE model_grants SET expires=4611686018427387904,model='wrong'",
                    "UPDATE model_grants SET model='gpt-6.1-sol',kind='approval'", "DELETE FROM model_grants"]:
            self.db.execute(sql)
            self.db.commit()
            with self.assertRaises(RepositoryError) as refusal:
                self.prepare()
            self.assertEqual(refusal.exception.code, 'spending_blocked')
        self.db.execute('INSERT INTO model_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('uncertain', *scope, None, 1, 'pending', '0' * 64, None, 1, None))
        self.db.commit()
        with self.assertRaises(RepositoryError) as refusal:
            self.prepare()
        self.assertEqual(refusal.exception.code, 'outcome_uncertain')
        self.db.execute('DELETE FROM model_operations')
        self.db.commit()
        self.item['model_access'] = {'reason': 'quota_exhausted'}
        self.save_item()
        with self.assertRaises(RepositoryError) as refusal:
            self.prepare()
        self.assertEqual(refusal.exception.code, 'quota_exhausted')
        self.frontier.assert_not_called()

    def test_claims_reservations_profile_conflicts_and_diagnosis_independence(self):
        row = self.prepare()
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
        self.item['reservation'] = {'issue_number': 10, 'assignment_id': 'ticket-input', 'input_digest': 'd' * 64}
        self.save_item()
        assignments.claims(self.db, self.request, scope, row['assignment_id'])
        self.item['reservation']['status'] = 'invalidated'
        self.save_item()
        with self.assertRaises(RepositoryError):
            assignments.claims(self.db, self.request, scope, row['assignment_id'])
        debug = self.configuration('diagnosis')
        assignments.claims(self.db, debug, {**scope, 'issue_number': 12}, 'f' * 64)
        self.assertEqual(len(assignments.rows(self.db)), 1)

    def test_result_truthful_untrusted_replay_and_conflict(self):
        row = self.prepare()
        result = self.result(row)
        stored = self.store(row, result)
        self.assertTrue(stored['result_disposition']['structurally_valid'])
        for flag in ('trusted_execution', 'advance_allowed', 'close_allowed'):
            self.assertFalse(stored['result_disposition'][flag])
        self.assertEqual(self.store(row, result)['submitted_result'], result)
        with self.assertRaises(RepositoryError) as refusal:
            self.store(row, {**result, 'work': ['replacement']})
        self.assertEqual(refusal.exception.code, 'result_conflict')
        self.assertEqual(assignments.inspect(self.db, 'product', 'm1', '42', row['assignment_id'])['submitted_result'], result)

    def test_result_identity_loads_artifacts_and_tests_matrix(self):
        row = self.prepare()
        result = self.result(row)
        for field, value in [('assignment_id', 'wrong'), ('claim_id', 'wrong'), ('handoff_digest', '0' * 64),
            ('loads', []), ('artifacts', []), ('work', []), ('tests', []), ('status', 'success')]:
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.store(row, {**result, field: value})
                self.assertEqual(refusal.exception.code, 'invalid_result')
        self.assertNotIn('submitted_result', assignments.rows(self.db)[0])

    def test_late_pause_blocks_claim_and_result_but_keeps_inspection(self):
        row = self.prepare()
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

    def test_revision_inspection_and_result_independent_holds(self):
        row = self.prepare()
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

    def test_direct_json_duplicate_hook_and_nested_request_shapes(self):
        from factory_v1.__main__ import unique_fields, IntakeError
        with self.assertRaises(IntakeError):
            json.loads('{"stage":"implementation","stage":"diagnosis"}', object_pairs_hook=unique_fields)
        for field in ('issue', 'profile', 'skills', 'standards', 'stage'):
            for value in (None, [], {}, 3):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(RepositoryError):
                        assignments.validate({**self.request, field: value})
        self.assertEqual(assignments.rows(self.db), [])
        self.frontier.assert_not_called()

    def test_released_direction_binds_each_affected_identity_and_body(self):
        response = {'response': 'Local explicit retry'}
        issue = self.request['issue']
        for change in ({}, {'id': 999}, {'node_id': 'wrong'}, {'body_sha256': '0' * 64}):
            self.item['stuck_work'] = {'old': {'status': 'released', 'sequence': 1, 'affected': [10, 11],
                'issues': {'10': {**issue, **change}, '11': {**issue, 'id': 111}}, 'applied_response': response}}
            self.save_item()
            row = self.prepare(dry=True)
            self.assertEqual(row['handoff'].get('operator_direction'), None if change else response)


if __name__ == "__main__":
    unittest.main()
