"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import copy
from contextlib import contextmanager
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from factory_v1 import diagnosis
from factory_v1.repositories import RepositoryError
from factory_v1.tests import test_sandbox



@contextmanager
def component_case(cls):
    case = cls(methodName='runTest')
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()

class ReportPolicyTests(unittest.TestCase):
    def setUp(self):
        self.pins = {'stuck_assignment': 'a' * 64, 'tree_sha256': 'b' * 64}
        self.contract = {'pins': self.pins}
        self.command = {'command': 'python symptom.py', 'result': 'LABELED mismatch, exit_code=1'}
        self.report = {'contract': diagnosis.CONTRACT, 'adaptation': 'read-only-adaptation', 'pins': self.pins,
                       'feedback_loop': {**self.command, 'symptom': 'Required greeting mismatch'},
                       'commands': [self.command], 'hypotheses': [
                           {'hypothesis': 'Wrong literal', 'prediction': 'Exact output assertion is red', 'evidence': 'Literal differs'}],
                       'attempted_repairs': ['Responsible attempted literal repair, still red'],
                       'findings': ['Source literal and observed mismatch'], 'cause_or_uncertainty': 'Wrong literal or hidden transform',
                       'directions': [{'id': 'retry', 'direction': 'Retry original requirement', 'tradeoff': 'More investigation'}],
                       'recommendation': 'retry', 'blockers': []}

    def validate(self, report):
        diagnosis.validate_report(report, self.contract, 'read-only-adaptation', [self.command])

    def reject(self, report):
        with self.assertRaises(RepositoryError) as raised:
            self.validate(report)
        self.assertEqual(raised.exception.code, 'invalid_result')

    def test_diagnosis_report_shape_and_identity_cases(self):
        cases = [('valid', {}, None)]
        cases += [('missing-' + field, {}, field) for field in sorted(diagnosis.REPORT_FIELDS)]
        cases += [(name, change, None) for name, change in [
            ('contract', {'contract': 'made-up'}), ('pin', {'pins': {**self.pins, 'tree_sha256': '0' * 64}}),
            ('adaptation', {'adaptation': 'write-fix'}), ('extra', {'accepted_fix': True}),
            ('attempts-empty', {'attempted_repairs': []}), ('findings-empty', {'findings': []}),
            ('uncertainty-empty', {'cause_or_uncertainty': ''}), ('commands-type', {'commands': None}),
            ('blockers-type', {'blockers': 'missing'}), ('attempt-type', {'attempted_repairs': [None]})]]
        for name, change, omitted in cases:
            with self.subTest(case=name):
                report = copy.deepcopy(self.report)
                report.update(change)
                if omitted is not None:
                    del report[omitted]
                if name == 'valid':
                    self.validate(report)
                else:
                    self.reject(report)



    def test_diagnosis_feedback_loop_cases(self):
        unavailable = {'feedback_loop': None, 'hypotheses': [], 'blockers': ['Missing environment / HITL template']}
        cases = [('unavailable', unavailable, True),
                 ('unexplained', {**unavailable, 'blockers': []}, False),
                 ('unsupported-theory', {**unavailable, 'hypotheses': self.report['hypotheses']}, False)]
        cases += [(name, change, False) for name, change in [
            ('commands-empty', {'commands': []}), ('invented-command', {'commands': [{'command': 'invented', 'result': 'pass'}]}),
            ('invented-result', {'feedback_loop': {**self.report['feedback_loop'], 'result': 'invented green'}}),
            ('empty-symptom', {'feedback_loop': {**self.report['feedback_loop'], 'symptom': ''}}),
            ('missing-symptom', {'feedback_loop': self.command}), ('missing-hypotheses', {'hypotheses': []})]]
        cases += [('hypothesis-' + str(i), {'hypotheses': [value]}, False) for i, value in enumerate((
            {'hypothesis': 'theory'}, {'hypothesis': 'theory', 'prediction': '', 'evidence': 'none'}, 'theory'))]
        for name, change, accepted in cases:
            with self.subTest(case=name):
                report = copy.deepcopy(self.report)
                report.update(copy.deepcopy(change))
                if accepted:
                    self.validate(report)
                else:
                    self.reject(report)



    def test_recommendation_names_one_unique_direction_not_approval(self):
        for change in ({'recommendation': 'accept-fix'}, {'directions': []},
                       {'directions': self.report['directions'] * 2},
                       {'directions': [{'id': 'retry', 'direction': 'relax requirements'}]}):
            self.reject({**self.report, **change})


    def test_malformed_duplicate_and_oversized_report_json_refuse(self):
        for raw in ('{"pins":1,"pins":2}', '{bad}', '[]', '{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}', 'x' * 16_000_001):
            with self.assertRaises(RepositoryError):
                parsed = diagnosis.simplification.object_json(raw, 'invalid_result')
                self.validate(parsed)

    def test_claim_retirement_requires_durable_verified_hold_and_no_live_worker(self):
        scope = {'project': 'p', 'iteration': 'i'}
        assignment = {'assignment_id': 'a', 'ticket_scope': scope, 'stuck_disposition': 'held',
                      'runtime': {'status': 'complete', 'container_removed': True}}
        record = {'blocks_verified': True, 'retired_assignments': ['a']}
        with closing(sqlite3.connect(':memory:')) as database:
            database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
            def stored(value):
                database.execute('DELETE FROM iterations')
                database.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps({'stuck_work': {'s': value}})))
            stored(record)
            self.assertTrue(diagnosis.released_claim(database, assignment))
            self.assertTrue(diagnosis.released_claim(database, {k: v for k, v in assignment.items() if k != 'runtime'}))
            for status in ('preparing', 'running', 'unconfirmed'):
                self.assertFalse(diagnosis.released_claim(database, {**assignment, 'runtime': {'status': status}}))
            for removed in (False, None):
                self.assertFalse(diagnosis.released_claim(database, {**assignment, 'runtime': {'status': 'complete', 'container_removed': removed}}))
            stored({**record, 'blocks_verified': False})
            self.assertFalse(diagnosis.released_claim(database, assignment))
            stored({**record, 'retired_assignments': []})
            self.assertFalse(diagnosis.released_claim(database, assignment))

    def test_debug_replacement_requires_terminal_confirmed_failure_not_completed_report(self):
        record = {'pins': self.pins, 'status': 'diagnosis', 'diagnosis_assignment': 'one'}
        axis = {'assignment_id': 'one', 'handoff': {'diagnosis': {'pins': self.pins}}}
        with closing(sqlite3.connect(':memory:')) as database:
            database.execute('CREATE TABLE role_assignments (assignment_id TEXT, payload TEXT)')
            def stored(runtime):
                database.execute('DELETE FROM role_assignments')
                database.execute('INSERT INTO role_assignments VALUES (?,?)', ('one', json.dumps({**axis, 'runtime': runtime})))
            for status in ('complete', 'running', 'preparing', 'unconfirmed'):
                stored({'status': status, 'container_removed': True})
                with self.assertRaises(RepositoryError):
                    diagnosis.debug_claim(database, record, 'new')
                diagnosis.debug_claim(database, record, 'one')
            for status in ('failed', 'stopped'):
                stored({'status': status, 'container_removed': False})
                with self.assertRaises(RepositoryError):
                    diagnosis.debug_claim(database, record, 'new')
                stored({'status': status, 'container_removed': True})
                diagnosis.debug_claim(database, record, 'new')
            with self.assertRaises(RepositoryError):
                diagnosis.debug_claim(database, {**record, 'status': 'awaiting-direction'}, 'new')

    def test_retained_json_terminal_response_is_matched_verbatim_not_reformatted(self):
        from factory_v1.sandbox import check_terminal_results

        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            root = Path(directory)
            (root / 'scratch').mkdir()
            output = '{"output": "LABELED AssertionError\\n", "exit_code": 1}'
            command = 'python symptom.py && python other.py'
            conversation = {'messages': [
                {'role': 'assistant', 'tool_calls': [{'id': 'repro', 'function': {
                    'name': 'terminal', 'arguments': json.dumps({'command': command})}}]},
                {'role': 'tool', 'tool_call_id': 'repro', 'content': output}]}
            path = root / 'scratch/conversation.json'
            path.write_text(json.dumps(conversation))
            check_terminal_results(root, [{'command': command, 'result': output}], verbatim=True)
            for changed in ('ALL PASSED; exit_code=0', 'LABELED AssertionError; exit_code=1',
                            json.dumps(json.loads(output), separators=(',', ':')), output + '\n'):
                with self.subTest(result=changed), self.assertRaises(RepositoryError) as refusal:
                    check_terminal_results(root, [{'command': command, 'result': changed}], verbatim=True)
                self.assertEqual(refusal.exception.code, 'invalid_result')
            with self.assertRaises(RepositoryError):
                check_terminal_results(root, [{'command': 'python symptom.py', 'result': output}], verbatim=True)
            # The retained response must be correlated to a terminal call, not arbitrary prose.
            conversation['messages'][1]['tool_call_id'] = 'unrelated'
            path.write_text(json.dumps(conversation))
            with self.assertRaises(RepositoryError):
                check_terminal_results(root, [{'command': command, 'result': output}], verbatim=True)

    def test_readonly_report_validation_retains_red_evidence_and_refuses_source_or_baseline_drift(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            root = Path(directory)
            workspace, baseline = root / 'workspace', root / 'inputs/baseline'
            workspace.mkdir()
            baseline.mkdir(parents=True)
            (workspace / 'hello.py').write_text('print("wrong greeting")\n')
            (baseline / 'hello.py').write_text('print("baseline greeting")\n')
            pins = {**self.pins, 'tree_sha256': diagnosis.assignments.digest(diagnosis.manifest(workspace)),
                    'baseline_sha256': diagnosis.assignments.digest(diagnosis.manifest(baseline))}
            report = {**self.report, 'pins': pins}
            assignment = {'runtime': {'artifacts': directory},
                          'handoff': {'adaptation': 'read-only-adaptation', 'diagnosis': {'pins': pins}}}
            result = {'artifacts': [diagnosis.simplification.artifact('diagnosis-report', json.dumps(report))],
                      'status': 'done', 'tests': [self.command]}
            scratch = root / 'scratch'
            scratch.mkdir()
            conversation = {'messages': [
                {'role': 'assistant', 'tool_calls': [{'id': 'repro', 'function': {
                    'name': 'terminal', 'arguments': json.dumps({'command': self.command['command']})}}]},
                {'role': 'tool', 'tool_call_id': 'repro', 'content': self.command['result']}]}
            (scratch / 'conversation.json').write_text(json.dumps(conversation))
            validated = diagnosis.validate_result(assignment, result)
            forged_command = {**self.command, 'result': 'ALL PASSED\nexit_code=0'}
            forged_report = {**report, 'commands': [forged_command],
                             'feedback_loop': {**report['feedback_loop'], 'result': forged_command['result']}}
            forged = {**result, 'tests': [forged_command],
                      'artifacts': [diagnosis.simplification.artifact('diagnosis-report', json.dumps(forged_report))]}
            with self.assertRaises(RepositoryError) as refusal:
                diagnosis.validate_result(assignment, forged)
            self.assertEqual(refusal.exception.code, 'invalid_result')
            self.assertFalse(validated['native_execution_trusted'])
            self.assertFalse(validated['advance_allowed'])
            self.assertFalse(validated['close_allowed'])
            with self.assertRaises(RepositoryError):
                diagnosis.validate_result(assignment, {**result, 'status': 'stuck'})
            (workspace / 'hello.py').write_text('forged fix')
            with self.assertRaises(RepositoryError):
                diagnosis.validate_result(assignment, result)
            (workspace / 'hello.py').write_text('print("wrong greeting")\n')
            (baseline / 'hello.py').write_text('forged baseline')
            with self.assertRaises(RepositoryError):
                diagnosis.validate_result(assignment, result)

    def test_only_explicit_structured_operator_directions_can_be_recorded(self):
        for action in ('retry', 'revise'):
            value = {'action': action, 'direction': 'Explicit original-scope direction'}
            self.assertEqual(diagnosis.operator_direction(json.dumps(value)), value)
        for content in ('looks good', '{"action":"retry"}',
                        '{"action":"retry","direction":""}',
                        '{"action":"merge","direction":"accept unverified fix"}',
                        '{"action":[],"direction":"retry"}',
                        '{"action":"retry","direction":"one","direction":"two"}'):
            with self.assertRaises(RepositoryError):
                diagnosis.operator_direction(content)

    def test_frontier_holds_only_affected_unreleased_work(self):
        item = {'stuck_work': {'one': {'status': 'awaiting-direction', 'affected': [10, 11]},
                               'two': {'status': 'released', 'affected': [12]},
                               'three': {'status': 'revision-required', 'affected': [13]}}}
        self.assertEqual(diagnosis.held_numbers(item), {10, 11, 13})


class DiagnosisLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.case = test_sandbox.SandboxTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.assignment = self.case.assignment
        self.tracker = self.assignment.tracker
        self.fixture = self.assignment.fixture
        self.root = self.case.root

    def command(self, name, request, config=None, dry=False):
        path = self.root / ('diagnosis-' + name + '.json')
        path.write_text(json.dumps(request))
        args = [name, '--project', 'product', '--iteration', 'm1', '--request', str(path), '--api-base', self.fixture.base]
        if config:
            args += ['--diagnosis-config', str(config)]
        if dry:
            args += ['--dry-run']
        return self.fixture.cli(*args)

    def stuck(self):
        self.assignment.ok(self.tracker.control('reserve-ticket', issue=10))
        prepared = self.assignment.ok(self.assignment.command())
        self.case.mode('implementation-stuck')
        result = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(result['runtime']['status'], 'complete', result)
        self.assertEqual(result['submitted_result']['status'], 'stuck')
        self.assertFalse(result['result_disposition']['checks_verified'])
        self.identity = {'stuck_assignment': result['assignment_id']}
        return result

    def debug_request(self):
        config = self.assignment.configuration(stage='diagnosis')
        return {**self.identity, **{k: config[k] for k in ('profile', 'skills', 'claim_id')}}

    def authorization(self, axis):
        path = self.root / 'host-debug-authorization.json'
        path.write_text(json.dumps({'operator_id': '42',
            'execution_reference': 'LABELED deterministic host verification, NOT live native authority',
            'diagnosis': diagnosis.execution_binding(axis)}))
        path.chmod(0o600)
        return path

    def respond(self, event, action='retry', **change):
        response = {'event_id': event['event']['event_id'], 'decision_id': event['event']['decision_id'],
                    'operator_id': '42', 'origin': event['destination'], 'reference': 'LABELED authenticated operator fixture',
                    'response': json.dumps({'action': action, 'direction': 'LABELED operator: keep original requirements; investigate greeting'})}
        response.update(change)
        path = self.root / 'operator-response.json'
        path.write_text(json.dumps(response))
        return self.fixture.cli('respond', '--project', 'product', '--iteration', 'm1', '--request', str(path))

from unittest.mock import Mock, patch
from factory_v1.tests import test_simplification


class DiagnosisComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        evidence = self.root / 'responsible'
        evidence.mkdir(mode=0o700)
        self.e = test_simplification.LocalStageEvidence(evidence)
        self.prior = self.e.assignment
        self.prior['handoff'].update(stage='implementation', repository='example/product',
            repository_id=123, workspace=str(evidence / 'workspace'), standards=[],
            profile={'name': 'responsible', 'home': str(self.root / 'responsible-home')})
        self.prior['handoff']['issue'].update(number=10, id=110, node_id='ISSUE10')
        self.e.seal_handoff()
        declaration = {'reason': 'Local explicit responsible stuckness',
            'attempted_fixes': [{'change': 'Literal repair', 'command': 'python symptom.py', 'result': 'Local red output'}],
            'findings': ['Local unresolved mismatch']}
        self.prior['submitted_result'] = {'status': 'stuck', 'run_id': self.e.runtime['run_id'],
            'artifacts': [diagnosis.simplification.artifact('stuck-declaration', json.dumps(declaration))],
            'tests': [{'command': 'python symptom.py', 'result': 'Local red output'}]}
        self.prior['result_disposition'] = {'isolated_execution': True}
        (evidence / 'scratch').mkdir()
        (evidence / 'scratch/result.json').write_text(json.dumps(self.prior['submitted_result']))
        self.database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.database.close)
        self.database.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                                   'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
            'repository': 'example/product', 'approval': {'operator_id': '42', 'reference': 'Local diagnosis fixture'},
            'repository_onboarding': {'metadata': {'id': 123}, 'api_base': 'https://api.github.com'},
            'ticket_work': {'board': {'project_id': 'BOARD', 'status': {'id': 'STATUS', 'options': {'Ready': 'READY', 'Blocked': 'BLOCKED'}}},
                            'request': {'tracker': {'statuses': {'ready': 'Ready', 'blocked': 'Blocked'}}}}}
        self.database.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
        self.store(self.prior)
        self.rows = [{'number': n, 'id': 100+n, 'node_id': 'ISSUE'+str(n), 'membership_id': 'MEM'+str(n),
                      'issue_state': 'open', 'status': 'ready', 'blockers': blockers}
                     for n, blockers in [(10, []), (11, [10]), (12, []), (13, [11])]]
        self.github = Mock(base='https://api.github.com')
        # Only external board/issue reads and status writes are simulated.
        self.enterContext(patch.object(diagnosis.tickets, 'frontier', side_effect=self.frontier))
        self.enterContext(patch.object(diagnosis.tickets.gh, 'read_issue', side_effect=self.read_issue))
        self.set_field = self.enterContext(patch.object(diagnosis.tickets.gh, 'set_field', side_effect=self.change_status))

    def store(self, row):
        self.database.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.database.commit()

    def frontier(self, database, item, github):
        observed = copy.deepcopy(item)
        observed['ticket_work']['frontier'] = {'items': copy.deepcopy(self.rows)}
        return observed

    def read_issue(self, github, repository, number):
        return {'body': 'Local issue ' + str(number), 'state': 'open', 'id': 100+number, 'node_id': 'ISSUE'+str(number)}

    def change_status(self, github, project, member, field, value):
        row = next(r for r in self.rows if r['membership_id'] == member)
        row['status'] = 'ready' if value == 'READY' else 'blocked'

    def declare(self):
        # The handoff body pin binds the actual external fixture body, not a self-validating constant.
        self.prior['handoff']['issue']['body_sha256'] = diagnosis.hashlib.sha256(b'Local issue 10').hexdigest()
        self.prior['handoff_digest'] = diagnosis.assignments.digest(self.prior['handoff'])
        self.store(self.prior)
        return diagnosis.declare(self.database, 'product', 'm1', '42', {'stuck_assignment': self.prior['assignment_id']}, self.github)

    def seed_declaration(self):
        self.prior['handoff']['issue']['body_sha256'] = diagnosis.hashlib.sha256(b'Local issue 10').hexdigest()
        self.prior['handoff_digest'] = diagnosis.assignments.digest(self.prior['handoff'])
        result = self.prior['submitted_result']
        artifact = result['artifacts'][0]
        pins = {'stuck_assignment': self.prior['assignment_id'], 'stuck_run': self.prior['runtime']['run_id'],
            'handoff_digest': self.prior['handoff_digest'], 'result_sha256': diagnosis.assignments.digest(result),
            'declaration_sha256': artifact['sha256'], 'tree_sha256': diagnosis.assignments.digest(self.e.initial),
            'baseline_sha256': diagnosis.assignments.digest(self.e.baseline),
            'issue_sha256': self.prior['handoff']['issue']['body_sha256'], 'spec_commit': self.prior['handoff']['spec_commit']}
        record = {'status': 'diagnosis', 'sequence': 1, 'pins': pins,
            'declaration': json.loads(artifact['content']), 'affected': [10, 11, 13],
            'issues': {str(n): {'id': 100+n, 'node_id': 'ISSUE'+str(n),
                'body_sha256': diagnosis.hashlib.sha256(('Local issue '+str(n)).encode()).hexdigest()} for n in (10, 11, 13)},
            'previous_statuses': {str(n): 'ready' for n in (10, 11, 13)},
            'retired_assignments': [self.prior['assignment_id']], 'blocks_verified': True, 'hold_representation': 'blocked-option',
            'diagnosis_assignment': None, 'event_id': None, 'applied_response': None}
        self.prior['stuck_disposition'] = 'held'
        self.store(self.prior)
        for row in self.rows:
            if row['number'] in record['affected']:
                row['status'] = 'blocked'
        item = diagnosis.assignments.current(self.database, 'product', 'm1', '42')
        item['stuck_work'] = {self.prior['assignment_id']: record}
        diagnosis.tickets.checkpoint(self.database, item)
        return record

    def test_responsible_stuck_source_retains_attempts_and_refuses_crash_or_forgery(self):
        good = copy.deepcopy(self.prior)
        diagnosis.source(self.database, 'product', 'm1', '42', good['assignment_id'])
        for group, field, value in [('runtime', 'status', 'failed'), ('runtime', 'container_removed', False),
            ('submitted_result', 'status', 'done'), ('result_disposition', 'isolated_execution', False)]:
            row = copy.deepcopy(good)
            row[group][field] = value
            self.store(row)
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    diagnosis.source(self.database, 'product', 'm1', '42', row['assignment_id'])
                self.assertEqual(refusal.exception.code, 'stuck_required')
        self.store(good)
        (self.e.root / 'scratch/result.json').write_text('{}')
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.source(self.database, 'product', 'm1', '42', good['assignment_id'])
        self.assertEqual(refusal.exception.code, 'stale_assignment')

    def test_declaration_block_readback_ownership_cases(self):
        for lost_effect in (False, True):
            with self.subTest(lost_effect=lost_effect), component_case(DiagnosisComponentTests) as case:
                if lost_effect:
                    case.set_field.side_effect = None
                    with self.assertRaises(RepositoryError) as refusal:
                        case.declare()
                    self.assertEqual(refusal.exception.code, 'publication_mismatch')
                    self.assertEqual(case.set_field.call_count, 1)
                    self.assertEqual({r['status'] for r in case.rows}, {'ready'})
                    with closing(sqlite3.connect(case.root / 'state.sqlite')) as restarted:
                        item = diagnosis.assignments.current(restarted, 'product', 'm1', '42')
                        held = diagnosis.assignments.rows(restarted)[0]
                        record = item['stuck_work'][held['assignment_id']]
                        self.assertEqual(record['status'], 'blocking')
                        self.assertFalse(record['blocks_verified'])
                        self.assertEqual(record['retired_assignments'], [held['assignment_id']])
                        self.assertEqual(held['stuck_disposition'], 'held')
                        self.assertFalse(diagnosis.released_claim(restarted, held))
                        self.assertEqual(diagnosis.held_numbers(item), {10, 11, 13})
                    case.set_field.side_effect = case.change_status
                    record = diagnosis.declare(case.database, 'product', 'm1', '42',
                        {'stuck_assignment': case.prior['assignment_id']}, case.github)
                    self.assertEqual(case.set_field.call_count, 4)
                else:
                    record = case.declare()
                self.assertEqual(record['affected'], [10, 11, 13])
                self.assertTrue(record['blocks_verified'])
                self.assertEqual(record['status'], 'diagnosis')
                self.assertEqual(case.rows[2]['status'], 'ready')
                self.assertEqual([r['status'] for r in case.rows if r['number'] != 12], ['blocked'] * 3)
                held = diagnosis.assignments.rows(case.database)[0]
                self.assertEqual(held['stuck_disposition'], 'held')
                self.assertTrue(diagnosis.released_claim(case.database, held))
                if not lost_effect:
                    writes = case.set_field.call_count
                    self.assertEqual(diagnosis.declare(case.database, 'product', 'm1', '42',
                        {'stuck_assignment': held['assignment_id']}, case.github), record)
                    self.assertEqual(case.set_field.call_count, writes)
                with closing(sqlite3.connect(case.root / 'state.sqlite')) as restarted:
                    self.assertEqual(diagnosis.assignments.current(restarted, 'product', 'm1', '42')['stuck_work'][held['assignment_id']], record)

    def test_exact_independent_handoff_and_real_staging(self):
        record = self.seed_declaration()
        request = copy.deepcopy(self.prior['handoff'])
        request['preceding'] = diagnosis.preceding(record, record['declaration'], self.prior)
        contract = diagnosis.handoff(self.database, 'product', 'm1', '42', request)
        self.assertTrue(contract['read_only'])
        self.assertFalse(contract['native_execution_trusted'])
        destination = self.root / 'debug'
        destination.mkdir()
        (destination / 'workspace').mkdir()
        pins = {'baseline': self.e.baseline}
        diagnosis.copy_candidate(self.database, 'product', 'm1', '42', {'handoff': {'diagnosis': contract}}, destination, pins)
        self.assertEqual(diagnosis.manifest(destination / 'workspace'), self.e.initial)
        request['baseline'] = 'f' * 40
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.handoff(self.database, 'product', 'm1', '42', request)
        self.assertEqual(refusal.exception.code, 'scope_mismatch')

    def test_overlapping_release_preserves_other_hold_and_exact_issue_body(self):
        record = self.seed_declaration()
        item = diagnosis.assignments.current(self.database, 'product', 'm1', '42')
        item['stuck_work']['other'] = {'pins': {'other': True}, 'status': 'awaiting-direction', 'affected': [11]}
        released = diagnosis.sync_blocks(self.database, item, record, self.github, release=True)
        statuses = {r['number']: r['status'] for r in released['ticket_work']['frontier']['items']}
        self.assertEqual(statuses, {10: 'ready', 11: 'blocked', 12: 'ready', 13: 'ready'})
        record['issues']['11']['body_sha256'] = '0' * 64
        writes = self.set_field.call_count
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.sync_blocks(self.database, item, record, self.github, release=True)
        self.assertEqual(refusal.exception.code, 'scope_mismatch')
        self.assertEqual(self.set_field.call_count, writes)

    def direction_inputs(self, action='retry'):
        record = self.seed_declaration()
        root = self.root / 'independent-debug'
        root.mkdir(mode=0o700)
        e = test_simplification.LocalStageEvidence(root)
        axis = copy.deepcopy(self.prior)
        axis.update(assignment_id='b' * 64, claim_id='independent-debug', runtime=e.runtime)
        axis['handoff'].update(stage='diagnosis', diagnosis={'pins': record['pins']}, skills=[],
            profile={'name': 'independent-debug', 'home': str(self.root / 'debug-home')},
            adaptation='LABELED independent diagnosis input')
        axis['handoff_digest'] = diagnosis.assignments.digest(axis['handoff'])
        axis['submitted_result'] = {'loads': [], 'work': ['LABELED read-only investigation']}
        axis['diagnosis_result'] = {'report_sha256': 'c' * 64}
        files = ['scratch/result.json', 'scratch/loads.json', 'scratch/events.jsonl', 'scratch/conversation.json',
            'scratch/probes.json', 'inputs/worker.py', 'inputs/sandbox_relay.py', 'inputs/assignment.json',
            'inputs/handoff.json', 'container-inspection.json', 'launch-command.json', 'source-artifacts.json',
            'worker-reported-tests.json', 'model-evidence/response-local.json']
        for name in files:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('LABELED execution-binding input; not native execution\n')
        self.store(axis)
        item = diagnosis.assignments.current(self.database, 'product', 'm1', '42')
        record = item['stuck_work'][self.prior['assignment_id']]
        record.update(status='awaiting-direction', event_id='direction-event', diagnosis_assignment=axis['assignment_id'],
                      authorization={'diagnosis': diagnosis.execution_binding(axis)})
        diagnosis.tickets.checkpoint(self.database, item)
        origin = {'platform': 'telegram', 'chat_id': '42'}
        response = {'event_id': 'direction-event', 'decision_id': 'direction-event', 'operator_id': '42',
            'origin': origin, 'reference': 'Local authenticated operator input',
            'response': json.dumps({'action': action, 'direction': 'Preserve original scope; investigate greeting'})}
        notification = {'event': {'event_id': 'direction-event', 'decision_id': 'direction-event', 'kind': 'decision'},
                        'destination': origin, 'decision_response': response}
        # Completed native diagnosis and authenticated transport retrieval are prerequisites,
        # not the direction/release/retirement invariant. Bindings and response validation remain real.
        self.enterContext(patch.object(diagnosis, 'completed', return_value=axis))
        self.enterContext(patch.object(diagnosis.attention, 'get_record', return_value=notification))
        return {'stuck_assignment': self.prior['assignment_id'], 'event_id': 'direction-event'}, notification

    def test_correlated_direction_application_cases(self):
        cases = [('silence', 'decision_required'), ('unrelated', 'invalid_decision'),
                 ('revise', None), ('replay', None), ('conflicting-replay', 'decision_conflict')]
        for mode, code in cases:
            with self.subTest(mode=mode), component_case(DiagnosisComponentTests) as case:
                request, notification = case.direction_inputs('revise')
                response = copy.deepcopy(notification['decision_response'])
                if mode in ('replay', 'conflicting-replay'):
                    item = diagnosis.assignments.current(case.database, 'product', 'm1', '42')
                    record = item['stuck_work'][case.prior['assignment_id']]
                    record.update(status='revision-required', applied_response=response,
                        direction=diagnosis.operator_direction(response['response']))
                    diagnosis.tickets.checkpoint(case.database, item)
                if mode == 'silence':
                    notification['decision_response'] = None
                elif mode == 'unrelated':
                    request['event_id'] = 'unrelated'
                    notification['decision_response'].update(event_id='unrelated', decision_id='unrelated')
                    notification['event'].update(event_id='unrelated', decision_id='unrelated')
                elif mode == 'conflicting-replay':
                    notification['decision_response']['response'] = json.dumps({'action': 'retry', 'direction': 'Changed scope'})
                before = case.durable_snapshot()
                with patch.object(diagnosis.attention, 'get_record', wraps=diagnosis.attention.get_record) as read:
                    if code:
                        with self.assertRaises(RepositoryError) as refusal:
                            diagnosis.apply_direction(case.database, 'product', 'm1', '42', request, case.github)
                        self.assertEqual(refusal.exception.code, code)
                        self.assertEqual(case.durable_snapshot(), before)
                    else:
                        revised = diagnosis.apply_direction(case.database, 'product', 'm1', '42', request, case.github)
                        self.assertEqual(revised['status'], 'revision-required')
                        self.assertEqual(revised['applied_response'], response)
                        if mode == 'replay':
                            self.assertEqual(revised, record)
                    if mode == 'unrelated':
                        read.assert_not_called()
                case.set_field.assert_not_called()
                self.assertEqual([r['status'] for r in case.rows], ['blocked', 'blocked', 'ready', 'blocked'])

    def test_retry_release_intent_survives_partial_external_effect_then_reconciles(self):
        request, _ = self.direction_inputs()
        fail_once = True
        def effect(github, project, member, field, value):
            nonlocal fail_once
            durable = diagnosis.assignments.current(self.database, 'product', 'm1', '42')['stuck_work'][self.prior['assignment_id']]
            self.assertEqual(durable['status'], 'release-pending')
            self.change_status(github, project, member, field, value)
            if fail_once:
                fail_once = False
                raise RepositoryError('transport_error', 'Local lost status response')
        self.set_field.side_effect = effect
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'transport_error')
        saved = diagnosis.assignments.current(self.database, 'product', 'm1', '42')['stuck_work'][self.prior['assignment_id']]
        self.assertEqual(saved['status'], 'release-pending')
        self.assertIsNotNone(saved['applied_response'])
        released = diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(released['status'], 'released')
        self.assertEqual(self.set_field.call_count, 3)  # Only three distinct Ready effects; Blocked rows are inputs.
        self.assertEqual({r['status'] for r in self.rows}, {'ready'})
        for row in diagnosis.assignments.rows(self.database):
            self.assertEqual(row['stuck_disposition'], 'superseded')
        writes = self.set_field.call_count
        self.assertEqual(diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github), released)
        self.assertEqual(self.set_field.call_count, writes)

    def test_declaration_prewrite_admission_cases(self):
        from factory_v1 import controls
        for reason, code in [('pause', 'iteration_paused'), ('revised-body', 'scope_mismatch')]:
            with self.subTest(reason=reason), component_case(DiagnosisComponentTests) as case:
                if reason == 'revised-body':
                    record = case.seed_declaration()
                else:
                    controls.request(case.database, 'product', 'm1', '42', 'pause', 'local diagnosis pause')
                before = case.durable_snapshot()
                reads = []
                def read(github, repository, number):
                    reads.append(number)
                    return {'body': 'Revised original requirement'} if len(reads) == 1 else case.read_issue(github, repository, number)
                with patch.object(diagnosis.tickets.gh, 'read_issue', side_effect=read):
                    with self.assertRaises(RepositoryError) as refusal:
                        diagnosis.declare(case.database, 'product', 'm1', '42',
                            {'stuck_assignment': case.prior['assignment_id']}, case.github)
                self.assertEqual(refusal.exception.code, code)
                self.assertEqual(case.durable_snapshot(), before)
                case.set_field.assert_not_called()
                held = diagnosis.assignments.rows(case.database)[0]
                if reason == 'pause':
                    self.assertNotIn('stuck_disposition', held)
                else:
                    self.assertEqual(len(reads), 1)
                    self.assertEqual([r['status'] for r in case.rows], ['blocked', 'blocked', 'ready', 'blocked'])
                    self.assertEqual(held['stuck_disposition'], 'held')
                    self.assertEqual(diagnosis.assignments.current(case.database, 'product', 'm1', '42')['stuck_work'][held['assignment_id']], record)
                    self.assertEqual(diagnosis.declare(case.database, 'product', 'm1', '42',
                        {'stuck_assignment': held['assignment_id']}, case.github), record)
                    case.set_field.assert_not_called()

    def completed_inputs(self):
        """Direct local records/artifacts, not a native worker or stage execution."""
        record = self.seed_declaration()
        root = self.root / 'completed-debug'
        root.mkdir(mode=0o700)
        e = test_simplification.LocalStageEvidence(root)
        axis = copy.deepcopy(self.prior)
        axis.pop('stuck_disposition', None)
        axis.update(assignment_id='b' * 64, claim_id='completed-debug', runtime=e.runtime,
                    result_disposition={'isolated_execution': True})
        axis['handoff'].update(stage='diagnosis', diagnosis={'pins': record['pins']}, skills=[],
            workspace=str(root / 'workspace'), profile={'name': 'completed-debug', 'home': str(self.root / 'debug-home')},
            adaptation='LABELED local read-only diagnosis input; no native execution', preceding=[],
            capabilities=['read_workspace', 'scratch', 'model'], result_contract=diagnosis.assignments.CONTRACT,
            claim_id=axis['claim_id'])
        axis['handoff_digest'] = diagnosis.assignments.digest(axis['handoff'])
        command = {'command': 'python symptom.py', 'result': 'LABELED local red feedback; exit_code=1'}
        report = {'contract': diagnosis.CONTRACT, 'adaptation': axis['handoff']['adaptation'], 'pins': record['pins'],
            'feedback_loop': {**command, 'symptom': 'Wrong greeting'}, 'commands': [command],
            'hypotheses': [{'hypothesis': 'Wrong literal', 'prediction': 'Greeting assertion is red', 'evidence': 'Local mismatch'}],
            'attempted_repairs': ['Responsible literal repair stayed red'], 'findings': ['Local mismatch'],
            'cause_or_uncertainty': 'Wrong literal or hidden transform', 'blockers': [],
            'directions': [{'id': 'retry', 'direction': 'Investigate original requirement', 'tradeoff': 'More investigation'}],
            'recommendation': 'retry'}
        result = {'status': 'done', 'run_id': e.runtime['run_id'], 'loads': [],
            'work': ['LABELED local read-only investigation input'], 'tests': [command],
            'artifacts': [diagnosis.simplification.artifact('diagnosis-report', json.dumps(report))]}
        conversation = {'messages': [
            {'role': 'assistant', 'tool_calls': [{'id': 'repro', 'function': {
                'name': 'terminal', 'arguments': json.dumps({'command': command['command']})}}]},
            {'role': 'tool', 'tool_call_id': 'repro', 'content': command['result']}]}
        files = {'scratch/result.json': result, 'scratch/loads.json': [], 'scratch/events.jsonl': {},
            'scratch/conversation.json': conversation, 'scratch/probes.json': {}, 'inputs/worker.py': {},
            'inputs/sandbox_relay.py': {}, 'inputs/assignment.json': {'assignment_id': axis['assignment_id']},
            'inputs/handoff.json': axis['handoff'], 'container-inspection.json': {}, 'launch-command.json': {},
            'source-artifacts.json': {}, 'worker-reported-tests.json': [command],
            'model-evidence/response-local.json': {'label': 'Local binding input; no native/model execution'}}
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content))
        axis['submitted_result'] = result
        axis['diagnosis_result'] = {**report, 'report_sha256': result['artifacts'][0]['sha256'],
            'native_execution_trusted': False, 'advance_allowed': False, 'close_allowed': False}
        self.store(axis)
        item = diagnosis.assignments.current(self.database, 'product', 'm1', '42')
        record = item['stuck_work'][self.prior['assignment_id']]
        record['diagnosis_assignment'] = axis['assignment_id']
        item.update(origin={'platform': 'discord', 'chat_id': '42', 'thread_id': '42',
                            'parent_chat_id': '41', 'scope_id': '40'},
                    correlation={'project_id': 'product', 'iteration_id': 'm1', 'control_run_id': 'local-control',
                                 'evidence_id': 'local-evidence', 'stage': 'diagnosis'})
        diagnosis.tickets.checkpoint(self.database, item)
        # Assignment eligibility is an outside prerequisite. Completed/report/auth readers stay real.
        self.eligibility = self.enterContext(patch.object(diagnosis.assignments, 'prepare', return_value=axis))
        return record, axis

    def host_grant(self, axis, path=None):
        path = path or self.root / 'host-authorization.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        config = {'operator_id': '42', 'execution_reference': 'LABELED local host grant; not native acceptance',
                  'diagnosis': diagnosis.execution_binding(axis)}
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        return path, config

    def durable_snapshot(self):
        return {table: self.database.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                for table in ('iterations', 'role_assignments', 'notifications')
                if self.database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()}

    def test_completed_reader_requires_isolation_terminal_removal_and_exact_report(self):
        record, good = self.completed_inputs()
        self.assertEqual(diagnosis.completed(self.database, 'product', 'm1', '42', record, self.github), good)
        self.assertFalse(good['diagnosis_result']['native_execution_trusted'])
        self.eligibility.assert_called_once()
        self.assertTrue(self.eligibility.call_args.kwargs['dry_run'])
        for group, field, value in [('runtime', 'status', 'running'), ('runtime', 'container_removed', False),
                                    ('result_disposition', 'isolated_execution', False),
                                    ('diagnosis_result', 'cause_or_uncertainty', 'Changed stored report')]:
            with self.subTest(group=group, field=field):
                row = copy.deepcopy(good)
                row[group][field] = value
                self.store(row)
                before = self.durable_snapshot()
                with self.assertRaises(RepositoryError) as refusal:
                    diagnosis.completed(self.database, 'product', 'm1', '42', record, self.github)
                self.assertEqual(refusal.exception.code, 'invalid_result' if group == 'diagnosis_result' else 'diagnosis_incomplete')
                self.assertEqual(self.durable_snapshot(), before)
        row = copy.deepcopy(good)
        del row['diagnosis_result']
        self.store(row)
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.completed(self.database, 'product', 'm1', '42', record, self.github)
        self.assertEqual(refusal.exception.code, 'diagnosis_incomplete')
        self.store(good)
        before = self.durable_snapshot()
        self.assertEqual(diagnosis.completed(self.database, 'product', 'm1', '42', {**record, 'status': 'release-pending'}, self.github), good)
        self.assertEqual(self.eligibility.call_args.kwargs['diagnosis_revalidation'], good['assignment_id'])
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.set_field.call_count, 0)

    def test_authorization_reader_requires_private_exact_host_grant_outside_every_worker_scope(self):
        _, axis = self.completed_inputs()
        path, config = self.host_grant(axis)
        before = self.durable_snapshot()
        self.assertEqual(diagnosis.authorization(self.database, path, '42', axis), config)
        for mode in (0o400, 0o640, 0o644):
            with self.subTest(mode=mode):
                path.chmod(mode)
                with self.assertRaises(RepositoryError) as refusal:
                    diagnosis.authorization(self.database, path, '42', axis)
                self.assertEqual(refusal.exception.code, 'approval_required')
        path.chmod(0o600)
        link = self.root / 'linked-grant.json'
        os.link(path, link)
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.authorization(self.database, path, '42', axis)
        self.assertEqual(refusal.exception.code, 'approval_required')
        link.unlink()
        for assignment in (self.prior, axis):
            for scope in (assignment['handoff']['workspace'], assignment['handoff']['profile']['home'],
                          assignment['runtime']['artifacts']):
                with self.subTest(assignment=assignment['assignment_id'], scope=scope):
                    mounted, _ = self.host_grant(axis, Path(scope) / 'mounted-grant.json')
                    with self.assertRaises(RepositoryError) as refusal:
                        diagnosis.authorization(self.database, mounted, '42', axis)
                    self.assertEqual(refusal.exception.code, 'approval_required')
                    mounted.unlink()
        root = Path(axis['runtime']['artifacts'])
        root.chmod(0o755)
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.authorization(self.database, path, '42', axis)
        self.assertEqual(refusal.exception.code, 'execution_required')
        root.chmod(0o700)
        for change in ({'operator_id': 'other'}, {'execution_reference': ''}, {'extra': True},
                       {'diagnosis': {**config['diagnosis'], 'report_sha256': '0' * 64}},
                       {'diagnosis': {**config['diagnosis'], 'pins': {}}}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**config, **change}))
                with self.assertRaises(RepositoryError) as refusal:
                    diagnosis.authorization(self.database, path, '42', axis)
                self.assertEqual(refusal.exception.code, 'execution_required')
        path.write_text(json.dumps(config))
        evidence = root / 'model-evidence/response-local.json'
        original = evidence.read_bytes()
        evidence.write_bytes(original + b'\n')
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.authorization(self.database, path, '42', axis)
        self.assertEqual(refusal.exception.code, 'execution_required')
        evidence.write_bytes(original)
        self.assertEqual(diagnosis.authorization(self.database, path, '42', axis), config)
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.set_field.call_count, 0)

    def test_request_direction_requires_fresh_authority_and_persists_exact_pending_event_replay(self):
        from factory_v1.tests.receipt_policy import consumer_matrix
        with component_case(DiagnosisComponentTests) as case:
            _, axis = case.completed_inputs()
            path, config = case.host_grant(axis)
            self.assertEqual(consumer_matrix(case, case.database, path, 'diagnosis',
                lambda: diagnosis.authorization(case.database, path, '42', axis)), config)
            self.assertEqual(case.set_field.call_count, 0)
        _, axis = self.completed_inputs()
        request = {'stuck_assignment': self.prior['assignment_id']}
        path, config = self.host_grant(axis)
        before = self.durable_snapshot()
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.request_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'execution_required')
        self.assertEqual(self.durable_snapshot(), before)
        for config_change in ({'operator_id': 'other'}, {'diagnosis': {**config['diagnosis'], 'run_id': 'stale-run'}}):
            item = diagnosis.assignments.current(self.database, 'product', 'm1', '42')
            item['stuck_work'][self.prior['assignment_id']]['authorization'] = {**config, **config_change}
            diagnosis.tickets.checkpoint(self.database, item)
            before = self.durable_snapshot()
            with self.assertRaises(RepositoryError) as refusal:
                diagnosis.request_direction(self.database, 'product', 'm1', '42', request, self.github)
            self.assertEqual(refusal.exception.code, 'execution_required')
            self.assertEqual(self.durable_snapshot(), before)
        result = diagnosis.request_direction(self.database, 'product', 'm1', '42', request, self.github, path)
        notification = result['attention']
        event = notification['event']
        self.assertEqual(result['stuck']['authorization'], config)
        self.assertEqual(result['stuck']['status'], 'awaiting-direction')
        self.assertEqual(event['event_id'], 'stuck-' + self.prior['assignment_id'])
        self.assertEqual(event['decision_id'], event['event_id'])
        self.assertEqual(event['evidence_ids'], [self.prior['assignment_id'], axis['assignment_id'], axis['diagnosis_result']['report_sha256']])
        self.assertEqual(json.loads(event['message'])['directions'], axis['diagnosis_result']['directions'])
        self.assertEqual((notification['state'], notification['attempts'], notification['decision_response']), ('pending', 0, None))
        before = self.durable_snapshot()
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            item = diagnosis.assignments.current(restarted, 'product', 'm1', '42')
            self.assertEqual(diagnosis.attention.get_record(restarted, item, event['event_id']), notification)
            self.assertEqual(diagnosis.request_direction(restarted, 'product', 'm1', '42', request, self.github), result)
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.database.execute('SELECT count(*) FROM notifications').fetchone()[0], 1)
        evidence = Path(axis['runtime']['artifacts']) / 'model-evidence/response-local.json'
        evidence.write_bytes(evidence.read_bytes() + b'\n')
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.request_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'execution_required')
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(self.set_field.call_count, 0)





if __name__ == "__main__":
    unittest.main()
