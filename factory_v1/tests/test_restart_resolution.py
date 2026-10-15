"""Exact effect continuation components, with explicit rows and mocked boundaries."""
import copy
import hashlib
import io
import json
import sqlite3
import unittest
from contextlib import ExitStack, closing, redirect_stdout
from dataclasses import replace
from unittest.mock import patch

from factory_v1 import assignments, bugs, controls, effect_resolution, publication, repositories, requirements, restart, tickets
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
    def test_host_registration_keeps_ordinary_hold_matrix(self):
        f = fixture(self, recovery_fixtures.RecoveryRegistrationTests)
        item = f.stored()
        receipt = blocked('delivery', 'main_frozen')
        item['restart_reconciliation'] = receipt
        f.save(item)
        output = io.StringIO()
        argv = ['factory', '--state', str(f.root / 'state.db'), '--operator-id', 'operator', 'register-recovery', '--project', 'p', '--iteration', 'i', '--api-base', f.github.base, '--recovery-config', str(f.config_file)]
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

    def test_ticket_exact_partial_membership_matrix(self):
        baseline = copy.deepcopy(self.f.item)
        issues = copy.deepcopy(self.f.issues)
        for defect in ('membership', 'issue', 'identity', 'inputs', None):
            with self.subTest(defect=defect), ExitStack() as stack:
                self.f.item = copy.deepcopy(baseline)
                self.f.work = self.f.item['ticket_work']
                self.f.issues = copy.deepcopy(issues)
                self.f.members.clear()
                self.f.persist()
                self.f.add_item.reset_mock()
                self.f.set_field.reset_mock()
                if defect == 'membership':
                    self.f.work['intents']['note']['membership'] = True
                if defect == 'issue':
                    self.f.issues.clear()
                if defect == 'identity':
                    self.f.issues[10]['id'] = 999
                if defect == 'inputs':
                    stack.enter_context(patch.object(tickets, 'pinned_inputs', return_value={}))
                self.f.persist()
                if defect == 'inputs':
                    with self.assertRaises(IntakeError) as caught:
                        self.publish()
                    self.assertEqual(caught.exception.code, 'ticket_conflict')
                else:
                    completed = self.publish()
                    if defect:
                        self.assertFalse(completed['ticket_work'].get('publication_complete'))
                        self.assertEqual(completed['ticket_work']['blocker']['code'], 'publication_mismatch' if defect == 'identity' else 'publication_uncertain')
                    else:
                        self.assertTrue(completed['ticket_work']['publication_complete'])
                        self.assertEqual(completed['restart_reconciliation'], baseline['restart_reconciliation'])
                        self.assertEqual(completed['ticket_work']['published']['note'], {'number': 10, 'id': 1010, 'node_id': 'I10'})
                        self.assertEqual(set(self.f.issues), {10})
                        self.assertEqual(self.f.members[10]['fields'], {'SCOPE': 'M1', 'STATUS': 'Ready'})
                        writes = (self.f.add_item.call_count, self.f.set_field.call_count)
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
                if defect:
                    self.f.add_item.assert_not_called()
                self.f.github.request.assert_not_called()
                self.f.add_dependency.assert_not_called()



    def test_ticket_scoped_authority_and_hold_guards_matrix(self):
        original = copy.deepcopy(self.f.item['restart_reconciliation'])
        authority = effect_resolution.bound(self.f.database, self.f.stored(), 'ticket-publication', self.f.work['assignment_id'])
        for forged in (None, True, replace(authority, target='wrong'), replace(authority, project='other'), replace(authority, operator='wrong'), replace(authority, authority='0' * 64)):
            with self.subTest(forged=forged), self.assertRaises(RepositoryError):
                controls.require_active(self.f.database, 'product', 'm1', resolution=forged)
        self.f.work['execution']['run_id'] = 'changed'
        self.f.persist()
        with self.assertRaises(RepositoryError) as caught:
            controls.require_active(self.f.database, 'product', 'm1', resolution=authority)
        self.assertEqual(caught.exception.code, 'stale_admission')
        self.f.work['execution']['run_id'] = 'fixture-run'
        self.f.persist()
        cases = [(blocked('model', 'operation_uncertain'), 'reconciliation_required'), (blocked('delivery', 'main_frozen'), 'reconciliation_required'), (original | {'blockers': original['blockers'] + [{'boundary': 'worker', 'code': 'worker_uncertain'}]}, 'reconciliation_required'), ({'status': 'blocked'}, 'invalid_restart'), (original, 'iteration_paused')]
        for report, code in cases:
            self.f.item['restart_reconciliation'] = report
            self.f.persist()
            if code == 'iteration_paused':
                self.f.pause()
            with self.subTest(code=code, report=report), self.assertRaises(IntakeError) as caught:
                self.publish()
            self.assertEqual(caught.exception.code, code)
            self.f.add_item.assert_not_called()


    def test_ticket_last_membership_pause_matrix(self):
        original = self.f.membership
        paused = False

        def read(*args):
            nonlocal paused
            member = original(*args)
            if member is not None and (not paused):
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

    def test_candidate_exact_pending_pr_matrix(self):
        original = copy.deepcopy(self.f.config)
        baseline = copy.deepcopy(self.item)
        baseline_values = copy.deepcopy(self.f.values)
        cases = [({'candidate': 'f' * 40}, 'authority'), ({'binding': {}}, 'authority'), ({'operator_id': 'wrong'}, 'authority'), ({}, 'mode'), ({}, 'hold'), ({}, 'pause'), ({}, None)]
        for mutation, defect in cases:
            with self.subTest(mutation=mutation, defect=defect):
                self.writes.clear()
                self.f.values = copy.deepcopy(baseline_values)
                _, control_path = controls.paths(self.db, 'p', 'i')
                assert control_path is not None
                control_path.unlink(missing_ok=True)
                self.item = copy.deepcopy(baseline)
                self.persist()
                self.config.write_text(json.dumps(original | mutation))
                self.config.chmod(420 if defect == 'mode' else 384)
                if defect == 'hold':
                    self.item['restart_reconciliation']['blockers'].append({'boundary': 'model', 'code': 'operation_uncertain'})
                    self.persist()
                if defect == 'pause':
                    controls.request(self.db, 'p', 'i', 'operator', 'pause', 'SIMULATED pause')
                if defect:
                    with self.assertRaises(IntakeError) as caught:
                        self.publish()
                    if defect == 'pause':
                        self.assertEqual(caught.exception.code, 'iteration_paused')
                    self.assertEqual(self.writes, [])
                else:
                    result = self.publish()
                    self.assertEqual(result['status'], 'published')
                    self.assertEqual(result['candidate'], 'b' * 40)
                    self.assertEqual(result['pr_number'], 20)
                    self.assertFalse(result['close_allowed'])
                    self.assertFalse(result['merge_allowed'])
                    self.assertEqual([p for p, _ in self.writes], ['/pulls'])
                    self.publish()
                    self.assertEqual([p for p, _ in self.writes], ['/pulls'])
                    with self.assertRaises(RepositoryError):
                        controls.require_active(self.db, 'p', 'i')



    def test_candidate_last_pr_pause_matrix(self):
        paused = False

        def read(github, path, payload=None, method=None):
            nonlocal paused
            result = self.transport(github, path, payload, method)
            if path.endswith(publication.pr_query(self.f.config)) and (not paused):
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
        real_context = bugs.context
        self.f = fixture(self, bug_fixtures.BugAdmissionTests)
        f = self.f
        self.enterContext(patch.object(bugs, 'context', real_context))
        self.enterContext(patch.object(requirements, 'revalidate_inputs', return_value=f.inputs))
        self.enterContext(patch.object(tickets, 'verified_board', return_value=f.board))
        f.item['ticket_work']['inputs'] = f.inputs
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

    def test_bug_exact_missing_membership_and_holds_matrix(self):
        baseline = copy.deepcopy(self.f.item)
        for defect in ('membership', 'hold', 'retired', 'archived', None, 'pause'):
            with self.subTest(defect=defect):
                self.f.item = copy.deepcopy(baseline)
                self.f.work = self.f.item['bug_work']['defect']
                self.member = None
                self.f.add_item.reset_mock()
                self.f.set_field.reset_mock()
                if defect == 'membership':
                    self.f.work['membership_intent'] = True
                if defect == 'hold':
                    self.f.item['restart_reconciliation']['blockers'].append({'boundary': 'delivery', 'code': 'main_frozen'})
                if defect == 'retired':
                    self.f.work['requirement_disposition'] = {'change_id': 'retired'}
                if defect == 'archived':
                    self.f.item['bug_work_history'] = [{'assignment_id': 'old', 'issue_intent': {'title': 'uncertain'}}]
                self.f.save()
                if defect == 'pause':
                    self.f.pause(None)
                if defect in ('hold', 'retired', 'pause'):
                    with self.assertRaises(IntakeError) as caught:
                        self.publish()
                    if defect != 'hold':
                        self.assertEqual(caught.exception.code, 'stale_requirements' if defect == 'retired' else 'iteration_paused')
                    self.f.add_item.assert_not_called()
                else:
                    result = self.publish()
                    if defect == 'membership':
                        self.assertEqual(result['bug_work']['defect']['blocker']['code'], 'publication_uncertain')
                        self.f.add_item.assert_not_called()
                    else:
                        self.assertTrue(result['bug_work']['defect']['publication_complete'])
                        self.assertEqual(result['bug_work']['defect']['identity'], {'number': 13, 'id': 1013, 'node_id': 'I13'})
                        self.assertEqual(self.member, {'id': 'M13', 'status': 'todo'})
                        if defect == 'archived':
                            self.assertEqual(result['bug_work_history'], self.f.item['bug_work_history'])
                        count = (self.f.add_item.call_count, self.f.set_field.call_count)
                        self.publish()
                        self.assertEqual((self.f.add_item.call_count, self.f.set_field.call_count), count)
                        self.assertEqual(result['restart_reconciliation']['status'], 'blocked')
                        with self.assertRaises(RepositoryError):
                            controls.require_active(self.f.database, 'p1', 'm1')
                self.f.github.request.assert_not_called()
                self.f.add_dependency.assert_not_called()




class IntegratedBranchResolutionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture(self, effects.CandidateRestartTests)
        self.f.merged()  # Exact PR/commit/check dictionaries, no integration precursor.
        self.f.values['/git/ref/heads/factory/issue-10'] = None

    def test_integrated_deleted_branch_exact_readback_matrix(self):
        f = self.f
        row, values, pr = (copy.deepcopy(f.assignment), copy.deepcopy(f.values), copy.deepcopy(f.pr))
        for mutation in (None, 'unmerged', 'authority', 'tree', 'ancestry', 'checks', 'missing-merge', 'wrong-branch'):
            f.assignment = copy.deepcopy(row)
            f.values = copy.deepcopy(values)
            f.pr = copy.deepcopy(pr)
            f.values['/pulls/20'] = f.pr
            if mutation == 'unmerged':
                f.pr['merged'] = False
            elif mutation == 'authority':
                f.assignment['merge']['authority']['head'] = 'a' * 40
            elif mutation == 'tree':
                f.values['/git/commits/' + 'f' * 40]['tree']['sha'] = 'e' * 40
            elif mutation == 'ancestry':
                f.values['/compare/' + 'f' * 40 + '...' + 'f' * 40]['merge_base_commit']['sha'] = 'a' * 40
            elif mutation == 'checks':
                f.values['/commits/' + 'f' * 40 + '/check-runs?per_page=100&filter=latest']['check_runs'][0]['conclusion'] = 'failure'
            elif mutation == 'missing-merge':
                f.assignment.pop('merge')
            elif mutation == 'wrong-branch':
                f.values['/git/ref/heads/factory/issue-10'] = {'ref': 'refs/heads/factory/issue-10', 'object': {'type': 'commit', 'sha': 'e' * 40}}
            with self.subTest(mutation=mutation):
                if mutation:
                    with self.assertRaises(RepositoryError):
                        restart.candidate(f.assignment, f.api)
                else:
                    result = restart.candidate(f.assignment, f.api)
                    self.assertEqual(result, {'candidate': 'b' * 40, 'tree': 'c' * 40, 'pr_number': 20})
                    self.assertEqual(f.assignment['merge']['integrated_evidence']['integrated'], 'f' * 40)
                    self.assertFalse(f.assignment['merge']['close_allowed'])
                f.api.write.assert_not_called()
                f.api.issue_state.assert_not_called()
        f.assignment, f.values, f.pr = (copy.deepcopy(row), copy.deepcopy(values), copy.deepcopy(pr))
        f.config = f.assignment['publication']['approval']
        f.values['/pulls/20'] = f.pr
        saved = f.public_effect(merged=True)
        self.assertEqual(saved['merge']['status'], 'merged')
        self.assertEqual(saved['merge']['integrated_evidence']['integrated'], 'f' * 40)
        self.assertFalse(saved['merge']['close_allowed'])
