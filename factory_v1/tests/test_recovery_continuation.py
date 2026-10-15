"""Minimum recovery continuation components; no workers or external services."""
import copy
import json
from unittest.mock import patch

from factory_v1 import assignments, attention, controls, diagnosis, recovery, requirements, requirement_publication, tickets
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.spending import SpendingError
from factory_v1.tests.test_recovery_components import RecoveryComponentFixture


class RecoveryContinuationTests(RecoveryComponentFixture):
    def failed_root(self):
        prior = self.seed_assignment()
        prior['runtime'] = {'status': 'stopped', 'container_removed': True, 'error': 'controller_lost',
                            'recoverable': True, 'run_id': 'lost-run', 'artifacts': str(self.root / 'failed'),
                            'controller_pid': -1, 'controller_start': 'SIMULATED absent controller'}
        with self.db:
            self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                            (json.dumps(prior), prior['assignment_id']))
            item = self.stored()
            item['budget'] = {'attempts': 3, 'deadline': 12345}
            save_iteration(self.db, item)
        return prior

    def continuation_file(self, prior, kind='infrastructure', change_id=None):
        config = copy.deepcopy(self.authorization)
        config['reference'] = 'SIMULATED explicit continuation authorization'
        config['continuation'] = {'kind': kind, 'assignment_id': prior['assignment_id'],
                                  'runtime_sha256': assignments.digest(prior['runtime']), 'change_id': change_id}
        path = self.root / 'continue.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        return path, config

    def continue_recovery(self, path):
        with patch.object(assignments, 'admission_preview', return_value={'scope': 'simulated'}):
            return recovery.continue_recovery(self.db, 'p', 'i', '42', path, self.github)

    def seed_continued(self, before, prior, authorization):
        item = copy.deepcopy(before)
        record = recovery.active(item)
        assert record is not None
        record['continuations'] = [{'authorization': authorization, 'prior_config': record['config'],
            'prior_assignment': prior['assignment_id'], 'prior_handoff_digest': prior['handoff_digest'],
            'prior_runtime': prior['runtime'], 'prior_revision': prior['revision']}]
        config = {k: authorization[k] for k in recovery.CONFIG_FIELDS}
        record.update(config=config, assignment=None, status='registered')
        record['parked'] = list(dict.fromkeys([*record['parked'], prior['assignment_id']]))
        item['main_incident'].update(recovery_assignment=assignments.digest(config), recovery_issue=20)
        historical = prior | {'recovery_disposition': config['incident_id']}
        with self.db:
            self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                (json.dumps(historical), prior['assignment_id']))
            save_iteration(self.db, item)
            self.db.execute('UPDATE delivery_incidents SET payload=? WHERE id=?',
                (json.dumps(item['main_incident']), config['incident_id']))

    def test_reconciled_infrastructure_continuation_preserves_failed_execution_and_budget(self):
        prior = self.failed_root()
        before = self.stored()
        path, config = self.continuation_file(prior)
        with self.assertRaises(RepositoryError):
            self.prepare('fresh')  # An implicit retry is still forbidden.
        record = self.continue_recovery(path)
        self.assertEqual(record, self.continue_recovery(path))
        durable = self.stored()
        history = recovery.active(durable)['continuations'][0]
        self.assertEqual(history['prior_runtime'], prior['runtime'])
        self.assertEqual(history['prior_config'], recovery.active(before)['config'])
        self.assertEqual(history['authorization'], config)
        old = next(a for a in assignments.rows(self.db) if a['assignment_id'] == prior['assignment_id'])
        self.assertEqual(old['runtime'], prior['runtime'])
        self.assertEqual(old['recovery_disposition'], self.authorization['incident_id'])
        self.assertEqual(durable['budget'], before['budget'])
        self.assertEqual(durable['main_incident']['incident_id'], before['main_incident']['incident_id'])
        self.seed_continued(before, prior, config)
        fresh = self.prepare('fresh')
        self.assertNotIn('runtime', fresh)
        self.assertNotEqual(fresh['assignment_id'], prior['assignment_id'])
        history = recovery.active(self.stored())['continuations'][0]
        self.assertEqual(history['prior_runtime'], prior['runtime'])
        self.assertEqual(history['prior_config'], before['main_recoveries'][self.authorization['incident_id']]['config'])
        self.assertEqual(history['authorization'], config)
        old = next(a for a in assignments.rows(self.db) if a['assignment_id'] == prior['assignment_id'])
        self.assertEqual(old['runtime'], prior['runtime'])
        with self.assertRaises(RepositoryError):
            assignments.require_result_active(old)
        with self.assertRaises(RepositoryError):
            self.prepare()  # Old claim cannot relaunch or reuse evidence.
        self.assertEqual(self.prepare('fresh')['assignment_id'], fresh['assignment_id'])
        self.assertEqual(self.stored()['budget'], before['budget'])
        self.assertEqual(self.stored()['main_incident']['incident_id'], before['main_incident']['incident_id'])
        self.assertIsNotNone(self.sample(True)['incident'])
        self.github.request.assert_not_called()

    def test_infrastructure_refuses_unreconciled_stuck_changed_scope_and_spending_holds(self):
        prior = self.failed_root()
        for delta in ({'container_removed': False}, {'status': 'unconfirmed'}, {'recoverable': False}):
            changed = prior | {'runtime': prior['runtime'] | delta}
            with self.db:
                self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                                (json.dumps(changed), prior['assignment_id']))
            path, _ = self.continuation_file(changed)
            before = self.stored()
            with self.assertRaises(RepositoryError):
                self.continue_recovery(path)
            self.assertEqual(self.stored(), before)
        with self.db:
            self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(prior), prior['assignment_id']))
        path, config = self.continuation_file(prior)
        for reason in ('outcome_uncertain', 'quota_exhausted', 'spending_blocked'):
            before = self.stored()
            with patch.object(assignments, 'admission_preview', side_effect=SpendingError(reason, 'SIMULATED hold')):
                with self.assertRaises(RepositoryError) as caught:
                    recovery.continue_recovery(self.db, 'p', 'i', '42', path, self.github)
            self.assertEqual(caught.exception.code, reason)
            self.assertEqual(self.stored(), before)
        self.issue['body'] = 'unapproved edit'
        with self.assertRaises(RepositoryError):
            self.continue_recovery(path)
        self.issue['body'] = 'repair requirements'
        for hold in ({'stuck_disposition': 'held'}, {'requirement_disposition': {'status': 'obsolete'}},
                     {'submitted_result': {'status': 'stuck'}}):
            with self.db:
                self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                                (json.dumps(prior | hold), prior['assignment_id']))
            with self.assertRaises(RepositoryError):
                self.continue_recovery(path)

    def revised_state(self, prior, action='revise'):
        item = self.stored()
        response = {'event_id': 'revise-event', 'decision_id': 'revise-event', 'operator_id': '42',
                    'origin': item['origin'], 'reference': 'SIMULATED approved revise',
                    'response': json.dumps({'action': action, 'direction': 'bounded revised repair'})}
        self.db.execute('CREATE TABLE IF NOT EXISTS notifications (project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project,iteration,event_id))')
        notification = attention.enqueue(self.db, item, {'event_id': 'revise-event', 'kind': 'decision',
            'decision_id': 'revise-event', 'message': 'SIMULATED choose direction', 'issue_number': 20,
            'run_id': 'debug-run', 'evidence_ids': [prior['assignment_id']]})
        attention.respond(self.db, item, response, '42')
        direction = {'operator_id': '42', 'origin': item['origin'], 'reference': response['reference'],
                     'content': 'bounded revised repair'}
        pins = {'stuck_assignment': prior['assignment_id']}
        axis = {'assignment_id': 'd' * 64, 'claim_id': 'debug', 'handoff': {'stage': 'diagnosis', 'workspace': str(self.root / 'debug'),
                'profile': {'name': 'debug', 'home': str(self.root / 'debug-home')}, 'diagnosis': {'pins': pins}},
                'ticket_scope': prior['ticket_scope'], 'runtime': {'status': 'complete', 'container_removed': True,
                    'artifacts': str(self.root / 'debug-artifacts'), 'controller_pid': -1, 'controller_start': 'SIMULATED absent'},
                'result_disposition': {'isolated_execution': True}, 'submitted_result': {'status': 'done'},
                'diagnosis_result': {'report_sha256': 'e' * 64}}
        binding = {'SIMULATED': 'independently verified debug execution'}
        linked = {'assignment_id': prior['assignment_id'], 'event_id': 'revise-event', 'applied_response': response,
                  'affected': [20], 'authorization': {'operator_id': '42', 'diagnosis': binding}}
        stuck = {**{k: linked[k] for k in ('event_id', 'applied_response', 'affected', 'authorization')},
                 'pins': pins, 'issues': {'20': prior['handoff']['issue']}, 'sequence': 1,
                 'status': 'released', 'direction': {'action': action, 'direction': direction['content']},
                 'requirement_change': 'repair-v2', 'retired_assignments': [prior['assignment_id']],
                 'diagnosis_assignment': axis['assignment_id'], 'blocks_verified': True}
        self.issue.update(body='approved revised repair requirements', title='Repair', labels=[])
        updated = requirements.issue_pin(self.issue)
        new_revision = {'spec': {'commit': 'b' * 40}, 'ticket_assignment': 'repair-v2', 'ticket_inputs': 'e' * 64}
        change = {'status': 'resumed', 'kind': 'material', 'affected': [20], 'requirements': ['US-1'], 'stuck_revision': linked,
                  'retired_assignments': [prior['assignment_id']], 'issues': {'20': prior['handoff']['issue']},
                  'old_revision': {'20': prior['revision']}, 'new_revision': new_revision,
                  'resolution': direction, 'resume_direction': direction, 'updated_issues': {'20': updated},
                  'new_inputs': {'handoff': new_revision['spec'], 'documents': {}},
                  'executions': {'to-spec': {}, 'to-tickets': {}}, 'updates': {'20': {'title': 'Repair'}},
                  'old_issues': {'20': {'labels': []}}, 'dependency_pins': {'20': []}, 'memberships': {'20': 'MEM20'}}
        item.update(stuck_work={prior['assignment_id']: stuck}, requirement_changes={'repair-v2': change},
                    requirement_authority={'20': new_revision})
        item['ticket_work']['board']['scope'] = {'id': 'S', 'options': {'i': 'I'}}
        prior['requirement_disposition'] = {'change_id': 'repair-v2', 'status': 'obsolete'}
        with self.db:
            self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(prior), prior['assignment_id']))
            self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (axis['assignment_id'], json.dumps(axis)))
            save_iteration(self.db, item)
        return updated, axis, binding

    def revision_seams(self, axis, binding):
        from contextlib import ExitStack
        stack = ExitStack()
        # Native execution/synthesis and remote read seams; the real publication validator still runs.
        stack.enter_context(patch.object(diagnosis, 'execution_binding', return_value=binding))
        stack.enter_context(patch.object(diagnosis, 'validate_result', return_value=axis['diagnosis_result']))
        stack.enter_context(patch.object(requirement_publication, 'verify_synthesis'))
        stack.enter_context(patch.object(tickets, 'pinned_inputs', side_effect=lambda db, item, gh: item['requirement_changes']['repair-v2']['new_inputs']))
        stack.enter_context(patch.object(tickets, 'verified_board', return_value=self.stored()['ticket_work']['board']))
        stack.enter_context(patch.object(tickets, 'external_contract', return_value=True))
        stack.enter_context(patch.object(tickets.gh, 'dependencies', return_value=[]))
        stack.enter_context(patch.object(tickets.gh, 'membership', return_value={'id': 'MEM20', 'scope': 'I', 'status': 'T'}))
        return stack

    def test_revision_supersession_requires_correlated_direction_and_current_versioned_publication(self):
        prior = self.failed_root()
        updated, axis, binding = self.revised_state(prior)
        path, config = self.continuation_file(prior, 'revision', 'repair-v2')
        config['issue'] = updated
        path.write_text(json.dumps(config))
        before = self.stored()
        with self.revision_seams(axis, binding):
            for mutate in (lambda i: i['requirement_changes']['repair-v2'].update(status='published'),
                           lambda i: i['stuck_work'][prior['assignment_id']]['applied_response'].update(reference='unrelated'),
                           lambda i: i['requirement_authority']['20'].update(ticket_inputs='0' * 64),
                           lambda i: i['requirement_changes']['repair-v2'].update(executions={})):
                original = self.stored()
                changed = copy.deepcopy(original)
                mutate(changed)
                with self.db:
                    save_iteration(self.db, changed)
                with self.assertRaises(RepositoryError):
                    self.continue_recovery(path)
                self.assertEqual(self.stored(), changed)
                with self.db:
                    save_iteration(self.db, original)
            record = self.continue_recovery(path)
        self.assertEqual(record['config']['issue'], updated)
        self.assertEqual(record['continuations'][0]['prior_config']['issue'], self.pin)
        self.seed_continued(before, prior, config)
        self.pin = updated
        with self.preparation_seams():
            fresh = recovery.prepare(self.db, 'p', 'i', '42', self.request('revised') | {'spec_commit': 'b' * 40}, self.github)
        self.assertEqual(fresh['revision'], requirements.authority(self.stored(), 20))
        self.assertIn('requirement_change', fresh['handoff'])
        self.assertNotIn('runtime', fresh)
        self.assertEqual(self.stored()['budget'], {'attempts': 3, 'deadline': 12345})
        self.assertIsNotNone(self.sample(True)['incident'])
        self.github.request.assert_not_called()


    def test_complete_applied_retry_direction_still_admits_fresh_repair(self):
        prior = self.failed_root()
        _, axis, binding = self.revised_state(prior, action='retry')
        item = self.stored()
        item.pop('requirement_changes')
        item.pop('requirement_authority')
        prior.pop('requirement_disposition')
        prior['stuck_disposition'] = 'superseded'
        self.issue['body'] = 'repair requirements'
        with self.db:
            save_iteration(self.db, item)
            self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(prior), prior['assignment_id']))
        with patch.object(diagnosis, 'execution_binding', return_value=binding), \
             patch.object(diagnosis, 'validate_result', return_value=axis['diagnosis_result']):
            fresh = self.prepare('retry')
        self.assertEqual(fresh['handoff']['operator_direction']['reference'], 'SIMULATED approved revise')
        self.assertNotIn('runtime', fresh)
        self.assertIsNotNone(self.sample(True)['incident'])

    def test_direction_guard_requires_actual_response_and_independent_execution_not_released_flags(self):
        from factory_v1.errors import IntakeError
        prior = self.failed_root()
        _, axis, binding = self.revised_state(prior)
        item = self.stored()
        stuck = item['stuck_work'][prior['assignment_id']]
        with patch.object(diagnosis, 'execution_binding', return_value=binding), \
             patch.object(diagnosis, 'validate_result', return_value=axis['diagnosis_result']):
            recovery.validate_direction(self.db, item, stuck, '42', 'revise')
            for delta in ({'applied_response': {'reference': 'partial'}},
                          {'applied_response': stuck['applied_response'] | {'reference': 'different actual response'}},
                          {'authorization': {'diagnosis': {'unbound': True}}}):
                with self.subTest(delta=delta), self.assertRaises((RepositoryError, IntakeError)):
                    recovery.validate_direction(self.db, item, stuck | delta, '42', 'revise')
            for delta in ({'runtime': axis['runtime'] | {'container_removed': False}},
                          {'result_disposition': {'isolated_execution': False}}):
                changed = axis | delta
                with self.db:
                    self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(changed), axis['assignment_id']))
                with self.subTest(delta=delta), self.assertRaises(RepositoryError):
                    recovery.validate_direction(self.db, item, stuck, '42', 'revise')

    def test_controlled_revision_frontier_pin_changes_only_after_correlated_publication(self):
        prior = self.failed_root()
        updated, axis, binding = self.revised_state(prior)
        item = self.stored()
        record = recovery.active(item)
        assert record is not None
        change = item['requirement_changes']['repair-v2']
        for status in ('held', 'resolved', 'synthesized'):
            change['status'] = status
            self.assertEqual(recovery.observed_issue_pin(item, record), self.pin)
        for status in ('publication-pending', 'published', 'resume-pending', 'resumed'):
            change['status'] = status
            self.assertEqual(recovery.observed_issue_pin(item, record), updated)
            self.assertEqual(record['config']['issue'], self.pin)
        change['stuck_revision']['applied_response'] = {'reference': 'partial fabricated flag'}
        with self.assertRaises(RepositoryError):
            recovery.observed_issue_pin(item, record)

    def test_public_frontier_observes_revised_issue_but_keeps_pending_revision_and_discovery_holds(self):
        from contextlib import ExitStack
        prior = self.failed_root()
        updated, _, _ = self.revised_state(prior)
        item = self.stored()
        work = item['ticket_work']
        work['inputs']['handoff']['issue'] = {'number': 3}
        work['request'].update(skill={})
        work['request']['tracker']['triage_label'] = 'ready'
        self.issue['labels'] = [{'name': 'ready'}]
        members = [{'id': 'MEM20', 'content': {'repository': {'nameWithOwner': 'example/product'}, 'number': 20, 'id': 'I20'},
                    'values': {'S': 'I', 'F': 'T'}}]
        with ExitStack() as stack:
            stack.enter_context(patch.object(tickets, 'skill'))
            stack.enter_context(patch.object(tickets, 'pinned_inputs', return_value=work['inputs']))
            stack.enter_context(patch.object(tickets, 'verified_board', return_value=work['board']))
            stack.enter_context(patch.object(requirements, 'revalidate_inputs', return_value={}))
            stack.enter_context(patch.object(tickets, 'external_contract', return_value='ready'))
            stack.enter_context(patch.object(tickets.gh, 'project_items', return_value=members))
            stack.enter_context(patch.object(tickets.gh, 'single_select_values', side_effect=lambda m: m['values']))
            stack.enter_context(patch.object(tickets.gh, 'dependencies', return_value=[]))
            for status, eligible in [('published', []), ('resume-pending', []), ('resumed', [20])]:
                item['requirement_changes']['repair-v2']['status'] = status
                with self.db:
                    save_iteration(self.db, item)
                observed = tickets.frontier(self.db, self.stored(), self.github)
                self.assertEqual(observed['ticket_work']['frontier']['eligible'], eligible)
                self.assertEqual(recovery.active(observed)['config']['issue'], self.pin)
            with patch('factory_v1.bugs.held_numbers', return_value={20}):
                self.assertEqual(tickets.frontier(self.db, self.stored(), self.github)['ticket_work']['frontier']['eligible'], [])
            self.issue['body'] += ' unapproved drift'
            with self.assertRaises(RepositoryError):
                tickets.frontier(self.db, self.stored(), self.github)
        self.github.request.assert_not_called()

    def test_continue_recovery_public_cli_dispatch_and_exact_durable_replay(self):
        import contextlib
        import io
        import sys
        from factory_v1 import __main__ as cli
        prior = self.failed_root()
        inspected = recovery.inspect(self.db, 'p', 'i', '42')
        self.assertEqual(inspected['attempt']['runtime'], prior['runtime'])
        path, config = self.continuation_file(prior)
        self.assertEqual(config['continuation']['runtime_sha256'], inspected['attempt']['runtime_sha256'])
        argv = ['factory_v1', '--state', str(self.root / 'state.sqlite'), '--operator-id', '42',
                'continue-recovery', '--project', 'p', '--iteration', 'i', '--recovery-config', str(path),
                '--api-base', self.github.base]
        for _ in range(2):
            with patch.object(sys, 'argv', argv), patch('factory_v1.repositories.GitHub', return_value=self.github), \
                 patch.object(assignments, 'admission_preview', return_value={'scope': 'simulated'}), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                cli.main()
            record = json.loads(out.getvalue())
            self.assertEqual(record, recovery.active(self.stored()))
            self.assertEqual(len(record['continuations']), 1)
            self.assertEqual(record['continuations'][0]['authorization'], config)
        self.github.request.assert_not_called()


    def test_continuation_pause_at_last_issue_read_admits_no_supersession(self):
        prior = self.failed_root()
        path, _ = self.continuation_file(prior)
        before = self.stored()
        def read(*args):
            controls.request(self.db, 'p', 'i', '42', 'pause', 'SIMULATED final interruption')
            return copy.deepcopy(self.issue)
        self.reader.side_effect = read
        with self.assertRaises(RepositoryError) as caught:
            self.continue_recovery(path)
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(self.stored(), before)
        self.assertNotIn('recovery_disposition', assignments.rows(self.db)[0])
