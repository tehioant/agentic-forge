"""Fresh-process assignment lifecycle tests. Tracker and execution evidence are fixtures only."""
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
    'implement': '6b13bcb6119df090c97be3c8a90560071a26f21f4fc6ad0facde97b25ecb1da0',
    'tdd': '93ea419b76e9caaf26153b828e984f7c3fb136f4caa67b14af95f32ea965a1cc',
    'codebase-design': '2c20617f87ec8af6a434859f381b2f061a69b530444e74eb39e78bb016a6d1e2',
    'code-review': '47f4e52c21694def9c7c11cbfbf891ca35eac7a93e395797515be3c8a409ae50',
    'simplify-code': '9ee977c3362a1c07f2e327198e066ddb3c0ed67709706fc410165ee820b2ca01',
    'diagnosing-bugs': '9168404abda0967a5d32977e3498cd95fda6807018852f3de736a78357c82b40',
    'tdd/tests.md': '859f9e592c188fda4fc7277dd180e4ce9c7a2e13f6efe1f6f29eccc9d28c106a',
    'tdd/mocking.md': '3ceb807fdf4a47d6a93d4d9a891e5ba6d362a6247bd08adc451feebfc17361ef',
}
ENGINEERING = ['implement']


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
                           'dependencies': {'tdd': ['codebase-design', 'tdd/tests.md', 'tdd/mocking.md']}.get(name, [])})
        return result

    def configuration(self, number=10, stage='implementation'):
        names, role, preceding = {
            'implementation': (ENGINEERING, 'implementation', []),
            'corrections': (ENGINEERING, 'implementation', ['candidate', 'findings']),
            'simplify': (['simplify-code'], 'simplification', ['candidate', 'implementation-evidence']),
            'review-standards': (['code-review'], 'review', ['candidate', 'simplification-evidence']),
            'review-spec': (['code-review'], 'review', ['candidate', 'simplification-evidence']),
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

    def test_prepare_inspect_restart_and_exact_replay_preserve_actual_bytes(self):
        prepared = self.ok(self.command())
        self.assertTrue(prepared['assignment_ready'])
        self.assertFalse(prepared['launchable'])
        self.assertFalse(prepared['execution_allowed'])
        self.assertTrue(prepared['claim_persisted'])
        self.assertFalse(prepared['spending_admission']['reserved'])
        self.assertEqual(self.ok(self.command())['assignment_id'], prepared['assignment_id'])
        inspected = self.ok(self.inspect(prepared))
        self.assertEqual(inspected['handoff_digest'], prepared['handoff_digest'])
        for selected in inspected['handoff']['skills']:
            self.assertEqual(selected['instructions'].encode(), Path(selected['path']).read_bytes())
        self.assertEqual(inspected['handoff']['credentials'], [])
        self.assertFalse(inspected['handoff']['inherited_memory'])
        self.assert_no_external_writes()
        self.assertEqual(self.tracker.inspect()['stage'], self.item['stage'])

    def test_dry_run_is_deterministic_and_writes_no_state_or_claim(self):
        before = self.fixture.state.read_bytes()
        first = self.ok(self.command(dry_run=True))
        second = self.ok(self.command(dry_run=True))
        self.assertEqual(first, second)
        self.assertFalse(first['claim_persisted'])
        self.assertEqual(self.fixture.state.read_bytes(), before)
        self.refused(self.inspect(first), 'not_found')
        self.assert_no_external_writes()
        prepared = self.ok(self.command())
        self.assertEqual(prepared['assignment_id'], first['assignment_id'])
        self.assertEqual(prepared['handoff_digest'], first['handoff_digest'])

    def test_launch_refuses_even_with_isolated_assertion_and_no_state(self):
        for extra in ((), ('--isolated',)):
            self.refused(self.command('launch-assignment', assignment='not-even-prepared', extra=extra), 'isolation_unavailable')
        self.assertEqual(self.fixture.calls, [])

    def test_startup_configuration_refused_before_github_or_claim(self):
        cases = [({'stage': 'unknown'}, 'invalid_assignment'), ({'workspace': 'relative'}, 'invalid_assignment'),
                 ({'workspace': '/workspace/../other'}, 'invalid_assignment'), ({'baseline': 'main'}, 'invalid_assignment'),
                 ({'result_contract': 'success-is-authority'}, 'capability_blocked'),
                 ({'capabilities': ['merge', 'provider-credentials']}, 'capability_blocked'),
                 ({'memory': 'ambient-session'}, 'invalid_assignment'), ({'standards': []}, 'invalid_assignment')]
        for change, code in cases:
            with self.subTest(change=change):
                self.refused(self.command(request={**self.request, **change}), code)
        for change, code in [({'provider': 'paid-api'}, 'model_blocked'), ({'reasoning': None}, 'model_blocked'),
                             ({'name': 'default'}, 'profile_blocked'), ({'role': 'review'}, 'profile_blocked')]:
            request = copy.deepcopy(self.request)
            request['profile'].update(change)
            self.refused(self.command(request=request), code)
        self.assertEqual(self.fixture.calls, [])
        self.ok(self.command())

    def test_unusable_workspace_and_profile_paths_refuse_before_tracker_or_claim(self):
        for existing_claim in (False, True):
            if existing_claim:
                prepared = self.ok(self.command())
            for field in ('workspace', 'profile-home'):
                for invalid in ('nul', 'double-leading-slash'):
                    for dry_run in (True, False):
                        with self.subTest(existing_claim=existing_claim, field=field,
                                          invalid=invalid, dry_run=dry_run):
                            request = copy.deepcopy(self.request)
                            path = request['workspace'] if field == 'workspace' else request['profile']['home']
                            malformed = path + '\x00suffix' if invalid == 'nul' else '/' + path
                            if field == 'workspace':
                                request['workspace'] = malformed
                                code = 'invalid_assignment'
                            else:
                                request['profile']['home'] = malformed
                                request['profile']['name'] = 'different-name-same-home'
                                request['claim_id'] = 'aliased-profile-claim'
                                code = 'profile_blocked'
                            before = self.fixture.state.read_bytes()
                            self.fixture.calls.clear()
                            self.refused(self.command(request=request, dry_run=dry_run), code)
                            self.assertEqual(self.fixture.calls, [])
                            self.assertEqual(self.fixture.state.read_bytes(), before)
            if existing_claim:
                self.assertEqual(self.ok(self.inspect(prepared))['handoff_digest'], prepared['handoff_digest'])
                self.assertEqual(self.ok(self.command())['assignment_id'], prepared['assignment_id'])

    def test_selected_skill_closure_missing_ambiguous_unknown_or_changed_is_refused(self):
        for change in ('missing', 'duplicate', 'unknown', 'pin', 'source', 'dependencies', 'relative', 'absent'):
            request = copy.deepcopy(self.request)
            if change == 'missing': request['skills'].pop()
            if change == 'duplicate': request['skills'].append(copy.deepcopy(request['skills'][0]))
            if change == 'unknown': request['skills'][0]['name'] = 'beta-role'
            if change == 'pin': request['skills'][0]['sha256'] = '0' * 64
            if change == 'source': request['skills'][0]['source'] = '/other/duplicate/SKILL.md'
            if change == 'dependencies': request['skills'][0]['dependencies'] = ['unknown-dependency']
            if change == 'relative': request['skills'][0]['path'] = 'SKILL.md'
            if change == 'absent': request['skills'][0]['path'] = str(self.root / 'absent.md')
            self.refused(self.command(request=request), 'skill_blocked')
        dependency = self.skills / 'implement/SKILL.md'
        raw = dependency.read_bytes()
        dependency.write_bytes(raw + b'\nchanged')
        self.refused(self.command(), 'skill_blocked')
        dependency.write_bytes(raw)
        symlink = self.root / 'alias.md'
        symlink.symlink_to(dependency)
        request = copy.deepcopy(self.request)
        request['skills'][-1]['path'] = str(symlink)
        self.refused(self.command(request=request), 'skill_blocked')
        self.assertEqual(self.fixture.calls, [])

    def test_scope_repository_id_issue_identity_body_and_spec_refusals(self):
        for field, value in [('repository', 'other/product'), ('repository_id', 124), ('spec_commit', 'c' * 40)]:
            self.refused(self.command(request={**self.request, field: value}), 'scope_mismatch')
        self.assertEqual(self.fixture.calls, [])
        for key, value, code in [('id', 99999, 'ticket_ineligible'), ('node_id', 'wrong', 'ticket_ineligible'),
                                 ('body_sha256', '0' * 64, 'scope_mismatch')]:
            request = copy.deepcopy(self.request)
            request['issue'][key] = value
            self.refused(self.command(request=request), code)
        self.assert_no_external_writes()

    def test_frontier_blockers_deferred_foreign_active_and_partial_publication_refuse(self):
        self.refused(self.command(request=self.configuration(number=11)), 'ticket_ineligible')
        for status in ('Blocked', 'Deferred'):
            self.tracker.members[10]['fields']['STATUS'] = status
            self.refused(self.command(), 'ticket_ineligible')
        self.tracker.members[10]['fields']['STATUS'] = 'Ready'
        self.tracker.members[12]['fields']['STATUS'] = 'Active'
        self.refused(self.command(), 'ticket_ineligible')
        self.tracker.members[12]['fields']['SCOPE'] = 'M2'
        self.refused(self.command(), 'ticket_ineligible')
        self.tracker.members[12]['fields'].update(STATUS='Ready', SCOPE='M1')
        self.mutate_iteration(lambda item: item['ticket_work'].update(publication_complete=False))
        self.refused(self.command(), 'ticket_ineligible')
        self.assert_no_external_writes()

    def test_spending_missing_exhausted_expired_wrong_scope_and_uncertain_refuse_before_frontier(self):
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('UPDATE model_grants SET remaining=0')
        self.refused(self.command(), 'spending_blocked')
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('UPDATE model_grants SET remaining=10,expires=?', (int(time.time()) - 1,))
        self.refused(self.command(), 'spending_blocked')
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('UPDATE model_grants SET expires=?,model=?', (int(time.time()) + 600, 'different-model'))
        self.refused(self.command(), 'spending_blocked')
        self.mutate_iteration(lambda item: item.update(model_access={'reason': 'quota_exhausted'}))
        self.refused(self.command(), 'quota_exhausted')
        self.assertEqual(self.fixture.calls, [])

    def test_one_ticket_and_one_profile_claim_survive_fresh_processes(self):
        prepared = self.ok(self.command())
        self.refused(self.command(request=self.configuration(number=12)), 'claim_conflict')
        altered = copy.deepcopy(self.request)
        altered['claim_id'] = 'second-writer'
        self.refused(self.command(request=altered), 'profile_claim_conflict')
        altered['profile']['name'] = 'different-name-same-home'
        self.refused(self.command(request=altered), 'profile_claim_conflict')
        self.assertEqual(self.ok(self.inspect(prepared))['claim_id'], 'fixture-claim-implementation')

    def test_assignment_claim_refuses_different_reservation_without_effects(self):
        prepared = self.ok(self.command())
        before = self.tracker.inspect()
        self.fixture.calls.clear()
        self.refused(self.tracker.control('reserve-ticket', issue=12), 'claim_conflict')
        self.assertEqual(self.tracker.inspect(), before)
        self.assertEqual(self.ok(self.inspect(prepared))['ticket_scope']['issue_number'], 10)
        self.assertEqual(self.tracker.members[12]['fields']['STATUS'], 'Ready')
        self.assert_no_external_writes()

    def test_assignment_claim_allows_same_ticket_reservation_and_replay(self):
        prepared = self.ok(self.command())
        reserved = self.ok(self.tracker.control('reserve-ticket', issue=10))
        self.assertEqual(reserved['ticket_work']['frontier']['active'], [10])
        self.assertEqual(self.ok(self.tracker.control('reserve-ticket', issue=10))['reservation'], reserved['reservation'])
        self.assertEqual(self.ok(self.command())['assignment_id'], prepared['assignment_id'])
        self.assertEqual(self.ok(self.inspect(prepared))['ticket_scope']['issue_number'], 10)

    def test_ticket_reservation_refuses_different_assignment_without_effects(self):
        reserved = self.ok(self.tracker.control('reserve-ticket', issue=12))
        self.fixture.calls.clear()
        self.refused(self.command(), 'claim_conflict')
        self.assertEqual(self.tracker.inspect(), reserved)
        self.refused(self.command('inspect-assignment', assignment='unknown'), 'not_found')
        self.assert_no_external_writes()

    def test_concurrent_prepare_and_reserve_admit_only_one_ticket(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            prepare = pool.submit(self.command)
            reserve = pool.submit(self.tracker.control, 'reserve-ticket', issue=12)
            prepared, reserved = prepare.result(), reserve.result()
        self.assertEqual(sorted(r.returncode for r in (prepared, reserved)), [0, 2])
        denied = prepared if prepared.returncode == 2 else reserved
        self.refused(denied, 'claim_conflict')
        observed = self.tracker.inspect()
        if prepared.returncode == 0:
            assignment = self.ok(prepared)
            self.assertNotIn('reservation', observed)
            self.assertEqual(observed['ticket_work']['frontier']['active'], [])
            self.assertEqual(self.ok(self.inspect(assignment))['ticket_scope']['issue_number'], 10)
            self.assert_no_external_writes()
        else:
            self.assertEqual(observed['reservation']['issue_number'], 12)
            self.assertEqual(observed['ticket_work']['frontier']['active'], [12])
            self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Ready')
            self.refused(self.command(), 'claim_conflict')

    def test_concurrent_prepare_and_reserve_share_same_ticket(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            prepare = pool.submit(self.command)
            reserve = pool.submit(self.tracker.control, 'reserve-ticket', issue=10)
            prepared, reserved = self.ok(prepare.result()), self.ok(reserve.result())
        self.assertEqual(reserved['reservation']['issue_number'], 10)
        observed = self.tracker.inspect()
        self.assertEqual(observed['ticket_work']['frontier']['active'], [10])
        self.assertEqual(observed['reservation'], reserved['reservation'])
        self.assertEqual(self.ok(self.inspect(prepared))['ticket_scope']['issue_number'], 10)
        self.assertEqual(self.ok(self.command())['assignment_id'], prepared['assignment_id'])

    def test_concurrent_claims_admit_exactly_one_ticket(self):
        other = self.configuration(number=12)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda request: self.command(request=request), [self.request, other]))
        self.assertEqual(sorted(r.returncode for r in responses), [0, 2])
        denied = next(r for r in responses if r.returncode == 2)
        self.assertEqual(json.loads(denied.stderr)['error'], 'claim_conflict')
        self.assert_no_external_writes()

    def test_stage_bindings_precise_closures_and_read_only_review_profiles(self):
        expected = {'implementation': ['implement'], 'corrections': ['implement'], 'simplify': ['simplify-code'],
                    'review-standards': ['code-review'], 'review-spec': ['code-review'],
                    'diagnosis': ['diagnosing-bugs'], 'repair': ['diagnosing-bugs', 'implement']}
        for stage, entry in expected.items():
            request = self.configuration(stage=stage)
            prepared = self.ok(self.command(request=request, dry_run=True))
            self.assertEqual(prepared['handoff']['skill_entry_points'], entry)
            if stage in {'review-standards', 'review-spec', 'diagnosis'}:
                self.assertNotIn('write_workspace', prepared['handoff']['capabilities'])
            if stage != 'implementation':
                request['preceding'] = []
                self.refused(self.command(request=request, dry_run=True), 'invalid_assignment')
        self.assert_no_external_writes()

    def test_changed_requirements_or_issue_revision_never_reuse_old_claim(self):
        prepared = self.ok(self.command())
        self.tracker.issues[10]['body'] += '\nMaterial changed requirement'
        self.refused(self.command(), 'scope_mismatch')
        new_request = self.configuration()
        self.refused(self.command(request=new_request), 'claim_conflict')
        self.mutate_iteration(lambda item: item['handoff'].update(commit='d' * 40))
        self.refused(self.inspect(prepared), 'stale_assignment')

    def test_results_are_structurally_stored_but_never_authorize_advancement_or_closure(self):
        assignment = self.ok(self.command())
        request = self.result(assignment)
        stored = self.ok(self.command('assignment-result', request, assignment['assignment_id']))
        self.assertTrue(stored['result_disposition']['structurally_valid'])
        self.assertFalse(stored['result_disposition']['trusted_execution'])
        self.assertFalse(stored['result_disposition']['advance_allowed'])
        self.assertFalse(stored['result_disposition']['close_allowed'])
        self.assertFalse(stored['execution_allowed'])
        self.assertEqual(self.ok(self.inspect(assignment))['submitted_result'], request)
        self.ok(self.command('assignment-result', request, assignment['assignment_id']))
        request['work'] = ['Changed result under same assignment']
        self.refused(self.command('assignment-result', request, assignment['assignment_id']), 'result_conflict')
        self.assertEqual(self.tracker.inspect()['stage'], self.item['stage'])
        self.assert_no_external_writes()

    def test_success_assertions_missing_artifacts_wrong_loads_or_result_scope_refused(self):
        assignment = self.ok(self.command())
        self.refused(self.command('assignment-result', {'status': 'done'}, assignment['assignment_id']), 'invalid_result')
        for key, value in [('assignment_id', 'wrong'), ('handoff_digest', '0' * 64), ('claim_id', 'wrong'),
                           ('loads', []), ('work', []), ('artifacts', []), ('tests', []), ('status', 'success')]:
            request = self.result(assignment)
            request[key] = value
            self.refused(self.command('assignment-result', request, assignment['assignment_id']), 'invalid_result')
        request = self.result(assignment)
        request['loads'][0]['sha256'] = '0' * 64
        self.refused(self.command('assignment-result', request, assignment['assignment_id']), 'invalid_result')
        self.assertNotIn('submitted_result', self.ok(self.inspect(assignment)))
        self.assert_no_external_writes()

    def test_result_external_scope_drift_and_changed_dependencies_refused(self):
        assignment = self.ok(self.command())
        request = self.result(assignment)
        self.tracker.issues[10]['body'] += '\nChanged scope'
        self.refused(self.command('assignment-result', request, assignment['assignment_id']), 'scope_mismatch')
        dependency = self.skills / 'implement/SKILL.md'
        dependency.write_text('changed source input')
        self.refused(self.command('assignment-result', request, assignment['assignment_id']), 'skill_blocked')
        # The immutable handoff remains inspectable even when installed sources change.
        self.assertNotIn('submitted_result', self.ok(self.inspect(assignment)))

    def test_pause_retains_inspection_but_refuses_prepare_and_result(self):
        assignment = self.ok(self.command())
        self.mutate_iteration(lambda item: item.update(status='paused'))
        self.assertEqual(self.ok(self.inspect(assignment))['handoff_digest'], assignment['handoff_digest'])
        self.refused(self.command(), 'iteration_paused')
        self.refused(self.command('assignment-result', self.result(assignment), assignment['assignment_id']), 'iteration_paused')

    def test_missing_allowance_paid_approval_and_uncertain_model_operation_refuse(self):
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('UPDATE model_grants SET kind=?', ('approval',))
        self.refused(self.command(), 'spending_blocked')
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('DELETE FROM model_grants')
        self.refused(self.command(), 'spending_blocked')
        self.grant()
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('INSERT INTO model_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('uncertain-fixture', 'product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses',
                        None, 1, 'pending', '0' * 64, None, int(time.time()), None))
        self.refused(self.command(), 'outcome_uncertain')
        self.assertEqual(self.fixture.calls, [])

    def test_existing_ticket_reservations_are_reused_not_overwritten_or_stolen(self):
        reserved = self.tracker.ok(self.tracker.control('reserve-ticket', issue=10))
        self.fixture.calls.clear()
        assignment = self.ok(self.command())
        self.assertEqual(self.tracker.inspect()['reservation'], reserved['reservation'])
        self.mutate_iteration(lambda item: item['reservation'].update(status='invalidated'))
        self.refused(self.command(), 'claim_conflict')
        self.assertEqual(self.ok(self.inspect(assignment))['claim_id'], self.request['claim_id'])
        self.assert_no_external_writes()

    def test_malformed_json_and_nested_shapes_are_structured_startup_refusals(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"stage":"implementation","stage":"diagnosis"}')
        response = self.fixture.cli('prepare-assignment', '--project', 'product', '--iteration', 'm1',
                                    '--api-base', self.fixture.base, '--request', str(path))
        self.refused(response, 'invalid_assignment')
        for key in ['issue', 'profile', 'skills', 'standards', 'preceding', 'stage']:
            for value in (None, [], {}, 3):
                if value == [] and key == 'preceding':
                    continue
                request = copy.deepcopy(self.request)
                request[key] = value
                response = self.command(request=request)
                self.assertEqual(response.returncode, 2, response.stderr)
                self.assertIn('error', json.loads(response.stderr))
        self.assertEqual(self.fixture.calls, [])

    def test_assigned_review_axes_cannot_share_a_top_level_profile(self):
        standards = self.configuration(stage='review-standards')
        assignment = self.ok(self.command(request=standards))
        spec = self.configuration(stage='review-spec')
        spec['profile']['home'] = standards['profile']['home']
        self.refused(self.command(request=spec), 'profile_claim_conflict')
        spec = self.configuration(stage='review-spec')
        other = self.ok(self.command(request=spec))
        self.assertNotEqual(assignment['assignment_id'], other['assignment_id'])
        self.assertNotIn('write_workspace', other['handoff']['capabilities'])
        self.assert_no_external_writes()

    def test_public_controls_refuse_wrong_operator_and_unknown_assignment(self):
        assignment = self.ok(self.command())
        response = self.fixture.cli('--operator-id', 'other', 'inspect-assignment', '--project', 'product',
                                    '--iteration', 'm1', '--assignment', assignment['assignment_id'])
        self.refused(response, 'approval_required')
        self.refused(self.command('inspect-assignment', assignment='unknown'), 'not_found')
        self.refused(self.command('assignment-result', self.result(assignment), 'unknown'), 'not_found')

    def test_concurrent_attention_state_is_not_overwritten_by_assignment(self):
        event = {'event_id': 'fixture-attention', 'kind': 'incident', 'message': 'Labeled fixture incident',
                 'issue_number': 10, 'run_id': 'fixture-run', 'evidence_ids': ['fixture-evidence']}
        path = self.root / 'attention.json'
        path.write_text(json.dumps(event))
        with ThreadPoolExecutor(max_workers=2) as pool:
            assignment = pool.submit(self.command)
            attention = pool.submit(self.fixture.cli, 'attention', '--project', 'product', '--iteration', 'm1', '--request', str(path))
            self.ok(assignment.result())
            self.ok(attention.result())
        notifications = self.ok(self.fixture.cli('notifications', '--project', 'product', '--iteration', 'm1'))
        self.assertIn('fixture-attention', json.dumps(notifications))
        self.assertEqual(self.tracker.inspect()['ticket_work'], self.item['ticket_work'])


if __name__ == '__main__':
    unittest.main()
