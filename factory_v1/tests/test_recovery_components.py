"""Small, deterministic recovery fixtures; no worker, HTTP server or model is started."""
import copy
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


class RecoveryComponents(RecoveryFixture):
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
        self.sample(False)
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
        return stack

    def prepare(self, claim='repair-1', dry_run=False):
        with self.preparation_seams():
            return recovery.prepare(self.db, 'p', 'i', '42', self.request(claim), self.github, dry_run)

    def completed_report(self):
        self.register()
        assignment = self.prepare()
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

    def test_private_registration_is_durable_idempotent_and_pins_real_observation(self):
        record = self.register()
        self.assertEqual(record, self.register())
        self.assertEqual(record['evidence'], self.evidence(False))
        self.assertEqual(self.stored()['main_incident']['recovery_issue'], 20)
        self.assertIsNotNone(self.sample(True)['incident'])
        for mutation in ({'paths': ['other.py']}, {'evidence_id': '0' * 64}):
            self.config_file.write_text(json.dumps(self.authorization | mutation))
            with self.assertRaises(RepositoryError):
                self.register()
        self.github.request.assert_not_called()

    def test_worker_mount_or_public_config_cannot_grant_recovery(self):
        self.config_file.chmod(0o644)
        with self.assertRaisesRegex(RepositoryError, 'Private'):
            self.register()
        self.config_file.chmod(0o600)
        self.db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', ('x', json.dumps({
            'handoff': {'workspace': str(self.root), 'profile': {'home': '/fixture-home'}}})))
        self.db.commit()
        with self.assertRaisesRegex(RepositoryError, 'outside'):
            self.register()

    def test_fresh_repair_handoff_and_claim_replay_with_feature_priority(self):
        self.register()
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
        self.register()
        assignment = self.prepare()
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
        self.register()
        assignment = self.prepare()
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
        self.assertEqual(recovery.handoff(self.db, item, request), assignment['handoff']['recovery'])

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

    def test_final_merge_boundary_checks_simplified_bytes_and_exact_host_scope(self):
        assignment, report, result, artifacts = self.completed_report()
        item = self.stored()
        config = {'recovery': {'incident_id': item['main_incident']['incident_id'],
                              'implementation_assignment': assignment['assignment_id'], 'issue': 20, 'removed_assignments': []},
                  'baseline': 'a' * 40, 'issue': 20}
        self.assertEqual(recovery.merge_boundary(self.db, item, config, assignment, artifacts), report)
        config['recovery']['implementation_assignment'] = '0' * 64
        with self.assertRaises(RepositoryError):
            recovery.merge_boundary(self.db, item, config, assignment, artifacts)
        config['recovery']['implementation_assignment'] = assignment['assignment_id']
        simplified = self.root / 'simplified'
        (simplified / 'workspace').mkdir(parents=True)
        (simplified / 'inputs/baseline').mkdir(parents=True)
        (simplified / 'workspace/app.py').write_text('fixed')
        (simplified / 'workspace/unrelated.py').write_text('unrelated')
        (simplified / 'inputs/baseline/app.py').write_text('broken')
        with self.assertRaisesRegex(RepositoryError, 'authorized incident paths'):
            recovery.merge_boundary(self.db, item, config, assignment, simplified)

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

    def test_safe_revert_reopens_exact_ticket_and_status_with_lost_response_reconciliation(self):
        prior, old_issue = self.delivered_ticket()
        assignment, report, result, artifacts = self.completed_report()
        report.update(mode='safe-revert', removed_assignments=[prior['assignment_id']],
                      rationale='Red feedback identifies delivered regression; bounded revert restores main at cost of accepted behavior.')
        result['artifacts'] = [simplification.artifact('recovery-report', json.dumps(report))]
        self.assertEqual(recovery.validate_result(assignment, result)['mode'], 'safe-revert')
        config = {'recovery': {'incident_id': self.authorization['incident_id'], 'implementation_assignment': assignment['assignment_id'],
                              'issue': 20, 'removed_assignments': [prior['assignment_id']]}, 'baseline': 'a' * 40, 'issue': 20}
        self.assertEqual(recovery.merge_boundary(self.db, self.stored(), config, assignment, artifacts), report)
        field, member = self.tracker_for_removed(old_issue, lost_after=True)
        for _ in range(2):
            item = self.stored()
            record = recovery.active(item)
            recovery.reopen_removed(self.db, item, record, report, self.github)
        self.assertEqual(self.github.request.call_count, 1)
        self.assertEqual(field.call_count, 1)
        self.assertEqual((old_issue['state'], member['status']), ('open', 'T'))
        saved = next(a for a in assignments.rows(self.db) if a['assignment_id'] == prior['assignment_id'])
        self.assertEqual(saved['merge']['failure']['code'], 'delivery_removed')
        record = recovery.active(self.stored())
        assert record is not None
        self.assertEqual(record['removed'][prior['assignment_id']]['dependencies'], [])
        self.assertEqual(recovery.held_numbers(self.stored()), {10})
        self.assertIsNotNone(self.sample(True)['incident'])

    def test_unknown_reopen_never_blindly_duplicates_and_changed_identity_never_writes(self):
        prior, old_issue = self.delivered_ticket()
        self.register()
        field, member = self.tracker_for_removed(old_issue, lost_before=True)
        report = {'removed_assignments': [prior['assignment_id']]}
        for _ in range(2):
            item = self.stored()
            with self.assertRaises(RepositoryError):
                recovery.reopen_removed(self.db, item, recovery.active(item), report, self.github)
        self.assertEqual(self.github.request.call_count, 1)
        field.assert_not_called()
        old_issue['id'] = 999
        item = self.stored()
        with self.assertRaisesRegex(RepositoryError, 'identity|requirements'):
            recovery.reopen_removed(self.db, item, recovery.active(item), report, self.github)
        self.assertEqual(self.github.request.call_count, 1)

    def test_worker_removal_list_cannot_override_host_merge_assessment(self):
        assignment, report, result, artifacts = self.completed_report()
        item = self.stored()
        config = {'recovery': {'incident_id': self.authorization['incident_id'], 'implementation_assignment': assignment['assignment_id'],
                              'issue': 20, 'removed_assignments': ['f' * 64]}, 'baseline': 'a' * 40, 'issue': 20}
        with self.assertRaisesRegex(RepositoryError, 'host removal assessment'):
            recovery.merge_boundary(self.db, item, config, assignment, artifacts)

    def test_reopening_preserves_independently_blocked_progress(self):
        prior, old_issue = self.delivered_ticket()
        item = self.stored()
        item['ticket_work']['request']['tracker']['statuses']['blocked'] = 'Blocked'
        item['ticket_work']['board']['status']['options']['Blocked'] = 'B'
        with self.db:
            save_iteration(self.db, item)
        self.register()
        field, member = self.tracker_for_removed(old_issue)
        member['status'] = 'B'
        item = self.stored()
        recovery.reopen_removed(self.db, item, recovery.active(item), {'removed_assignments': [prior['assignment_id']]}, self.github)
        field.assert_not_called()
        self.assertEqual(member['status'], 'B')

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
        state = self.root / 'state.sqlite'
        Path(str(state) + '.onboarding.lock').touch(mode=0o600)
        for command, extra in [('register-recovery', ['--recovery-config', str(self.config_file), '--api-base', self.github.base]),
                               ('inspect-recovery', [])]:
            stdout = io.StringIO()
            argv = ['factory_v1', '--state', str(state), '--operator-id', '42', command, '--project', 'p', '--iteration', 'i', *extra]
            with patch.object(sys, 'argv', argv), patch('factory_v1.repositories.GitHub', return_value=self.github), contextlib.redirect_stdout(stdout):
                cli.main()
            payload = json.loads(stdout.getvalue())
            self.assertTrue(payload)
        self.assertEqual(payload['incident']['recovery_issue'], 20)
        request_file = self.root / 'request.json'
        request_file.write_text(json.dumps(self.request()))
        for command, option, value in [('prepare-recovery', '--request', request_file), ('verify-recovery', '--merge-config', self.config_file)]:
            stdout = io.StringIO()
            argv = ['factory_v1', '--state', str(state), '--operator-id', '42', command, '--project', 'p', '--iteration', 'i',
                    option, str(value), '--api-base', self.github.base]
            with patch.object(sys, 'argv', argv), patch('factory_v1.repositories.GitHub', return_value=self.github), \
                 self.preparation_seams(), patch.object(recovery, 'verify', return_value={'status': 'simulated-verifier-boundary'}) as verifier, \
                 contextlib.redirect_stdout(stdout):
                cli.main()
            payload = json.loads(stdout.getvalue())
            if command == 'prepare-recovery':
                self.assertEqual(payload['handoff']['stage'], 'repair')
                self.assertEqual(self.stored()['main_incident']['recovery_assignment'], payload['assignment_id'])
                verifier.assert_not_called()
            else:
                self.assertEqual(payload['status'], 'simulated-verifier-boundary')
                verifier.assert_called_once()
        self.github.request.assert_not_called()

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
            self.register()
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
