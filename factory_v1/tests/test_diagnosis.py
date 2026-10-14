"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import copy
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

    def test_red_loop_is_diagnosis_not_repair(self):
        self.validate(self.report)

    def test_each_report_field_is_required(self):
        for field in diagnosis.REPORT_FIELDS:
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                del report[field]
                self.reject(report)

    def test_wrong_contract_pin_adaptation_and_extra_claims_refuse(self):
        for change in ({'contract': 'made-up'}, {'pins': {**self.pins, 'tree_sha256': '0' * 64}},
                       {'adaptation': 'write-fix'}, {'accepted_fix': True}):
            with self.subTest(change=change):
                self.reject({**self.report, **change})

    def test_no_loop_requires_explicit_blocker_and_no_hypotheses(self):
        report = {**self.report, 'feedback_loop': None, 'hypotheses': [], 'blockers': ['Missing environment / HITL template']}
        self.validate(report)
        self.reject({**report, 'blockers': []})
        self.reject({**report, 'hypotheses': self.report['hypotheses']})

    def test_commands_and_loop_must_match_retained_results_verbatim(self):
        for change in ({'commands': []}, {'commands': [{'command': 'invented', 'result': 'pass'}]},
                       {'feedback_loop': {**self.report['feedback_loop'], 'result': 'invented green'}},
                       {'feedback_loop': {**self.report['feedback_loop'], 'symptom': ''}},
                       {'feedback_loop': self.command}, {'hypotheses': []}):
            with self.subTest(change=change):
                self.reject({**self.report, **change})

    def test_ranked_hypotheses_have_falsifiable_predictions(self):
        for hypothesis in ({'hypothesis': 'theory'}, {'hypothesis': 'theory', 'prediction': '', 'evidence': 'none'}, 'theory'):
            self.reject({**self.report, 'hypotheses': [hypothesis]})

    def test_recommendation_names_one_unique_direction_not_approval(self):
        for change in ({'recommendation': 'accept-fix'}, {'directions': []},
                       {'directions': self.report['directions'] * 2},
                       {'directions': [{'id': 'retry', 'direction': 'relax requirements'}]}):
            self.reject({**self.report, **change})

    def test_missing_attempts_findings_or_uncertainty_refuse(self):
        for field, value in [('attempted_repairs', []), ('findings', []), ('cause_or_uncertainty', ''),
                             ('commands', None), ('blockers', 'missing'), ('attempted_repairs', [None])]:
            self.reject({**self.report, field: value})

    def test_malformed_duplicate_and_oversized_report_json_refuse(self):
        for raw in ('{"pins":1,"pins":2}', '{bad}', '[]', '{"x":' + '[' * 2000 + '0' + ']' * 2000 + '}', 'x' * 16_000_001):
            with self.assertRaises(RepositoryError):
                parsed = diagnosis.simplification.object_json(raw, 'invalid_result')
                self.validate(parsed)

    def test_claim_retirement_requires_durable_verified_hold_and_no_live_worker(self):
        scope = {'project': 'p', 'iteration': 'i'}
        assignment = {'assignment_id': 'a', 'ticket_scope': scope, 'stuck_disposition': 'held',
                      'runtime': {'status': 'complete'}}
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
        return {'body': 'Local issue ' + str(number)}

    def change_status(self, github, project, member, field, value):
        row = next(r for r in self.rows if r['membership_id'] == member)
        row['status'] = 'ready' if value == 'READY' else 'blocked'

    def declare(self):
        # The handoff body pin binds the actual external fixture body, not a self-validating constant.
        self.prior['handoff']['issue']['body_sha256'] = diagnosis.hashlib.sha256(b'Local issue 10').hexdigest()
        self.prior['handoff_digest'] = diagnosis.assignments.digest(self.prior['handoff'])
        self.store(self.prior)
        return diagnosis.declare(self.database, 'product', 'm1', '42', {'stuck_assignment': self.prior['assignment_id']}, self.github)

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

    def test_declare_transitive_scope_durable_retirement_and_independent_progress(self):
        record = self.declare()
        self.assertEqual(record['affected'], [10, 11, 13])
        self.assertTrue(record['blocks_verified'])
        self.assertEqual(record['status'], 'diagnosis')
        self.assertEqual(self.rows[2]['status'], 'ready')
        self.assertEqual([r['status'] for r in self.rows if r['number'] != 12], ['blocked'] * 3)
        held = diagnosis.assignments.rows(self.database)[0]
        self.assertEqual(held['stuck_disposition'], 'held')
        self.assertTrue(diagnosis.released_claim(self.database, held))
        writes = self.set_field.call_count
        self.assertEqual(diagnosis.declare(self.database, 'product', 'm1', '42', {'stuck_assignment': held['assignment_id']}, self.github), record)
        self.assertEqual(self.set_field.call_count, writes)
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            self.assertEqual(diagnosis.assignments.current(restarted, 'product', 'm1', '42')['stuck_work'][held['assignment_id']], record)

    def test_exact_independent_handoff_and_real_staging(self):
        record = self.declare()
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
        record = self.declare()
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
        record = self.declare()
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

    def test_direction_silence_revise_and_exact_replay_never_imply_retry(self):
        request, notification = self.direction_inputs('revise')
        response = notification['decision_response']
        notification['decision_response'] = None
        writes = self.set_field.call_count
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'decision_required')
        notification['decision_response'] = response
        record = diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(record['status'], 'revision-required')
        self.assertEqual(self.set_field.call_count, writes)
        self.assertEqual(diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github), record)
        notification['decision_response'] = {**response, 'response': json.dumps({'action': 'retry', 'direction': 'Changed scope'})}
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'decision_conflict')

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
        self.assertEqual(self.set_field.call_count, 6)  # Three Blocked writes, then three distinct Ready writes.
        self.assertEqual({r['status'] for r in self.rows}, {'ready'})
        for row in diagnosis.assignments.rows(self.database):
            self.assertEqual(row['stuck_disposition'], 'superseded')
        writes = self.set_field.call_count
        self.assertEqual(diagnosis.apply_direction(self.database, 'product', 'm1', '42', request, self.github), released)
        self.assertEqual(self.set_field.call_count, writes)

    def test_pause_blocks_declaration_without_retiring_or_status_mutation(self):
        from factory_v1 import controls
        controls.request(self.database, 'product', 'm1', '42', 'pause', 'local diagnosis pause')
        with self.assertRaises(RepositoryError) as refusal:
            diagnosis.declare(self.database, 'product', 'm1', '42', {'stuck_assignment': self.prior['assignment_id']}, self.github)
        self.assertEqual(refusal.exception.code, 'iteration_paused')
        self.set_field.assert_not_called()
        self.assertNotIn('stuck_disposition', diagnosis.assignments.rows(self.database)[0])


if __name__ == "__main__":
    unittest.main()
