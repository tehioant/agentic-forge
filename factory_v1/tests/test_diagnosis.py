"""Focused report-policy units plus two public CLI smokes. External execution is LABELED simulation."""
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

    def test_public_stuck_debug_direction_retry_and_independent_progress(self):
        stuck = self.stuck()
        held = self.assignment.ok(self.command('declare-stuck', self.identity))
        self.assertEqual(held['affected'], [10, 11])
        self.assertTrue(held['blocks_verified'])
        frontier = self.assignment.ok(self.tracker.control('frontier'))['ticket_work']['frontier']
        self.assertEqual(frontier['eligible'], [12])
        self.assertEqual(frontier['active'], [])
        for number in (10, 11):
            self.assertEqual(self.tracker.members[number]['fields']['STATUS'], 'Blocked')
            self.assertEqual(self.tracker.issues[number]['state'], 'open')
        self.assertNotIn('reservation', self.tracker.inspect())
        writes = len(self.fixture.calls)
        self.assertEqual(self.assignment.ok(self.command('declare-stuck', self.identity)), held)
        self.assertFalse(any(c[0] != 'GET' and c[1] != '/graphql' for c in self.fixture.calls[writes:]))
        dependent = self.assignment.configuration(11)
        dependent['profile'].update(name='dependent-builder', home=str(self.root / 'dependent-home'))
        dependent['claim_id'] = 'dependent-claim'
        self.assignment.refused(self.assignment.command(request=dependent), 'ticket_ineligible')
        before = self.fixture.state.read_bytes()
        dry = self.assignment.ok(self.command('prepare-diagnosis', self.debug_request(), dry=True))
        self.assertEqual(self.fixture.state.read_bytes(), before)
        debug = self.assignment.ok(self.command('prepare-diagnosis', self.debug_request()))
        self.assertEqual(debug['assignment_id'], dry['assignment_id'])
        self.assignment.refused(self.assignment.command('assignment-result', self.assignment.result(debug), debug['assignment_id']), 'invalid_result')
        # A held primary does not monopolize the implementation slot while debug runs.
        self.assignment.ok(self.tracker.control('reserve-ticket', issue=12))
        independent = self.assignment.configuration(12)
        independent['baseline'] = self.assignment.request['baseline']
        independent['profile'].update(name='independent-builder', home=str(self.root / 'independent-home'))
        independent['claim_id'] = 'independent-claim'
        ready = self.assignment.ok(self.assignment.command(request=independent))
        self.case.mode('')
        progress = self.assignment.ok(self.case.launch(ready))
        self.assertEqual(progress['runtime']['status'], 'complete', progress)
        axis = self.assignment.ok(self.case.launch(debug))
        self.assertEqual(axis['runtime']['status'], 'complete', axis)
        self.assertEqual(axis['handoff']['skill_entry_points'], ['diagnosing-bugs'])
        self.assertEqual(axis['handoff']['capabilities'], ['read_workspace', 'scratch', 'model'])
        self.assertEqual(axis['handoff']['diagnosis']['pins'], held['pins'])
        root = Path(axis['runtime']['artifacts'])
        inspection = json.loads((root / 'container-inspection.json').read_text())
        self.assertFalse(next(m['RW'] for m in inspection['Mounts'] if m['Destination'] == '/workspace'))
        self.assertEqual((root / 'workspace/hello.py').read_bytes(), (Path(stuck['runtime']['artifacts']) / 'workspace/hello.py').read_bytes())
        self.assertIn('AssertionError', axis['diagnosis_result']['feedback_loop']['result'])
        self.assertIn('exit_code=1', axis['diagnosis_result']['feedback_loop']['result'])
        self.assertFalse(axis['diagnosis_result']['advance_allowed'])
        self.assertFalse(axis['diagnosis_result']['native_execution_trusted'])
        self.assignment.refused(self.command('request-direction', self.identity), 'execution_required')
        path = self.authorization(axis)
        path.chmod(0o644)
        self.assignment.refused(self.command('request-direction', self.identity, path), 'approval_required')
        path.chmod(0o600)
        mounted_by_builder = Path(stuck['runtime']['artifacts']) / 'scratch/fake-host-grant.json'
        mounted_by_builder.write_bytes(path.read_bytes())
        mounted_by_builder.chmod(0o600)
        self.assignment.refused(self.command('request-direction', self.identity, mounted_by_builder), 'approval_required')
        notified = self.assignment.ok(self.command('request-direction', self.identity, path))
        event = notified['attention']
        self.assertEqual(event['destination'], self.tracker.inspect()['origin'])
        self.assertEqual(event['state'], 'pending')
        self.assertIsNone(event['decision_response'])
        self.assertEqual(self.assignment.ok(self.command('request-direction', self.identity))['attention'], event)
        apply = {**self.identity, 'event_id': event['event']['event_id']}
        self.assignment.refused(self.command('apply-direction', apply), 'decision_required')
        self.assignment.refused(self.respond(event, operator_id='99'), 'invalid_decision')
        self.assignment.refused(self.respond(event, response='Looks good; unrelated chat is not a direction'), 'invalid_decision')
        self.assignment.refused(self.respond(event, origin={**event['destination'], 'thread_id': '999'}), 'invalid_decision')
        self.assignment.refused(self.command('apply-direction', {**apply, 'event_id': 'unrelated'}), 'invalid_decision')
        transcript = root / 'scratch/conversation.json'
        actual = transcript.read_bytes()
        transcript.write_bytes(actual + b'\n')
        self.assignment.refused(self.command('request-direction', self.identity), 'execution_required')
        transcript.write_bytes(actual)
        self.assignment.ok(self.respond(event))
        dependent_body = self.tracker.issues[11]['body']
        self.tracker.issues[11]['body'] += '\nExternally changed dependent scope'
        self.assignment.refused(self.command('apply-direction', apply), 'scope_mismatch')
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')
        self.assertEqual(self.tracker.members[12]['fields']['STATUS'], 'Active')
        self.tracker.issues[11]['body'] = dependent_body
        # Lose the response after restoring the root; the dependent remains held.
        self.tracker.drop = 'field'
        interrupted = self.command('apply-direction', apply)
        self.assertEqual(interrupted.returncode, 2, interrupted.stdout)
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Ready')
        self.assertEqual(self.tracker.members[11]['fields']['STATUS'], 'Blocked')
        pending = self.tracker.inspect()['stuck_work'][stuck['assignment_id']]
        self.assertEqual(pending['status'], 'release-pending')
        self.assertIsNotNone(pending['applied_response'])
        frontier = self.assignment.ok(self.tracker.control('frontier'))['ticket_work']['frontier']
        self.assertNotIn(10, frontier['eligible'])
        self.assertNotIn(11, frontier['eligible'])
        self.assertEqual(self.assignment.ok(self.assignment.inspect(stuck))['stuck_disposition'], 'held')
        self.assertNotIn('stuck_disposition', self.assignment.ok(self.assignment.inspect(axis)))
        self.assignment.refused(self.case.launch(debug), 'ticket_ineligible')
        self.assignment.refused(self.command('prepare-diagnosis', self.debug_request()), 'diagnosis_held')
        replacement = copy.deepcopy(axis['handoff'])
        replacement = {k: replacement[k] for k in diagnosis.assignments.REQUEST_FIELDS}
        replacement['skills'] = self.assignment.configuration(stage='diagnosis')['skills']
        replacement['profile'].update(name='replacement-debug', home=str(self.root / 'replacement-debug'))
        replacement['claim_id'] = 'replacement-debug'
        self.assignment.refused(self.assignment.command(request=replacement), 'claim_conflict')
        transcript.write_bytes(actual + b'\n')
        refused = self.command('apply-direction', apply)
        self.assertEqual(refused.returncode, 2, refused.stdout)
        self.assertEqual(self.tracker.members[11]['fields']['STATUS'], 'Blocked')
        transcript.write_bytes(actual)
        released = self.assignment.ok(self.command('apply-direction', apply))
        self.assertEqual(released['status'], 'released')
        self.assertEqual(self.assignment.ok(self.command('apply-direction', apply)), released)
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Ready')
        self.assertEqual(self.tracker.members[11]['fields']['STATUS'], 'Ready')
        self.assertEqual(self.tracker.members[12]['fields']['STATUS'], 'Active')
        self.assertEqual(self.tracker.issues[10]['state'], 'open')
        self.assertFalse(self.assignment.ok(self.assignment.inspect(axis))['diagnosis_result']['advance_allowed'])
        # A direction is not authority to overlap the independent implementation ticket.
        retry = copy.deepcopy(self.assignment.request)
        retry['profile'].update(name='retry-builder', home=str(self.root / 'retry-home'))
        retry['claim_id'] = 'retry-claim'
        self.assignment.refused(self.assignment.command(request=retry), 'claim_conflict')

    def test_public_diagnosis_rejects_green_claim_contradicting_retained_red_output(self):
        from factory_v1 import sandbox

        self.stuck()
        self.assignment.ok(self.command('declare-stuck', self.identity))
        debug = self.assignment.ok(self.command('prepare-diagnosis', self.debug_request()))
        self.case.mode('diagnosis-false-green')
        axis = self.assignment.ok(self.case.launch(debug))
        self.assertEqual(axis['runtime']['status'], 'failed', axis.get('runtime'))
        self.assertEqual(axis['runtime']['error'], 'invalid_result')
        self.assertTrue(axis['runtime']['container_removed'])
        self.assertNotIn('submitted_result', axis)
        self.assertNotIn('diagnosis_result', axis)
        root = Path(axis['runtime']['artifacts'])
        conversation = (root / 'scratch/conversation.json').read_bytes()
        self.assertIn(b'AssertionError', conversation)
        self.assertIn(b'exit_code=1', conversation)
        result = json.loads((root / 'scratch/result.json').read_text())
        self.assertEqual(result['tests'][0]['result'], 'ALL PASSED\nexit_code=0')
        envelope = json.loads((root / 'inputs/assignment.json').read_text())
        for validate in (lambda: sandbox.check_worker_evidence(axis, root, envelope['inputs']),
                         lambda: diagnosis.validate_result(axis, result)):
            with self.assertRaises(RepositoryError) as refusal:
                validate()
            self.assertEqual(refusal.exception.code, 'invalid_result')
        self.assertEqual((root / 'scratch/conversation.json').read_bytes(), conversation)
        self.assignment.refused(self.command('request-direction', self.identity), 'diagnosis_incomplete')
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')
        self.assertEqual(self.tracker.members[11]['fields']['STATUS'], 'Blocked')

    def test_missing_capability_material_direction_and_pause_stale_replay_fail_closed(self):
        prepared = self.assignment.ok(self.assignment.command())
        self.assignment.refused(self.command('declare-stuck', {'stuck_assignment': prepared['assignment_id']}), 'stuck_required')
        # An infrastructure failure/limit without a responsible stuck result never escalates.
        self.case.mode('malformed')
        failed = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(failed['runtime']['status'], 'failed')
        self.assignment.refused(self.command('declare-stuck', {'stuck_assignment': prepared['assignment_id']}), 'stuck_required')
        request = copy.deepcopy(self.assignment.request)
        request['profile'].update(name='second-builder', home=str(self.root / 'second-home'))
        request['claim_id'] = 'second-claim'
        prepared = self.assignment.ok(self.assignment.command(request=request))
        self.case.mode('implementation-stuck')
        stuck = self.assignment.ok(self.case.launch(prepared))
        self.identity = {'stuck_assignment': stuck['assignment_id']}
        self.tracker.omit_effect = True
        self.assignment.refused(self.command('declare-stuck', self.identity), 'publication_mismatch')
        independent = self.assignment.configuration(12)
        independent['profile'].update(name='third-builder', home=str(self.root / 'third-home'))
        independent['claim_id'] = 'third-claim'
        self.assignment.refused(self.assignment.command(request=independent), 'claim_conflict')
        self.tracker.omit_effect = False
        held = self.assignment.ok(self.command('declare-stuck', self.identity))
        self.assertEqual(held['affected'], [10, 11])
        debug_request = self.debug_request()
        prepared = self.assignment.ok(self.command('prepare-diagnosis', debug_request))
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.refused(self.case.launch(prepared), 'iteration_paused')
        self.assignment.refused(self.command('request-direction', self.identity), 'iteration_paused')
        self.assignment.mutate_iteration(lambda item: item.update(status='active'))
        self.case.mode('diagnosis-blocker')
        axis = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(axis['runtime']['status'], 'complete', axis)
        self.assertEqual(axis['submitted_result']['status'], 'blocked')
        self.assertIsNone(axis['diagnosis_result']['feedback_loop'])
        self.assertTrue(axis['diagnosis_result']['blockers'])
        config = self.authorization(axis)
        raw = config.read_text()
        invalid = json.loads(raw)
        invalid['diagnosis']['pins']['spec_commit'] = 'f' * 40
        config.write_text(json.dumps(invalid))
        self.assignment.refused(self.command('request-direction', self.identity, config), 'execution_required')
        config.write_text(raw)
        mounted = Path(axis['runtime']['artifacts']) / 'scratch/grant.json'
        mounted.write_text(raw)
        mounted.chmod(0o600)
        self.assignment.refused(self.command('request-direction', self.identity, mounted), 'approval_required')
        event = self.assignment.ok(self.command('request-direction', self.identity, config))['attention']
        self.assignment.ok(self.respond(event, 'revise'))
        apply = {**self.identity, 'event_id': event['event']['event_id']}
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.refused(self.command('apply-direction', apply), 'iteration_paused')
        self.assignment.mutate_iteration(lambda item: item.update(status='active'))
        decision = self.assignment.ok(self.command('apply-direction', apply))
        self.assertEqual(decision['status'], 'revision-required')
        self.assertEqual(self.assignment.ok(self.command('apply-direction', apply)), decision)
        self.assertEqual(self.assignment.ok(self.tracker.control('frontier'))['ticket_work']['frontier']['eligible'], [12])
        self.assignment.refused(self.respond(event, 'retry'), 'decision_conflict')
        self.tracker.issues[10]['body'] += '\nChanged requirement'
        self.assignment.refused(self.command('declare-stuck', self.identity), 'scope_mismatch')
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')
        self.assertEqual(self.tracker.issues[10]['state'], 'open')


if __name__ == '__main__':
    unittest.main()
