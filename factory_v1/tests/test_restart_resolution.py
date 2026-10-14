"""Exact effect continuation components, with explicit rows and mocked boundaries."""
import copy
import hashlib
import io
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from factory_v1 import assignments, bugs, controls, effect_resolution, publication, repositories, restart, tickets
from factory_v1.__main__ import main
from factory_v1.errors import IntakeError
from factory_v1.repositories import RepositoryError
from factory_v1.tests import test_bug_admission as bug_fixtures
from factory_v1.tests import test_restart_effects as effects
from factory_v1.tests import test_recovery_registration as recovery_fixtures


def fixture(case, cls):
    value = cls('runTest')
    case.addCleanup(value.doCleanups)
    value.setUp()
    return value


def blocked(boundary, code='publication_uncertain'):
    return {'status': 'blocked', 'observations': {}, 'blockers': [{'boundary': boundary, 'code': code}]}


class PublicRecoveryRegistrationTests(unittest.TestCase):
    def test_public_host_registration_preserves_restart_hold_and_blocks_ordinary_admission(self):
        f = fixture(self, recovery_fixtures.RecoveryRegistrationTests)
        item = f.stored()
        receipt = blocked('delivery', 'main_frozen')
        item['restart_reconciliation'] = receipt
        f.save(item)
        output = io.StringIO()
        argv = ['factory', '--state', str(f.root / 'state.db'), '--operator-id', 'operator',
                'register-recovery', '--project', 'p', '--iteration', 'i', '--api-base', f.github.base,
                '--recovery-config', str(f.config_file)]
        with patch('sys.argv', argv), patch.object(repositories, 'GitHub', return_value=f.github), redirect_stdout(output):
            main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'registered')
        self.assertEqual(result['config'], f.authorization)
        self.assertEqual(f.stored()['restart_reconciliation'], receipt)
        self.assertEqual(f.stored()['main_incident']['recovery_issue'], 20)
        with self.assertRaises(RepositoryError) as caught:
            controls.require_active(f.db, 'p', 'i')
        self.assertEqual(caught.exception.code, 'reconciliation_required')
        f.github.request.assert_not_called()


class TicketResolutionTests(unittest.TestCase):
    def setUp(self):
        self.f = effects.FrontierFixture(self)
        self.f.publication()  # Only explicit dictionaries/rows, not lifecycle execution.
        ticket = self.f.work['tickets'][0]
        body = tickets.body(self.f.item, ticket, {})
        self.f.issues[10] = self.f.issue(10, body)
        self.f.work['intents'] = {'note': {'issue': {'title': ticket['title'], 'body': body},
                                         'identity': {k: self.f.issues[10][k] for k in ('number', 'id', 'node_id')}}}
        self.f.item['restart_reconciliation'] = blocked('tracker')
        self.f.persist()
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            self.enterContext(patch(target, side_effect=AssertionError('External execution forbidden')))

    def publish(self, operator='42'):
        argv = ['factory', '--state', str(self.f.state), '--operator-id', operator,
                'publish-tickets', '--project', 'product', '--iteration', 'm1', '--api-base', self.f.github.base]
        output = io.StringIO()
        with patch('sys.argv', argv), patch.object(repositories, 'GitHub', return_value=self.f.github), redirect_stdout(output):
            main()
        return json.loads(output.getvalue())

    def test_public_partial_issue_finishes_only_unadmitted_membership_and_requires_fresh_restart(self):
        receipt = copy.deepcopy(self.f.item['restart_reconciliation'])
        completed = self.publish()
        self.assertTrue(completed['ticket_work']['publication_complete'])
        self.assertEqual(completed['restart_reconciliation'], receipt)
        self.assertEqual(completed['ticket_work']['published']['note'], {'number': 10, 'id': 1010, 'node_id': 'I10'})
        self.assertEqual(set(self.f.issues), {10})
        self.assertEqual(self.f.members[10]['fields'], {'SCOPE': 'M1', 'STATUS': 'Ready'})
        self.f.github.request.assert_not_called()
        self.f.add_dependency.assert_not_called()
        writes = self.f.add_item.call_count, self.f.set_field.call_count
        replay = self.publish()
        self.assertTrue(replay['ticket_work']['publication_complete'])
        self.assertEqual((self.f.add_item.call_count, self.f.set_field.call_count), writes)
        with self.assertRaises(RepositoryError) as caught:
            controls.require_active(self.f.database, 'product', 'm1')
        self.assertEqual(caught.exception.code, 'reconciliation_required')
        for ordinary in (tickets.synthesize, tickets.complete_synthesis):
            with self.assertRaises(RepositoryError) as caught:
                ordinary(self.f.database, completed, {}, self.f.github)
            self.assertEqual(caught.exception.code, 'reconciliation_required')
            self.f.database.rollback()
        self.assertNotIn('reservation', self.f.stored())
        with patch.object(restart.repositories, 'reconcile', return_value=completed), repositories.state_lock(self.f.database):
            report = restart.reconcile(self.f.database, 'product', 'm1', '42', self.f.github)
        self.assertEqual(report['status'], 'ready')
        controls.require_active(self.f.database, 'product', 'm1')

    def test_unknown_membership_and_issue_effects_do_not_repeat_writes(self):
        for missing in ('membership', 'issue'):
            with self.subTest(missing=missing):
                if missing == 'membership':
                    self.f.work['intents']['note']['membership'] = True
                else:
                    self.f.issues.clear()
                self.f.persist()
                result = self.publish()
                self.assertFalse(result['ticket_work'].get('publication_complete'))
                self.assertEqual(result['ticket_work']['blocker']['code'], 'publication_uncertain')
                self.f.add_item.assert_not_called()
                self.f.github.request.assert_not_called()

    def test_wrong_issue_identity_or_pinned_inputs_cannot_complete(self):
        self.f.issues[10]['id'] = 999
        result = self.publish()
        self.assertEqual(result['ticket_work']['blocker']['code'], 'publication_mismatch')
        self.f.add_item.assert_not_called()
        self.f.issues[10]['id'] = 1010
        with patch.object(tickets, 'pinned_inputs', return_value={}):
            with self.assertRaises(IntakeError) as caught:
                self.publish()
        self.assertEqual(caught.exception.code, 'ticket_conflict')
        self.f.github.request.assert_not_called()

    def test_pause_unrelated_and_malformed_holds_refuse_public_continuation(self):
        original = copy.deepcopy(self.f.item['restart_reconciliation'])
        for report, code in [(blocked('model', 'operation_uncertain'), 'reconciliation_required'),
                             (blocked('delivery', 'main_frozen'), 'reconciliation_required'),
                             (original | {'blockers': original['blockers'] + [{'boundary': 'worker', 'code': 'worker_uncertain'}]}, 'reconciliation_required'),
                             ({'status': 'blocked'}, 'invalid_restart')]:
            self.f.item['restart_reconciliation'] = report
            self.f.persist()
            with self.subTest(report=report), self.assertRaises(IntakeError) as caught:
                self.publish()
            self.assertEqual(caught.exception.code, code)
            self.f.add_item.assert_not_called()
        self.f.item['restart_reconciliation'] = original
        self.f.persist()
        self.f.pause()
        with self.assertRaises(IntakeError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.f.add_item.assert_not_called()

    def test_missing_wrong_and_stale_scoped_authority_is_not_dispatch_permission(self):
        authority = effect_resolution.bound(self.f.database, self.f.stored(), 'ticket-publication', self.f.work['assignment_id'])
        for forged in (True, replace(authority, target='wrong'), replace(authority, project='other'),
                       replace(authority, operator='wrong'), replace(authority, authority='0' * 64)):
            with self.subTest(forged=forged), self.assertRaises(RepositoryError):
                controls.require_active(self.f.database, 'product', 'm1', resolution=forged)
        with self.assertRaises(RepositoryError):
            controls.require_active(self.f.database, 'product', 'm1')
        self.f.work['execution']['run_id'] = 'changed'
        self.f.persist()
        with self.assertRaises(RepositoryError) as caught:
            controls.require_active(self.f.database, 'product', 'm1', resolution=authority)
        self.assertEqual(caught.exception.code, 'stale_admission')

    def test_pause_at_last_membership_read_keeps_receipt_and_no_new_fields(self):
        original = self.f.membership
        paused = False
        def read(*args):
            nonlocal paused
            member = original(*args)
            if member is not None and not paused:
                self.f.pause()
                paused = True
            return member
        with patch.object(tickets.gh, 'membership', side_effect=read), self.assertRaises(IntakeError) as caught:
            self.publish()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertTrue(paused)
        self.assertIn(10, self.f.members)
        self.f.set_field.assert_not_called()
        self.assertFalse(self.f.stored()['ticket_work'].get('publication_complete'))


class CandidateResolutionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture(self, effects.CandidateRestartTests)
        f = self.f
        f.root.chmod(0o700)
        self.state = f.root / 'state.sqlite'
        artifacts = f.root / 'artifacts'
        artifacts.mkdir(mode=0o700)
        f.assignment['runtime'].update(artifacts=str(artifacts), controller_pid=999999999, controller_start='exited-owner')
        self.db = sqlite3.connect(self.state)
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT); CREATE TABLE role_assignments (assignment_id TEXT, payload TEXT);')
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'owner/repo', 'status': 'active',
                     'execution_allowed': False, 'approval': {'operator_id': 'operator', 'reference': 'SIMULATED'},
                     'repository_onboarding': {'api_base': f.api.base, 'metadata': {'id': 123}},
                     'handoff': {'commit': 'c' * 40}, 'restart_reconciliation': blocked(f.assignment['assignment_id'] + ':publication')}
        h = f.assignment['handoff']
        h.update(workspace=str(f.root / 'assigned'), skills=[], capabilities=['write_workspace'], tracker={'api_base': f.api.base})
        h['profile'].update(role='implementation', home=str(f.root / 'profile'))
        h['issue']['body_sha256'] = hashlib.sha256(b'Pinned work').hexdigest()
        f.assignment['handoff_digest'] = assignments.digest(h)
        f.assignment['revision'] = {'spec': self.item['handoff'], 'ticket_assignment': None, 'ticket_inputs': None}
        f.assignment['ticket_scope'] = {'project': 'p', 'iteration': 'i', 'repository': 'owner/repo', 'repository_id': 123, 'issue_number': 10, 'issue_id': 100}
        f.assignment['result_disposition'] = {'isolated_execution': True}
        f.config.update(operator_id='operator', reference='SIMULATED', execution_reference='SIMULATED host verification',
                        manifest={}, paths=['app.py'], policy_paths=[], commit_date='2026-01-01T00:00:00Z', bearer='fixture', binding=publication.binding(f.assignment))
        f.assignment['publication']['approval'] = {k: v for k, v in f.config.items() if k != 'bearer'}
        f.plan['changes'] = []
        f.values[''] = {'id': 123, 'full_name': 'owner/repo', 'default_branch': 'main'}
        f.values['/issues/10'] = {'number': 10, 'id': 100, 'node_id': 'I10', 'state': 'open', 'body': 'Pinned work', 'repository_url': 'https://api.github.com/repos/owner/repo'}
        f.values['/git/ref/heads/main'] = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'a' * 40}}
        f.values[publication.pr_query(f.config)] = []
        self.config = f.root / 'authorization.json'
        self.config.write_text(json.dumps(f.config)); self.config.chmod(0o600)
        self.persist()
        (f.root / 'state.sqlite.onboarding.lock').touch(mode=0o600)
        self.writes = []
        def request(github, path, payload=None, method=None):
            suffix = path.removeprefix('/repos/owner/repo')
            if payload is not None:
                self.writes.append((suffix, payload))
                self.assertEqual(suffix, '/pulls')
                f.values[publication.pr_query(f.config)] = [f.pr]
                return copy.deepcopy(f.pr)
            return copy.deepcopy(f.values[suffix])
        self.transport = request
        self.enterContext(patch.object(publication.GitHub, 'request', request))
        self.enterContext(patch('subprocess.run', side_effect=AssertionError('No process execution')))

    def persist(self):
        with self.db:
            self.db.execute('DELETE FROM iterations')
            self.db.execute('DELETE FROM role_assignments')
            self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(self.item)))
            self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (self.f.assignment['assignment_id'], json.dumps(self.f.assignment)))

    def publish(self):
        argv = ['factory', '--state', str(self.state), '--operator-id', 'operator', 'publish-candidate',
                '--project', 'p', '--iteration', 'i', '--assignment', self.f.assignment['assignment_id'], '--publication-config', str(self.config)]
        output = io.StringIO()
        with patch('sys.argv', argv), redirect_stdout(output):
            main()
        return json.loads(output.getvalue())

    def test_public_pending_candidate_finishes_exact_pr_without_rewriting_candidate_or_dispatch(self):
        result = self.publish()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['candidate'], 'b' * 40)
        self.assertEqual(result['pr_number'], 20)
        self.assertFalse(result['close_allowed']); self.assertFalse(result['merge_allowed'])
        self.assertEqual([p for p, _ in self.writes], ['/pulls'])
        self.publish()
        self.assertEqual([p for p, _ in self.writes], ['/pulls'])
        with self.assertRaises(RepositoryError):
            controls.require_active(self.db, 'p', 'i')

    def test_missing_wrong_stale_authority_pause_and_unrelated_holds_do_not_write(self):
        original = copy.deepcopy(self.f.config)
        for mutation in ({'candidate': 'f' * 40}, {'binding': {}}, {'operator_id': 'wrong'}):
            self.config.write_text(json.dumps(original | mutation))
            with self.subTest(mutation=mutation), self.assertRaises(IntakeError):
                self.publish()
            self.assertEqual(self.writes, [])
        self.config.write_text(json.dumps(original)); self.config.chmod(0o644)
        with self.assertRaises(IntakeError): self.publish()
        self.config.chmod(0o600)
        self.item['restart_reconciliation']['blockers'].append({'boundary': 'model', 'code': 'operation_uncertain'})
        self.persist()
        with self.assertRaises(IntakeError): self.publish()
        self.item['restart_reconciliation']['blockers'].pop()
        self.persist()
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'SIMULATED pause')
        with self.assertRaises(IntakeError) as caught: self.publish()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(self.writes, [])


    def test_pause_at_last_pr_read_prevents_scoped_intent_or_write(self):
        paused = False
        def read(github, path, payload=None, method=None):
            nonlocal paused
            result = self.transport(github, path, payload, method)
            if path.endswith(publication.pr_query(self.f.config)) and not paused:
                controls.request(self.db, 'p', 'i', 'operator', 'pause', 'SIMULATED last PR read')
                paused = True
            return result
        with patch.object(publication.GitHub, 'request', read), self.assertRaises(IntakeError) as caught:
            self.publish()
        self.assertTrue(paused)
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(self.writes, [])
        stored = json.loads(self.db.execute('SELECT payload FROM role_assignments').fetchone()[0])
        self.assertEqual(stored['publication'], self.f.assignment['publication'])


class BugResolutionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture(self, bug_fixtures.BugAdmissionTests)
        f = self.f
        self.state = f.database.execute('PRAGMA database_list').fetchone()[2]
        f.item['repository_onboarding'].update(api_base='https://api.github.com', metadata={'id': 123})
        f.item['restart_reconciliation'] = blocked('tracker')
        f.item['restart_reconciliation']['blockers'].append({'boundary': 'bug-publication', 'code': 'publication_uncertain'})
        f.work['issue_intent'] = {'title': f.issue['title'], 'body': f.issue['body']}
        f.work['identity'] = {k: f.issue[k] for k in ('number', 'id', 'node_id')}
        before = f.affected['body']
        f.affected['body'] = before.replace('None (can start immediately).', '- #13')
        f.dependencies[10] = [copy.deepcopy(f.issue)]
        f.work['affected_intents'] = {'10': {'before': before, 'body': f.affected['body']}}
        self.member = None
        def membership(_github, _board, _repo, issue):
            return copy.deepcopy(self.member if issue['number'] == 13 else {'id': 'M10', 'status': 'todo'})
        f.membership.side_effect = membership
        def add(*args):
            self.member = {'id': 'M13', 'status': None}
        f.add_item.side_effect = add
        f.set_field.side_effect = lambda *args: self.member.update(status=args[-1])
        f.save()
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            self.enterContext(patch(target, side_effect=AssertionError('No external execution')))

    def publish(self):
        request = self.f.database.execute('PRAGMA database_list').fetchone()[2] + '.request.json'
        from pathlib import Path
        Path(request).write_text(json.dumps({'assignment_id': self.f.work['assignment_id']}))
        argv = ['factory', '--state', self.state, '--operator-id', 'operator', 'publish-bug',
                '--project', 'p1', '--iteration', 'm1', '--request', request, '--api-base', 'https://api.github.com']
        output = io.StringIO()
        with patch('sys.argv', argv), patch.object(repositories, 'GitHub', return_value=self.f.github), redirect_stdout(output):
            main()
        return json.loads(output.getvalue())

    def test_partial_known_bug_finishes_only_missing_membership_under_exact_scope(self):
        result = self.publish()
        self.assertTrue(result['bug_work']['defect']['publication_complete'])
        self.assertEqual(result['bug_work']['defect']['identity'], {'number': 13, 'id': 1013, 'node_id': 'I13'})
        self.assertEqual(self.member, {'id': 'M13', 'status': 'todo'})
        self.f.github.request.assert_not_called(); self.f.add_dependency.assert_not_called()
        count = self.f.add_item.call_count, self.f.set_field.call_count
        self.publish()
        self.assertEqual((self.f.add_item.call_count, self.f.set_field.call_count), count)
        self.assertEqual(result['restart_reconciliation']['status'], 'blocked')
        with self.assertRaises(RepositoryError): controls.require_active(self.f.database, 'p1', 'm1')

    def test_unknown_membership_and_other_archived_effects_cannot_repeat_or_expand(self):
        self.f.work['membership_intent'] = True
        self.f.save()
        result = self.publish()
        self.assertEqual(result['bug_work']['defect']['blocker']['code'], 'publication_uncertain')
        self.f.add_item.assert_not_called()
        self.f.work.pop('membership_intent')
        self.f.item['bug_work_history'] = [{'assignment_id': 'old', 'issue_intent': {'title': 'uncertain'}}]
        self.f.save()
        completed = self.publish()
        self.assertTrue(completed['bug_work']['defect']['publication_complete'])
        self.assertEqual(completed['bug_work_history'], self.f.item['bug_work_history'])
        self.assertEqual(completed['restart_reconciliation']['status'], 'blocked')
        self.f.github.request.assert_not_called()
        with self.assertRaises(RepositoryError): controls.require_active(self.f.database, 'p1', 'm1')

    def test_retirement_unrelated_holds_and_pause_refuse_bug_continuation(self):
        original = copy.deepcopy(self.f.item['restart_reconciliation'])
        self.f.item['restart_reconciliation']['blockers'].append({'boundary': 'delivery', 'code': 'main_frozen'})
        self.f.save()
        with self.assertRaises(IntakeError): self.publish()
        self.f.item['restart_reconciliation'] = original
        self.f.work['requirement_disposition'] = {'change_id': 'retired'}
        self.f.save()
        with self.assertRaises(IntakeError) as caught: self.publish()
        self.assertEqual(caught.exception.code, 'stale_requirements')
        self.f.work.pop('requirement_disposition'); self.f.save()
        self.f.pause(None)
        with self.assertRaises(IntakeError) as caught: self.publish()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.f.add_item.assert_not_called(); self.f.github.request.assert_not_called()


class IntegratedBranchResolutionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture(self, effects.CandidateRestartTests)
        self.f.merged()  # Exact PR/commit/check dictionaries, no integration precursor.
        self.f.values['/git/ref/heads/factory/issue-10'] = None

    def test_deleted_branch_requires_exact_integration_and_never_merge_or_closure(self):
        result = restart.candidate(self.f.assignment, self.f.api)
        self.assertEqual(result, {'candidate': 'b' * 40, 'tree': 'c' * 40, 'pr_number': 20})
        self.assertEqual(self.f.assignment['merge']['integrated_evidence']['integrated'], 'f' * 40)
        self.assertFalse(self.f.assignment['merge']['close_allowed'])
        self.f.api.write.assert_not_called(); self.f.api.issue_state.assert_not_called()

    def test_missing_unmerged_wrong_tree_ancestry_checks_or_authority_remain_held(self):
        f = self.f
        row, values, pr = copy.deepcopy(f.assignment), copy.deepcopy(f.values), copy.deepcopy(f.pr)
        for mutation in ('unmerged', 'authority', 'tree', 'ancestry', 'checks', 'missing-merge', 'wrong-branch'):
            f.assignment = copy.deepcopy(row); f.values = copy.deepcopy(values); f.pr = copy.deepcopy(pr)
            f.values['/pulls/20'] = f.pr
            if mutation == 'unmerged': f.pr['merged'] = False
            elif mutation == 'authority': f.assignment['merge']['authority']['head'] = 'a' * 40
            elif mutation == 'tree': f.values['/git/commits/' + 'f' * 40]['tree']['sha'] = 'e' * 40
            elif mutation == 'ancestry': f.values['/compare/' + 'f' * 40 + '...' + 'f' * 40]['merge_base_commit']['sha'] = 'a' * 40
            elif mutation == 'checks': f.values['/commits/' + 'f' * 40 + '/check-runs?per_page=100&filter=latest']['check_runs'][0]['conclusion'] = 'failure'
            elif mutation == 'missing-merge': f.assignment.pop('merge')
            else: f.values['/git/ref/heads/factory/issue-10'] = {'ref': 'refs/heads/factory/issue-10', 'object': {'type': 'commit', 'sha': 'e' * 40}}
            with self.subTest(mutation=mutation), self.assertRaises(RepositoryError):
                restart.candidate(f.assignment, f.api)
        f.api.write.assert_not_called(); f.api.issue_state.assert_not_called()

    def test_public_restart_accepts_deleted_integrated_branch_without_duplicate_writes(self):
        # Existing public-effect helper creates only persisted dictionaries and one CLI call.
        saved = self.f.public_effect(merged=True)
        self.assertEqual(saved['merge']['status'], 'merged')
        self.assertEqual(saved['merge']['integrated_evidence']['integrated'], 'f' * 40)
        self.assertFalse(saved['merge']['close_allowed'])
