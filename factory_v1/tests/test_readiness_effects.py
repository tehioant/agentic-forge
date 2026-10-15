"""Focused effect checkpoints; no service/worker/stage preparation.

Operation-specific cases require the core sibling's operation API. They are not
skipped or shimmed: the parent selects these exact IDs after combining source.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from factory_v1 import assignments, attention, effect_resolution, merge, messaging, recovery, requirements, tickets
from factory_v1.errors import IntakeError
from factory_v1.repositories import RepositoryError
from factory_v1.tests import test_readiness as fixtures


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'status': 'active', 'execution_allowed': False,
                     'approval': {'operator_id': 'operator', 'reference': 'fixture'},
                     'ticket_work': {'assignment_id': 'tickets', 'intents': {'one': {'issue': True}}},
                     'requirement_changes': {'revision': {'status': 'publication-pending', 'publication': {}}}}
        self.persist(self.item)

    def persist(self, item):
        self.db.execute('DELETE FROM iterations')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(item)))
        self.db.commit()

    def stored(self):
        return json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])

    def test_ticket_and_revision_receipts_keep_independent_holds_not_next_intents(self):
        fresh = deepcopy(self.item)
        fresh.update(status='paused', main_incident={'incident_id': 'independent'})
        fresh['requirement_changes']['new-hold'] = {'status': 'pending'}
        self.persist(fresh)
        work = self.item['ticket_work']
        work['intents']['one']['identity'] = {'number': 8, 'id': 80, 'node_id': 'I8'}
        work['intents']['speculative'] = {'membership': True}
        tickets.checkpoint(self.db, self.item, work=work, fields=('intents',))
        self.item['requirement_changes']['revision']['publication'].update(commit='a'*40, ref_intent=True)
        requirements.checkpoint(self.db, self.item, receipt=True)
        saved = self.stored()
        self.assertEqual(saved['main_incident'], fresh['main_incident'])
        self.assertEqual(saved['status'], 'paused')
        self.assertIn('new-hold', saved['requirement_changes'])
        self.assertNotIn('speculative', saved['ticket_work']['intents'])
        self.assertEqual(saved['ticket_work']['intents']['one']['identity']['number'], 8)
        journal = saved['requirement_changes']['revision']['publication']
        self.assertEqual(journal['commit'], 'a'*40)
        self.assertNotIn('ref_intent', journal)

    def test_merge_receipt_does_not_drop_new_assignment_hold(self):
        self.db.execute('CREATE TABLE role_assignments(assignment_id TEXT, payload TEXT)')
        stale = {'assignment_id': 'candidate', 'runtime': {'status': 'complete'}}
        fresh = {**stale, 'requirement_disposition': {'change_id': 'new'}}
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(fresh)))
        merge.save(self.db, stale, {'status': 'merged', 'failure': None})
        saved = json.loads(self.db.execute('SELECT payload FROM role_assignments').fetchone()[0])
        self.assertEqual(saved['requirement_disposition'], fresh['requirement_disposition'])
        self.assertEqual(saved['merge']['status'], 'merged')

    def test_recovery_receipt_keeps_new_revision_and_freeze(self):
        incident = dict(incident_id='incident', status='active', monitor_sha256=assignments.digest({}),
                        first_evidence='a'*64, latest_evidence='b'*64, recovery_assignment=None, recovery_issue=None)
        self.db.execute('CREATE TABLE delivery_incidents(project TEXT, iteration TEXT, id TEXT, payload TEXT)')
        self.db.execute('INSERT INTO delivery_incidents VALUES (?,?,?,?)', ('p', 'i', 'incident', json.dumps(incident)))
        self.item.update(main_incident=incident, delivery_monitor={'config': {}},
            main_recoveries={'incident': {'config': {'reference': 'exact'}, 'status': 'registered',
                'removed': {'prior': {'open_intent': True}}}})
        fresh = deepcopy(self.item)
        fresh['requirement_changes']['new-hold'] = {'status': 'pending'}
        fresh['main_recoveries']['incident']['independent_hold'] = 'retain'
        self.persist(fresh)
        receipt = self.item['main_recoveries']['incident']['removed']['prior']
        receipt.update(issue_readback={'number': 8, 'state': 'open'}, status_intent=True)
        recovery.checkpoint(self.db, self.item, receipt=True)
        saved = self.stored()
        self.assertEqual(saved['main_incident'], incident)
        self.assertIn('new-hold', saved['requirement_changes'])
        self.assertEqual(saved['main_recoveries']['incident']['independent_hold'], 'retain')
        saved_receipt = saved['main_recoveries']['incident']['removed']['prior']
        self.assertEqual(saved_receipt['issue_readback']['state'], 'open')
        self.assertNotIn('status_intent', saved_receipt)

    def test_transport_wrong_exact_destination_refuses_before_process(self):
        transport = object.__new__(messaging.HermesTransport)
        transport.destination = {'chat_id': '1', 'thread_id': '2'}
        transport.command = ['/not-executed']
        with patch.object(messaging.subprocess, 'run') as run:
            with self.assertRaises(IntakeError) as caught:
                transport.send({'destination': {'chat_id': '1', 'thread_id': '3'}})
        self.assertEqual(caught.exception.code, 'invalid_origin')
        run.assert_not_called()


class OperationIntegrationTests(fixtures.SupervisedFixture, unittest.TestCase):
    file = fixtures.ReadinessTests.file
    save = fixtures.ReadinessTests.save
    read = fixtures.ReadinessTests.read

    def setUp(self):
        fixtures.EnforcedReadinessTests.setUp(self)
        self.item['ticket_work'] = {'assignment_id': 'tickets', 'inputs': {}, 'request': {},
                                    'tickets': [{'key': 'one'}], 'execution': {'run_id': 'synthesis'}}
        self.save()

    def test_effect_checkpoint_last_reader_drift_no_new_intent(self):
        original = self.readers.execution_support.side_effect
        source = Path(self.config['slots'][0]['skills'][0]['path'])
        raw = source.read_bytes()
        for resolving in (False, True):
            for stage in ('intent', 'write', 'receipt', 'success'):
                with self.subTest(resolving=resolving, stage=stage):
                    self.item.pop('restart_reconciliation', None)
                    if resolving:
                        self.item['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                            'blockers': [{'boundary': 'tracker', 'code': 'publication_uncertain'}]}
                    self.save()
                    resolution = effect_resolution.bound(self.db, self.item, 'ticket-publication', 'tickets')
                    tickets.effect_check(self.db, self.item, resolution=resolution)
                    if stage in {'write', 'receipt', 'success'}:
                        self.item['ticket_work']['intents'] = {'one': {'issue': True}}
                        tickets.effect_checkpoint(self.db, self.item, resolution=resolution)
                    if stage in {'receipt', 'success'}:
                        # An already admitted effect returned while another writer held work.
                        fresh = deepcopy(self.item)
                        fresh['independent_hold'] = 'retained'
                        self.db.execute('UPDATE iterations SET payload=?', (json.dumps(fresh),)); self.db.commit()
                        work = self.item['ticket_work']
                        work['intents']['one']['identity'] = {'number': 8, 'id': 80, 'node_id': 'I8'}
                        tickets.checkpoint(self.db, self.item, work=work, fields=('intents',))
                    before = self.db.execute('SELECT payload FROM iterations').fetchone()[0]
                    def drift(*args):
                        result = original(*args)
                        source.write_bytes(raw + b'changed at independent metadata read-return')
                        return result
                    self.readers.execution_support.side_effect = drift
                    try:
                        with self.assertRaises(RepositoryError) as caught:
                            if stage == 'write':
                                tickets.effect_check(self.db, self.item, resolution=resolution)
                            else:
                                self.item['ticket_work']['next_intent'] = 'must-not-save'
                                tickets.effect_checkpoint(self.db, self.item, resolution=resolution)
                        self.assertEqual(caught.exception.code, 'readiness_inputs_changed')
                        self.assertEqual(before, self.db.execute('SELECT payload FROM iterations').fetchone()[0])
                    finally:
                        source.write_bytes(raw)
                        self.readers.execution_support.side_effect = original
                        self.item['ticket_work'].pop('next_intent', None)
                        self.item['ticket_work'].pop('intents', None)

    def test_effect_permission_and_health_denial_prevent_durable_intent(self):
        for kind in ('permission', 'health'):
            with self.subTest(kind=kind):
                before = self.db.execute('SELECT payload FROM iterations').fetchone()[0]
                if kind == 'permission':
                    self.write_authority['operations'].remove('projects')
                else:
                    self.health['sha'] = 'b'*40
                self.item['ticket_work']['issue_intent'] = True
                try:
                    with self.assertRaises(RepositoryError) as caught:
                        tickets.effect_checkpoint(self.db, self.item)
                    self.assertEqual(caught.exception.code, 'readiness_blocked')
                    self.assertEqual(before, self.db.execute('SELECT payload FROM iterations').fetchone()[0])
                finally:
                    self.item['ticket_work'].pop('issue_intent', None)
                    self.health['sha'] = 'a'*40
                    if kind == 'permission': self.write_authority['operations'].append('projects')

    def test_completion_notification_needs_no_new_worker_or_healthy_delivery(self):
        self.item.update(status='waiting-for-operator', completion={'report_id': 'done'},
                         correlation={'project_id': 'p', 'iteration_id': 'i', 'control_run_id': 'control',
                                      'evidence_id': 'evidence', 'stage': 'completion'})
        self.save()
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        for slot in self.config['slots']: Path(slot['profile']['home']).mkdir()
        self.health['sha'] = 'b'*40
        self.db.execute('CREATE TABLE notifications(project TEXT, iteration TEXT, event_id TEXT, payload TEXT)')
        attention.enqueue(self.db, self.item, {'event_id': 'done', 'kind': 'completion', 'message': 'Done',
            'issue_number': 1, 'run_id': 'completion', 'evidence_ids': ['proof']})
        self.db.commit()
        # The transport is the external seam; the real notification gate is intact.
        with patch.object(attention.HermesTransport, 'send', return_value='accepted') as send:
            result = attention.deliver(self.db, self.item, 'done', self.service['transport_config'])
        self.assertEqual(result['state'], 'accepted')
        send.assert_called_once()
        self.assertEqual(attention.get_record(self.db, self.item, 'done')['attempts'], 1)
