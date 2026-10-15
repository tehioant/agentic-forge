"""Small, deterministic recovery fixtures; no worker, HTTP server or model is started."""
import copy
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import assignments, delivery, merge, recovery, role_skills, simplification, tickets
from factory_v1.model_access import SUBSCRIPTION_SCOPE
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.tests.test_recovery import RecoveryFixture



@contextmanager
def component_case(cls):
    case = cls(methodName='runTest')
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()

class RecoveryComponentFixture(RecoveryFixture):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        db = sqlite3.connect(self.root / 'state.sqlite')
        self.db.backup(db)
        self.db.close()
        self.db = db
        self.addCleanup(db.close)
        (self.root / 'state.sqlite').chmod(0o600)
        evidence = self.evidence(False)
        evidence_id = assignments.digest(evidence)
        incident_id = 'main-' + assignments.digest({'project': 'p', 'iteration': 'i',
            'config': assignments.digest(self.config), 'sequence': 1})
        item = self.stored()
        incident = {'incident_id': incident_id, 'status': 'active',
            'monitor_sha256': assignments.digest(self.config), 'first_evidence': evidence_id,
            'latest_evidence': evidence_id, 'recovery_assignment': None, 'recovery_issue': None}
        item.update(main_incident=incident, delivery_monitor={'config': self.config,
            'config_sha256': assignments.digest(self.config), 'sequence': 1,
            'latest': {'evidence_id': evidence_id, 'status': 'failed', 'main': evidence['main']}})
        event = {'event_id': incident_id, 'kind': 'incident', 'message': 'Preadmitted failed-health incident',
            'issue_number': self.config['issue'], 'run_id': incident_id, 'evidence_ids': [evidence_id]}
        notification = {'project_id': 'p', 'iteration_id': 'i', 'event': event,
            'destination': item['origin'], 'state': 'pending', 'attempts': 0,
            'receipt': None, 'decision_response': None, 'correlation': {**item['correlation'],
                'work_item': {'repository': item['repository'], 'issue_number': event['issue_number']},
                'worker_run_id': incident_id, 'evidence_ids': [evidence_id]}}
        with self.db:
            for table, key in [('delivery_observations', 'id'), ('delivery_incidents', 'id'), ('notifications', 'event_id')]:
                self.db.execute(f'CREATE TABLE {table} (project TEXT, iteration TEXT, {key} TEXT, payload TEXT, PRIMARY KEY(project,iteration,{key}))')
            self.db.execute('INSERT INTO delivery_observations VALUES (?,?,?,?)', ('p', 'i', evidence_id, json.dumps(evidence)))
            self.db.execute('INSERT INTO delivery_incidents VALUES (?,?,?,?)', ('p', 'i', incident_id, json.dumps(incident)))
            self.db.execute('INSERT INTO notifications VALUES (?,?,?,?)', ('p', 'i', incident_id, json.dumps(notification)))
            save_iteration(self.db, item)
        self.issue = {'number': 20, 'id': 200, 'node_id': 'I20', 'body': 'repair requirements', 'state': 'open',
                      'html_url': 'https://github.com/example/product/issues/20'}
        self.pin = {k: self.issue[k] for k in ('number', 'id', 'node_id')} | {
            'body_sha256': hashlib.sha256(self.issue['body'].encode()).hexdigest()}
        self.github = Mock(base='https://api.github.com')
        self.github.request.side_effect = AssertionError('Unexpected external call')
        self.config_file = self.root / 'recovery.json'
        self.authorization = {'operator_id': '42', 'reference': 'SIMULATED independent host scope verification',
            'incident_id': self.stored()['main_incident']['incident_id'],
            'evidence_id': self.stored()['main_incident']['latest_evidence'],
            'issue': self.pin, 'paths': ['app.py'], 'removable_assignments': []}
        self.config_file.write_text(json.dumps(self.authorization))
        self.config_file.chmod(0o600)
        item = self.stored()
        item.update(status='active', execution_allowed=False, handoff={'commit': 'c' * 40},
                    ticket_work={'assignment_id': 'tickets', 'input_digest': 'd' * 64,
                        'inputs': {'documents': {}, 'handoff': {'commit': 'c' * 40}},
                        'publication_complete': True, 'board': {'project_id': 'P', 'status': {'id': 'F', 'options': {'Todo': 'T', 'Done': 'D'}}},
                        'request': {'tracker': {'statuses': {'ready': 'Todo', 'done': 'Done'}}}},
                    repository_onboarding={'status': 'verified', 'metadata': {'id': 7}, 'api_base': self.github.base})
        with self.db:
            save_iteration(self.db, item)
        self.read_patch = patch.object(tickets.gh, 'read_issue', side_effect=lambda g, r, n: copy.deepcopy(self.issue))
        self.reader = self.read_patch.start()
        self.addCleanup(self.read_patch.stop)
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            blocker = patch(target, side_effect=AssertionError('Unexpected external boundary: ' + target))
            blocker.start()
            self.addCleanup(blocker.stop)

    def register(self):
        return recovery.register(self.db, 'p', 'i', '42', self.config_file, self.github)

    def request(self, claim='repair-1'):
        provider, model, operation = SUBSCRIPTION_SCOPE
        return {'stage': 'repair', 'profile': {'name': claim, 'home': str(self.root / ('home-' + claim)), 'role': 'repair',
                    'provider': provider, 'model': model, 'operation': operation, 'reasoning': 'high'},
                'repository': 'example/product', 'repository_id': 7, 'workspace': str(self.root / 'repo'),
                'issue': self.pin, 'spec_commit': 'c' * 40, 'baseline': 'a' * 40, 'candidate': None,
                'standards': [simplification.artifact('AGENTS.md', 'meaningful tests')],
                'preceding': [], 'skills': [], 'capabilities': ['read_workspace', 'scratch', 'model', 'write_workspace'],
                'result_contract': assignments.CONTRACT, 'claim_id': claim}

    def preparation_seams(self):
        # Only external skill bytes/spending admission and authoritative tracker observation are simulated.
        selected = [{'name': name, 'source': role_skills.SELECTED[name][0], 'path': '/fixture/' + name,
                     'sha256': hashlib.sha256(name.encode()).hexdigest(), 'instructions': name, 'dependencies': []}
                    for name in ('diagnosing-bugs', 'implement')]
        def frontier(db, item, gh):
            item['ticket_work']['frontier'] = {'foreign_active': [], 'active': [10], 'items': [
                {'number': 20, 'id': 200, 'node_id': 'I20', 'admissible': True}]}
            return item
        stack = ExitStack()
        stack.enter_context(patch.object(role_skills, 'snapshots', return_value=selected))
        stack.enter_context(patch.object(assignments, 'admission_preview', return_value={'scope': 'test'}))
        stack.enter_context(patch.object(tickets, 'frontier', side_effect=frontier))
        # Restart inventory is an independent precursor to these recovery guards;
        # its real integration is covered by test_restart_recovery.
        stack.enter_context(patch('factory_v1.restart.before_dispatch'))
        return stack

    def prepare(self, claim='repair-1', dry_run=False):
        with self.preparation_seams():
            return recovery.prepare(self.db, 'p', 'i', '42', self.request(claim), self.github, dry_run)

    def seed_registered(self):
        """Declare admitted authority; registration tests use register instead."""
        item = self.stored()
        related = assignments.rows(self.db)
        removable = {a['assignment_id']: {'issue': a['handoff']['issue'], 'delivery': a['merge']}
                     for a in related if a['assignment_id'] in self.authorization['removable_assignments']}
        record = {'status': 'registered', 'config': copy.deepcopy(self.authorization),
            'evidence': self.evidence(False), 'assignment': None,
            'parked': [a['assignment_id'] for a in related], 'reservation': item.pop('reservation', None),
            'removable': removable, 'removed': {}, 'clearance': None}
        item['main_recoveries'] = {self.authorization['incident_id']: record}
        item['main_incident'].update(recovery_issue=20, recovery_assignment=assignments.digest(self.authorization))
        with self.db:
            for a in related:
                a['recovery_disposition'] = self.authorization['incident_id']
                self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(a), a['assignment_id']))
            save_iteration(self.db, item)
            self.db.execute('UPDATE delivery_incidents SET payload=? WHERE id=?',
                (json.dumps(item['main_incident']), self.authorization['incident_id']))
        return record

    def seed_assignment(self):
        """Preadmitted repair input with the same identity/projections as preparation."""
        record = self.seed_registered()
        item = self.stored()
        request = self.request() | {'preceding': recovery.preceding(record)}
        selected = [{'name': name, 'source': role_skills.SELECTED[name][0], 'path': '/fixture/' + name,
            'sha256': hashlib.sha256(name.encode()).hexdigest(), 'instructions': name, 'dependencies': []}
            for name in ('diagnosing-bugs', 'implement')]
        request['skills'] = [{k: v for k, v in s.items() if k != 'instructions'} for s in selected]
        scope = {'project': 'p', 'iteration': 'i', 'repository': item['repository'], 'repository_id': 7,
            'issue_number': 20, 'issue_id': 200, 'workspace': request['workspace']}
        loads = {self.issue['html_url']: self.pin['body_sha256']}
        for group in ('standards', 'preceding'):
            loads.update({'handoff:' + group + ':' + a['name']: a['sha256'] for a in request[group]})
        handoff = {**request, 'skills': selected, 'issue_body': self.issue['body'],
            'specification': item['ticket_work']['inputs'], 'adaptation': role_skills.ADAPTATIONS['repair'],
            'stage_rules': role_skills.STAGE_RULES['repair'],
            'support_policy': {k: role_skills.SUPPORT_POLICY[k] for k in sorted({'required', 'repository-inputs', 'diagnosing-bugs', 'implement-review'})},
            'input_loads': loads, 'skill_entry_points': ['diagnosing-bugs', 'implement'],
            'tracker': {'api_base': self.github.base, 'board': item['ticket_work']['board'], 'issue_url': self.issue['html_url']},
            'fresh_context': True, 'inherited_memory': False, 'inherited_sessions': False, 'credentials': [],
            'model_policy': {'scope': 'test', 'reservation': 1, 'recheck_at_call': True},
            'result_requirements': {'identity': ['assignment_id', 'handoff_digest', 'claim_id', 'run_id'],
                'required_evidence': ['actual-selected-instruction-loads', 'observable-skill-guided-work', 'artifacts', 'tests', 'trusted-whole-process-execution'],
                'advancement': 'disabled-without-trusted-whole-process-evidence'},
            'recovery': recovery.contract(record)}
        assignment = {'assignment_id': assignments.digest({'request': request, 'scope': scope}),
            'claim_id': request['claim_id'], 'ticket_scope': scope, 'handoff': handoff,
            'handoff_digest': assignments.digest(handoff), 'assignment_ready': True, 'launchable': False,
            'execution_allowed': False, 'status': 'prepared', 'refusal': 'whole_process_isolation_unavailable',
            'revision': {'spec': item['handoff'], 'ticket_assignment': 'tickets', 'ticket_inputs': 'd' * 64}}
        record = recovery.active(item)
        assert record is not None
        record.update(status='repair', assignment=assignment['assignment_id'])
        item['main_incident']['recovery_assignment'] = assignment['assignment_id']
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
            self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (assignment['assignment_id'], json.dumps(assignment)))
            save_iteration(self.db, item)
            self.db.execute('UPDATE delivery_incidents SET payload=? WHERE id=?',
                (json.dumps(item['main_incident']), self.authorization['incident_id']))
        return assignment

    def completed_report(self):
        assignment = self.seed_assignment()
        artifacts = self.root / 'artifacts'
        (artifacts / 'workspace').mkdir(parents=True)
        (artifacts / 'inputs/baseline').mkdir(parents=True)
        (artifacts / 'scratch').mkdir()
        (artifacts / 'workspace/app.py').write_text('fixed\n')
        (artifacts / 'inputs/baseline/app.py').write_text('broken\n')
        command, output = 'python focused.py', '{"exit_code": 1, "stdout": "expected symptom"}'
        conversation = {'messages': [{'role': 'assistant', 'tool_calls': [{'id': 'call', 'function': {
            'name': 'terminal', 'arguments': json.dumps({'command': command})}}]},
            {'role': 'tool', 'tool_call_id': 'call', 'content': output}]}
        (artifacts / 'scratch/conversation.json').write_text(json.dumps(conversation))
        assignment['runtime'] = {'artifacts': str(artifacts), 'run_id': 'run-1', 'status': 'complete', 'container_removed': True}
        scope = assignment['handoff']['recovery']
        tests = [{'command': command, 'result': output}]
        diagnosis = {'contract': 'factory-stuck-diagnosis-v1', 'pins': scope, 'adaptation': assignment['handoff']['adaptation'],
            'feedback_loop': {'command': command, 'result': output, 'symptom': 'exact failure'}, 'commands': tests,
            'hypotheses': [{'hypothesis': 'wrong literal', 'prediction': 'fix literal restores output', 'evidence': 'red output'}],
            'attempted_repairs': ['bounded literal fix'], 'findings': ['literal differs'], 'cause_or_uncertainty': 'literal mismatch',
            'directions': [{'id': 'forward', 'direction': 'fix literal', 'tradeoff': 'preserves behavior'}],
            'recommendation': 'forward', 'blockers': []}
        report = {'contract': recovery.CONTRACT, 'scope': scope, 'mode': 'roll-forward',
                  'rationale': 'Red literal check; forward preserves accepted behavior unlike revert.',
                  'diagnosis': diagnosis, 'removed_assignments': [], 'regression': 'focused regression retained', 'cleanup': 'no probes remain'}
        result = {'status': 'done', 'tests': tests, 'artifacts': [simplification.artifact('recovery-report', json.dumps(report))]}
        assignment['submitted_result'] = result
        return assignment, report, result, artifacts

    def delivered_ticket(self):
        old_issue = {'number': 10, 'id': 100, 'node_id': 'I10', 'body': 'original accepted behavior',
                     'state': 'closed', 'html_url': 'https://github.com/example/product/issues/10'}
        pin = {k: old_issue[k] for k in ('number', 'id', 'node_id')} | {
            'body_sha256': hashlib.sha256(old_issue['body'].encode()).hexdigest()}
        prior = {'assignment_id': 'f' * 64, 'claim_id': 'old', 'handoff': {'stage': 'simplify',
            'repository': 'example/product', 'issue': pin, 'workspace': str(self.root / 'old-workspace'),
            'profile': {'name': 'old-role', 'home': str(self.root / 'old-home')}},
            'ticket_scope': {'project': 'p', 'iteration': 'i', 'issue_number': 10}, 'merge': {'status': 'delivered'}}
        self.db.execute('CREATE TABLE IF NOT EXISTS role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (prior['assignment_id'], json.dumps(prior)))
        self.db.commit()
        self.authorization['removable_assignments'] = [prior['assignment_id']]
        self.config_file.write_text(json.dumps(self.authorization))
        self.reader.side_effect = lambda g, r, n: copy.deepcopy(self.issue if n == 20 else old_issue)
        return prior, old_issue

    def tracker_for_removed(self, old_issue, *, lost_after=False, lost_before=False):
        member = {'id': 'MEM10', 'status': 'D'}
        def write(path, payload, method):
            self.assertEqual((path, payload, method), ('/repos/example/product/issues/10',
                {'state': 'open', 'state_reason': 'reopened'}, 'PATCH'))
            if lost_before:
                raise RepositoryError('github_unknown', 'SIMULATED unavailable before effect')
            old_issue['state'] = 'open'
            if lost_after:
                raise RepositoryError('github_unknown', 'SIMULATED lost response after effect')
        def status(*args):
            self.assertEqual(args[1:], ('P', 'MEM10', 'F', 'T'))
            member['status'] = 'T'
            if lost_after:
                raise RepositoryError('github_unknown', 'SIMULATED lost status response after effect')
        self.github.request.side_effect = write
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(tickets.gh, 'membership', side_effect=lambda *a: copy.deepcopy(member)))
        field = stack.enter_context(patch.object(tickets.gh, 'set_field', side_effect=status))
        stack.enter_context(patch.object(tickets.gh, 'dependencies', return_value=[]))
        return field, member



class RecoveryComponents(RecoveryComponentFixture):
    def test_registered_recovery_authority_cases(self):
        for mode in ('valid', 'public', 'worker-mounted', 'changed-paths', 'changed-evidence'):
            with self.subTest(mode=mode), component_case(RecoveryComponentFixture) as case:
                error = None
                if mode == 'public':
                    case.config_file.chmod(0o644)
                    error = 'Private'
                elif mode == 'worker-mounted':
                    case.db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
                    case.db.execute('INSERT INTO role_assignments VALUES (?,?)', ('x', json.dumps({
                        'handoff': {'workspace': str(case.root), 'profile': {'home': '/fixture-home'}}})))
                    case.db.commit()
                    error = 'outside'
                elif mode.startswith('changed-'):
                    case.seed_registered()
                    mutation = {'paths': ['other.py']} if mode == 'changed-paths' else {'evidence_id': '0' * 64}
                    case.config_file.write_text(json.dumps(case.authorization | mutation))
                    error = 'authority'
                if error:
                    with self.assertRaisesRegex(RepositoryError, error):
                        case.register()
                else:
                    record = case.register()
                    self.assertEqual(record, case.register())
                    self.assertEqual(record['evidence'], case.evidence(False))
                    self.assertEqual(case.stored()['main_incident']['recovery_issue'], 20)
                    self.assertIsNotNone(case.sample(True)['incident'])
                case.github.request.assert_not_called()


    def test_fresh_repair_handoff_and_claim_replay_with_feature_priority(self):
        self.seed_registered()
        before = self.db.total_changes
        preview = self.prepare(dry_run=True)
        self.assertEqual(self.db.total_changes, before)
        assignment = self.prepare()
        self.assertEqual(assignment['assignment_id'], preview['assignment_id'])
        self.assertEqual(self.prepare()['assignment_id'], assignment['assignment_id'])
        self.assertEqual(self.stored()['main_incident']['recovery_assignment'], assignment['assignment_id'])
        self.assertEqual(assignment['handoff']['skill_entry_points'], ['diagnosing-bugs', 'implement'])
        self.assertEqual(assignment['handoff']['recovery']['paths'], ['app.py'])
        evidence = json.loads(assignment['handoff']['preceding'][0]['content'])
        self.assertEqual(evidence['evidence'], self.evidence(False))
        self.assertFalse(assignment['launchable'])
        for stage, number in [('implementation', 10), ('implementation', 20), ('repair', 10), ('reservation', 10)]:
            with self.assertRaises(RepositoryError):
                recovery.admission(self.stored(), stage, number)
        with self.assertRaises(RepositoryError):
            recovery.handoff(self.db, self.stored(), self.request() | {'preceding': [simplification.artifact('incident-evidence', 'worker assertion')]})

    def test_parked_claim_does_not_admit_feature_results_or_reuse_profile(self):
        assignment = self.seed_assignment()
        item = self.stored()
        record = recovery.active(item)
        assert record is not None
        record['parked'].append(assignment['assignment_id'])
        with self.db:
            save_iteration(self.db, item)
        self.assertTrue(recovery.released_claim(self.db, assignment))
        assignments.ticket_claims(self.db, {'issue_number': 99})
        with self.assertRaises(RepositoryError):
            assignments.require_result_active(assignment | {'recovery_disposition': 'incident'})
        with self.assertRaises(RepositoryError):
            assignments.claims(self.db, self.request(), assignment['ticket_scope'], 'other')

    def test_stuck_retry_waits_for_explicit_separate_debug_direction(self):
        assignment = self.seed_assignment()
        for status, response in [('diagnosis', None), ('awaiting-direction', None), ('release-pending', {'id': 'response'})]:
            item = self.stored()
            item['stuck_work'] = {'stuck': {'status': status, 'direction': {'action': 'retry'},
                'applied_response': response, 'retired_assignments': [assignment['assignment_id']]}}
            with self.db:
                save_iteration(self.db, item)
            request = self.request('repair-2') | {'preceding': recovery.preceding(recovery.active(item))}
            with self.assertRaises(RepositoryError):
                recovery.handoff(self.db, item, request)
            self.assertIsNotNone(self.sample(True)['incident'])
        item['stuck_work']['stuck']['status'] = 'released'
        with self.assertRaises(RepositoryError):
            recovery.handoff(self.db, item, request)  # A partial stored flag is not a real applied response.

    def test_report_requires_loop_rationale_exact_commands_and_authorized_removals(self):
        assignment, report, result, artifacts = self.completed_report()
        self.assertEqual(recovery.validate_result(assignment, result), report)
        for mutate in [lambda r: r.update(rationale=''), lambda r: r.update(removed_assignments=['f' * 64]),
                       lambda r: r['diagnosis'].update(feedback_loop=None),
                       lambda r: r['diagnosis']['commands'][0].update(result='green assertion'),
                       lambda r: r.update(scope=report['scope'] | {'paths': ['unrelated.py']})]:
            changed = copy.deepcopy(report)
            mutate(changed)
            altered = result | {'artifacts': [simplification.artifact('recovery-report', json.dumps(changed))]}
            with self.subTest(changed=changed), self.assertRaises(RepositoryError):
                recovery.validate_result(assignment, altered)
        (artifacts / 'workspace/unrelated.py').write_text('piggyback')
        with self.assertRaisesRegex(RepositoryError, 'unrelated'):
            recovery.validate_result(assignment, result)

    def test_recovery_merge_authority_cases(self):
        cases = [('valid', None), ('wrong-root', 'ancestry'), ('extra-path', 'authorized incident paths'),
                 ('host-removals', 'host removal assessment'), ('safe-revert', None)]
        for mode, error in cases:
            with self.subTest(mode=mode), component_case(RecoveryComponentFixture) as case:
                if mode == 'safe-revert':
                    prior, _ = case.delivered_ticket()
                assignment, report, result, artifacts = case.completed_report()
                removed = [prior['assignment_id']] if mode == 'safe-revert' else []
                if mode == 'safe-revert':
                    report.update(mode='safe-revert', removed_assignments=removed,
                        rationale='Red feedback identifies delivered regression; bounded revert restores main at cost of accepted behavior.')
                    result['artifacts'] = [simplification.artifact('recovery-report', json.dumps(report))]
                validated = recovery.validate_result(assignment, result)
                self.assertEqual(validated['mode'], 'safe-revert' if mode == 'safe-revert' else 'roll-forward')
                item = case.stored()
                config = {'recovery': {'incident_id': case.authorization['incident_id'],
                    'implementation_assignment': assignment['assignment_id'], 'issue': 20,
                    'removed_assignments': ['f' * 64] if mode == 'host-removals' else removed},
                    'baseline': 'a' * 40, 'issue': 20}
                if mode == 'wrong-root':
                    config['recovery']['implementation_assignment'] = '0' * 64
                elif mode == 'extra-path':
                    artifacts = case.root / 'simplified'
                    (artifacts / 'workspace').mkdir(parents=True)
                    (artifacts / 'inputs/baseline').mkdir(parents=True)
                    (artifacts / 'workspace/app.py').write_text('fixed')
                    (artifacts / 'workspace/unrelated.py').write_text('unrelated')
                    (artifacts / 'inputs/baseline/app.py').write_text('broken')
                if error:
                    with self.assertRaisesRegex(RepositoryError, error) as refusal:
                        recovery.merge_boundary(case.db, item, config, assignment, artifacts)
                    if mode == 'wrong-root':
                        self.assertEqual(refusal.exception.code, 'scope_mismatch')
                else:
                    self.assertEqual(recovery.merge_boundary(case.db, item, config, assignment, artifacts), report)

    def test_corrective_recovery_reassesses_final_choice_and_requires_original_native_binding(self):
        prior, _ = self.delivered_ticket()
        root, original_report, result, artifacts = self.completed_report()
        with self.db:
            self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(root), root['assignment_id']))
            self.db.execute('INSERT INTO role_assignments VALUES (?,?)', ('e' * 64, json.dumps({
                'assignment_id': 'e' * 64, 'handoff': {'stage': 'simplify',
                    'simplification': {'implementation_assignment': root['assignment_id']}}})))
        correction = copy.deepcopy(root)
        correction['assignment_id'] = 'd' * 64
        correction['handoff'].update(stage='corrections', corrections={'pins': {'candidate_assignment': 'e' * 64}})
        choice = {k: v for k, v in original_report.items() if k != 'diagnosis'}
        choice.update(mode='safe-revert', removed_assignments=[prior['assignment_id']], rationale='Independent rejection showed forward fix unsafe; bounded revert preferred.')
        with self.assertRaises(RepositoryError):
            recovery.validate_result(correction, correction['submitted_result'])
        correction['submitted_result']['artifacts'] = [simplification.artifact('recovery-choice', json.dumps(choice))]
        self.assertEqual(recovery.validate_result(correction, correction['submitted_result']), choice)
        config = {'operator_id': '42', 'recovery': {'incident_id': self.authorization['incident_id'],
                  'implementation_assignment': root['assignment_id'], 'issue': 20, 'removed_assignments': [prior['assignment_id']]},
                  'baseline': 'a' * 40, 'issue': 20}
        with patch.object(simplification, 'implementation') as root_checks, \
             patch.object(merge, 'execution_binding', return_value={'simulated': 'independent-native-root-binding'}):
            with self.assertRaisesRegex(RepositoryError, 'original diagnosis execution'):
                recovery.merge_boundary(self.db, self.stored(), config, correction, artifacts)
            config['recovery']['root_execution'] = {'simulated': 'independent-native-root-binding'}
            self.assertEqual(recovery.merge_boundary(self.db, self.stored(), config, correction, artifacts), choice)
            self.assertTrue(root_checks.called)
            config['recovery']['removed_assignments'] = []
            with self.assertRaisesRegex(RepositoryError, 'host removal assessment'):
                recovery.merge_boundary(self.db, self.stored(), config, correction, artifacts)

    def test_actual_merge_mutation_rechecks_scope_after_remote_admission(self):
        assignment, report, result, artifacts = self.completed_report()
        # Exercise only merge's final admission seam; no nested workers or public lifecycle setup.
        config = {'recovery': {'incident_id': self.authorization['incident_id'],
                              'implementation_assignment': assignment['assignment_id'], 'issue': 20, 'removed_assignments': []},
                  'candidate_assignment': assignment['assignment_id'], 'repository': 'example/product',
                  'repository_id': 7, 'api_base': self.github.base, 'issue': 20, 'head': 'b' * 40, 'baseline': 'a' * 40,
                  'base': 'main', 'branch': 'repair', 'standards_assignment': 's', 'spec_assignment': 's2', 'pr_number': 1}
        api = Mock(github=self.github)
        api.read.side_effect = lambda path: {'ref': 'refs/heads/' + ('main' if path.endswith('main') else 'repair'),
                                             'object': {'type': 'commit', 'sha': config['baseline'] if path.endswith('main') else config['head']}}
        api.prefix = '/repos/example/product'
        pr = {'base': {'sha': 'a' * 40}, 'state': 'open', 'merged': False, 'draft': False,
              'mergeable': True, 'mergeable_state': 'clean'}
        calls = []
        def publication(*args):
            if len(calls) == 1:
                (artifacts / 'workspace/unrelated.py').write_text('late piggyback')
            calls.append(args)
        with ExitStack() as stack:
            for name, value in [('load_config', config), ('controller_paths', None), ('MergeGitHub', api),
                                ('chain', (assignment, assignment, artifacts)), ('issue', self.issue),
                                ('mapped_candidate', {}), ('target', pr), ('protection', {}), ('checks', [])]:
                stack.enter_context(patch.object(merge, name, return_value=value))
            stack.enter_context(patch.object(merge, 'require_publication', side_effect=publication))
            stack.enter_context(patch.object(merge.review, 'evaluate', return_value={'status': 'accepted'}))
            stack.enter_context(patch.object(delivery, 'mutation_boundary', side_effect=lambda *a: self.stored()))
            with self.assertRaises(RepositoryError):
                merge.lifecycle(self.db, 'p', 'i', '42', self.config_file, 'merge-candidate')
        api.request.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_missing_host_registration_cannot_be_replaced_by_repair_role_or_merge_config(self):
        with self.assertRaises(RepositoryError):
            self.prepare()
        with self.assertRaises(RepositoryError):
            recovery.merge_boundary(self.db, self.stored(), {'recovery': {'incident_id': 'asserted'}}, {}, self.root)

    def test_removed_ticket_reopen_readback_cases(self):
        modes = ('lost-after', 'lost-before', 'issue-identity', 'blocked-before', 'blocked-patch',
                 'blocked-last-read', 'membership-id', 'membership-scope')
        for mode in modes:
            with self.subTest(mode=mode), component_case(RecoveryComponentFixture) as case:
                prior, issue = case.delivered_ticket()
                item = case.stored()
                item['ticket_work']['request']['tracker']['statuses']['blocked'] = 'Blocked'
                item['ticket_work']['board']['status']['options']['Blocked'] = 'B'
                item['ticket_work']['board']['scope'] = {'id': 'S', 'options': {'i': 'I'}}
                with case.db:
                    save_iteration(case.db, item)
                case.seed_registered()
                field, member = case.tracker_for_removed(issue, lost_after=mode == 'lost-after', lost_before=mode == 'lost-before')
                member['scope'] = 'I'
                if mode == 'blocked-before':
                    member['status'] = 'B'
                elif mode == 'issue-identity':
                    issue['id'] = 999
                elif mode == 'blocked-patch':
                    write = case.github.request.side_effect
                    def block(*args):
                        write(*args)
                        member['status'] = 'B'
                    case.github.request.side_effect = block
                reads, last_guard_injected = [], []
                def read_member(*args):
                    reads.append(True)
                    observed = copy.deepcopy(member)
                    if mode == 'blocked-last-read' and len(reads) == 3:
                        member['status'] = observed['status'] = 'B'
                        last_guard_injected.append(True)
                    if len(reads) > 1:
                        if mode == 'membership-id':
                            observed['id'] = 'replaced'
                        elif mode == 'membership-scope':
                            observed['scope'] = 'foreign'
                    return observed
                report = {'removed_assignments': [prior['assignment_id']]}
                error = mode in ('lost-before', 'issue-identity', 'membership-id', 'membership-scope')
                with patch.object(tickets.gh, 'membership', side_effect=read_member):
                    for _ in range(2 if mode in ('lost-after', 'lost-before') else 1):
                        item = case.stored()
                        if error:
                            with self.assertRaises(RepositoryError) as refusal:
                                recovery.reopen_removed(case.db, item, recovery.active(item), report, case.github)
                            if mode == 'issue-identity':
                                self.assertRegex(str(refusal.exception), 'identity|requirements')
                        else:
                            recovery.reopen_removed(case.db, item, recovery.active(item), report, case.github)
                if mode == 'lost-after':
                    self.assertEqual(case.github.request.call_count, 1)
                    self.assertEqual(field.call_count, 1)
                    self.assertEqual((issue['state'], member['status']), ('open', 'T'))
                    saved = next(a for a in assignments.rows(case.db) if a['assignment_id'] == prior['assignment_id'])
                    self.assertEqual(saved['merge']['failure']['code'], 'delivery_removed')
                    self.assertEqual(recovery.active(case.stored())['removed'][prior['assignment_id']]['dependencies'], [])
                    self.assertEqual(recovery.held_numbers(case.stored()), {10})
                    self.assertIsNotNone(case.sample(True)['incident'])
                else:
                    field.assert_not_called()
                    if mode == 'lost-before':
                        self.assertEqual(case.github.request.call_count, 1)
                    elif mode == 'issue-identity':
                        case.github.request.assert_not_called()
                    elif mode.startswith('blocked-'):
                        self.assertEqual(member['status'], 'B')
                        receipt = recovery.active(case.stored())['removed'][prior['assignment_id']]
                        self.assertEqual(receipt['membership']['status'], 'B')
                        if mode == 'blocked-patch':
                            self.assertFalse(receipt['status_intent'])
                        if mode == 'blocked-last-read':
                            self.assertEqual(last_guard_injected, [True])
                            self.assertTrue(receipt['status_intent'])
                    else:
                        self.assertEqual(len(reads), 2)
                        self.assertEqual(member['status'], 'D')




    def test_clearance_requires_merge_integrated_checks_and_fresh_health_after_readbacks(self):
        assignment, report, result, artifacts = self.completed_report()
        config = {'recovery': {'incident_id': self.authorization['incident_id'], 'implementation_assignment': assignment['assignment_id'],
                              'issue': 20, 'removed_assignments': []}, 'baseline': 'a' * 40, 'issue': 20,
                  'head': 'b' * 40, 'api_base': self.github.base, 'required_checks': self.config['required_checks']}
        journal = {'status': 'delivered', 'integrated_evidence': {'integrated': 'a' * 40, 'candidate': 'b' * 40}, 'delivery_checks': ['app-bound-pass']}
        def frontier(db, item, gh):
            item['ticket_work']['frontier'] = {'items': [], 'eligible': [], 'active': [], 'foreign_active': []}
            with db:
                save_iteration(db, item)
            return item
        with ExitStack() as stack:
            stack.enter_context(patch.object(merge, 'load_config', return_value=config))
            stack.enter_context(patch.object(merge, 'controller_paths'))
            lifecycle = stack.enter_context(patch.object(merge, 'lifecycle', return_value=journal))
            stack.enter_context(patch.object(merge, 'chain', return_value=(assignment, assignment, artifacts)))
            stack.enter_context(patch.object(tickets, 'frontier', side_effect=frontier))
            # A successful merge/check journal alone cannot clear unhealthy deployment.
            with patch.object(delivery, 'observe', return_value=self.evidence(False)), self.assertRaises(RepositoryError):
                recovery.verify(self.db, 'p', 'i', '42', self.config_file, self.github)
            self.assertIsNotNone(self.stored()['main_incident'])
            # Health can regress during affected-issue reads: the final observation is mandatory.
            with patch.object(delivery, 'observe', side_effect=[self.evidence(True), self.evidence(False)]), self.assertRaises(RepositoryError):
                recovery.verify(self.db, 'p', 'i', '42', self.config_file, self.github)
            self.assertIsNotNone(self.stored()['main_incident'])
            def interrupted_save(db, item):
                if 'main_incident' not in item:
                    raise RuntimeError('SIMULATED death after journal SQL, before clearance commit')
                save_iteration(db, item)
            with patch.object(delivery, 'observe', return_value=self.evidence(True)), \
                 patch.object(recovery, 'save_iteration', side_effect=interrupted_save), self.assertRaises(RuntimeError):
                recovery.verify(self.db, 'p', 'i', '42', self.config_file, self.github)
            delivery.inspect(self.db, 'p', 'i', '42')
            self.assertIsNotNone(self.stored()['main_incident'])
            with patch.object(delivery, 'observe', return_value=self.evidence(True)):
                cleared = recovery.verify(self.db, 'p', 'i', '42', self.config_file, self.github)
            self.assertEqual(cleared['status'], 'cleared')
            self.assertEqual(cleared['clearance']['checks'], journal['delivery_checks'])
            self.assertNotIn('main_incident', self.stored())
            delivery.inspect(self.db, 'p', 'i', '42')
            count = lifecycle.call_count
            self.assertEqual(recovery.verify(self.db, 'p', 'i', '42', self.config_file, self.github), cleared)
            self.assertEqual(lifecycle.call_count, count, 'Completed clearance replay must not repeat side effects')

    def test_production_cli_register_inspect_and_prepare_dispatch_without_external_services(self):
        import contextlib
        import io
        import sys
        from factory_v1 import __main__ as cli
        for command in ('register-recovery', 'inspect-recovery', 'prepare-recovery', 'verify-recovery'):
            with self.subTest(command=command), component_case(RecoveryComponentFixture) as case:
                state = case.root / 'state.sqlite'
                Path(str(state) + '.onboarding.lock').touch(mode=0o600)
                if command != 'register-recovery':
                    case.seed_registered()
                request_file = case.root / 'request.json'
                request_file.write_text(json.dumps(case.request()))
                extra = {'register-recovery': ['--recovery-config', str(case.config_file), '--api-base', case.github.base],
                    'inspect-recovery': [],
                    'prepare-recovery': ['--request', str(request_file), '--api-base', case.github.base],
                    'verify-recovery': ['--merge-config', str(case.config_file), '--api-base', case.github.base]}[command]
                stdout = io.StringIO()
                argv = ['factory_v1', '--state', str(state), '--operator-id', '42', command, '--project', 'p', '--iteration', 'i', *extra]
                with patch.object(sys, 'argv', argv), patch('factory_v1.repositories.GitHub', return_value=case.github), \
                     case.preparation_seams(), patch.object(recovery, 'verify', return_value={'status': 'simulated-verifier-boundary'}) as verifier, \
                     contextlib.redirect_stdout(stdout):
                    cli.main()
                payload = json.loads(stdout.getvalue())
                self.assertTrue(payload)
                if command == 'inspect-recovery':
                    self.assertEqual(payload['incident']['recovery_issue'], 20)
                elif command == 'prepare-recovery':
                    self.assertEqual(payload['handoff']['stage'], 'repair')
                    self.assertEqual(case.stored()['main_incident']['recovery_assignment'], payload['assignment_id'])
                elif command == 'verify-recovery':
                    self.assertEqual(payload['status'], 'simulated-verifier-boundary')
                    verifier.assert_called_once()
                if command != 'verify-recovery':
                    verifier.assert_not_called()
                case.github.request.assert_not_called()

    def test_authoritative_frontier_admits_only_registered_recovery_without_erasing_other_holds(self):
        item = self.stored()
        work = item['ticket_work']
        work['inputs']['handoff']['issue'] = {'number': 3}
        work['board']['scope'] = {'id': 'S', 'options': {'i': 'I'}}
        work['board']['status']['options']['Doing'] = 'A'
        work['request']['tracker'].update(triage_label='ready')
        work['request']['tracker']['statuses']['active'] = 'Doing'
        work['request']['skill'] = {}
        self.issue.update(body='## Bug scope\n\nrecovery\n\n## Blocked by\n\n', labels=[{'name': 'ready'}])
        self.pin['body_sha256'] = hashlib.sha256(self.issue['body'].encode()).hexdigest()
        self.authorization['issue'] = self.pin
        self.config_file.write_text(json.dumps(self.authorization))
        feature = self.issue | {'number': 10, 'id': 100, 'node_id': 'I10', 'body': '## Blocked by\n\n'}
        self.reader.side_effect = lambda g, r, n: copy.deepcopy(self.issue if n == 20 else feature)
        members = [{'id': 'M' + str(n), 'content': {'repository': {'nameWithOwner': 'example/product'}, 'number': n, 'id': node},
                    'values': {'S': scope, 'F': status}} for n, node, scope, status in [(20, 'I20', None, 'T'), (10, 'I10', 'I', 'A')]]
        with self.db:
            save_iteration(self.db, item)
        with ExitStack() as stack:
            stack.enter_context(patch.object(tickets, 'skill'))
            stack.enter_context(patch.object(tickets, 'pinned_inputs', return_value=work['inputs']))
            stack.enter_context(patch.object(tickets, 'verified_board', return_value=work['board']))
            contract_probe = stack.enter_context(patch.object(tickets, 'external_contract', return_value='ready'))
            stack.enter_context(patch.object(tickets.gh, 'project_items', return_value=members))
            stack.enter_context(patch.object(tickets.gh, 'single_select_values', side_effect=lambda m: m['values']))
            stack.enter_context(patch.object(tickets.gh, 'dependencies', return_value=[]))
            before = tickets.frontier(self.db, self.stored(), self.github)['ticket_work']['frontier']
            self.assertEqual(before['eligible'], [])
            self.assertEqual([r['number'] for r in before['items']], [10])
            self.seed_registered()
            admitted = tickets.frontier(self.db, self.stored(), self.github)['ticket_work']['frontier']
            self.assertEqual(admitted['eligible'], [20])
            self.assertTrue(any(len(call.args) == 3 and call.args[0]['number'] == 20 and
                                call.args[2] == ('vision.md', 'milestone.md') for call in contract_probe.call_args_list))
            self.assertFalse(next(r for r in admitted['items'] if r['number'] == 10)['admissible'])
            for target in ('factory_v1.requirements.held_numbers', 'factory_v1.bugs.held_numbers', 'factory_v1.diagnosis.held_numbers'):
                with patch(target, return_value={20}):
                    held = tickets.frontier(self.db, self.stored(), self.github)['ticket_work']['frontier']
                self.assertEqual(held['eligible'], [])
                self.assertTrue(next(r for r in held['items'] if r['number'] == 20)['held'])
            for field, value in [('body', 'changed registered requirements'), ('id', 999)]:
                old = self.issue[field]
                self.issue[field] = value
                with self.assertRaisesRegex(RepositoryError, 'Registered incident issue changed'):
                    tickets.frontier(self.db, self.stored(), self.github)
                self.issue[field] = old
