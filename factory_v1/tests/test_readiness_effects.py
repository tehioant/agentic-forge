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

from factory_v1 import planning, assignments, attention, effect_resolution, merge, messaging, recovery, requirements, tickets
from factory_v1 import readiness_gate, service_dispatch
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

    def test_receipt_writers_merge_only_returned_evidence_into_fresh_held_records(self):
        self.db.execute('CREATE TABLE role_assignments(assignment_id TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE delivery_incidents(project TEXT, iteration TEXT, id TEXT, payload TEXT)')
        baseline = deepcopy(self.item)
        for writer in ('ticket-revision', 'merge', 'recovery'):
            with self.subTest(writer=writer):
                self.item = deepcopy(baseline)
                fresh = deepcopy(self.item)
                fresh.update(status='paused', main_incident={'incident_id': 'independent'})
                fresh['requirement_changes']['new-hold'] = {'status': 'pending'}
                incident = dict(incident_id='incident', status='active', monitor_sha256=assignments.digest({}),
                    first_evidence='a'*64, latest_evidence='b'*64, recovery_assignment=None, recovery_issue=None)
                if writer == 'recovery':
                    self.db.execute('INSERT INTO delivery_incidents VALUES (?,?,?,?)', ('p', 'i', 'incident', json.dumps(incident)))
                    self.item.update(main_incident=incident, delivery_monitor={'config': {}},
                        main_recoveries={'incident': {'config': {'reference': 'exact'}, 'status': 'registered',
                            'removed': {'prior': {'open_intent': True}}}})
                    fresh.update(main_incident=incident, delivery_monitor={'config': {}},
                        main_recoveries=deepcopy(self.item['main_recoveries']))
                    fresh['main_recoveries']['incident']['independent_hold'] = 'retain'
                self.persist(fresh)
                if writer == 'ticket-revision':
                    work = self.item['ticket_work']
                    work['intents']['one']['identity'] = {'number': 8, 'id': 80, 'node_id': 'I8'}
                    work['intents']['speculative'] = {'membership': True}
                    tickets.checkpoint(self.db, self.item, work=work, fields=('intents',))
                    self.item['requirement_changes']['revision']['publication'].update(commit='a'*40, ref_intent=True)
                    requirements.checkpoint(self.db, self.item, receipt=True)
                elif writer == 'merge':
                    stale = {'assignment_id': 'candidate', 'runtime': {'status': 'complete'}}
                    assignment = {**stale, 'requirement_disposition': {'change_id': 'new'}}
                    self.db.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(assignment)))
                    merge.save(self.db, stale, {'status': 'merged', 'failure': None})
                else:
                    receipt = self.item['main_recoveries']['incident']['removed']['prior']
                    receipt.update(issue_readback={'number': 8, 'state': 'open'}, status_intent=True)
                    recovery.checkpoint(self.db, self.item, receipt=True)
                saved = self.stored()
                self.assertEqual(saved['main_incident'], fresh['main_incident'])
                self.assertEqual(saved['status'], 'paused')
                self.assertIn('new-hold', saved['requirement_changes'])
                if writer == 'ticket-revision':
                    self.assertNotIn('speculative', saved['ticket_work']['intents'])
                    self.assertEqual(saved['ticket_work']['intents']['one']['identity']['number'], 8)
                    journal = saved['requirement_changes']['revision']['publication']
                    self.assertEqual(journal['commit'], 'a'*40)
                    self.assertNotIn('ref_intent', journal)
                elif writer == 'merge':
                    saved = json.loads(self.db.execute('SELECT payload FROM role_assignments').fetchone()[0])
                    self.assertEqual(saved['requirement_disposition'], assignment['requirement_disposition'])
                    self.assertEqual(saved['merge']['status'], 'merged')
                else:
                    self.assertEqual(saved['main_incident'], incident)
                    self.assertEqual(saved['main_recoveries']['incident']['independent_hold'], 'retain')
                    saved_receipt = saved['main_recoveries']['incident']['removed']['prior']
                    self.assertEqual(saved_receipt['issue_readback']['state'], 'open')
                    self.assertNotIn('status_intent', saved_receipt)


class NotificationAdmissionTests(fixtures.SupervisedFixture, unittest.TestCase):
    """Reconstructed SPEC26-01 regression; original retained probe has no script."""
    file = fixtures.ReadinessTests.file
    save = fixtures.ReadinessTests.save
    read = fixtures.ReadinessTests.read

    def setUp(self):
        fixtures.EnforcedReadinessTests.setUp(self)
        self.db.execute('CREATE TABLE notifications(project TEXT, iteration TEXT, event_id TEXT, payload TEXT, '
                        'PRIMARY KEY(project, iteration, event_id))')
        self.pending = dict(project_id='p', iteration_id='i', destination=deepcopy(self.origin),
            event=dict(event_id='incident', kind='incident', message='Engineering capability unavailable',
                       issue_number=1, run_id='observer', evidence_ids=['proof']),
            state='pending', attempts=0, receipt=None, decision_response=None,
            correlation=dict(project_id='p', iteration_id='i', control_run_id='control', evidence_id='evidence',
                stage='incident', work_item=dict(repository='owner/product', issue_number=1),
                worker_run_id='observer', evidence_ids=['proof']))
        self.reset_pending()

    def reset_pending(self):
        attention.save(self.db, deepcopy(self.pending))
        self.db.commit()

    def deliver(self, public=False):
        if public:
            return attention.lifecycle(self.db, 'p', 'i', 'operator', 'deliver',
                event_id='incident', config_path=self.service['transport_config'])
        return attention.deliver(self.db, self.item, 'incident', self.service['transport_config'])

    def test_notifications_report_even_when_engineering_or_restart_is_held(self):
        original = self.github.request.side_effect
        restore = self.snapshot()
        cases = [(fault, route) for fault in ('projects', 'repository', 'board', 'stages')
                 for route in ('direct', 'lifecycle', 'nested')]
        cases += [('unheld', 'lifecycle'), ('restart', 'lifecycle'), ('completion', 'direct')]
        for fault, route in cases:
            with self.subTest(fault=fault, route=route):
                restore()
                try:
                    self.reset_pending()
                    event_id = 'incident'
                    if fault == 'restart':
                        self.item['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                            'blockers': [{'boundary': 'tracker', 'code': 'publication_uncertain'}]}
                        self.save()
                    if fault == 'completion':
                        self.item.update(status='waiting-for-operator', completion={'report_id': 'done'},
                            correlation={'project_id': 'p', 'iteration_id': 'i', 'control_run_id': 'control',
                                'evidence_id': 'evidence', 'stage': 'completion'})
                        self.save()
                        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
                        for slot in self.config['slots']: Path(slot['profile']['home']).mkdir()
                        self.health['sha'] = 'b'*40
                        event_id = 'done'
                        attention.enqueue(self.db, self.item, {'event_id': event_id, 'kind': 'completion', 'message': 'Done',
                            'issue_number': 1, 'run_id': 'completion', 'evidence_ids': ['proof']})
                        self.db.commit()
                    def metadata(path, *args, **kwargs):
                        if fault == 'repository' or (fault == 'board' and path == '/graphql'):
                            raise ValueError('independent metadata inaccessible')
                        return original(path, *args, **kwargs)
                    self.github.request.side_effect = metadata
                    if fault == 'projects': self.write_authority['operations'].remove('projects')
                    with patch.object(service_dispatch, 'installed', side_effect=ValueError('stage unavailable')) if fault == 'stages' else patch.object(service_dispatch, 'installed', wraps=service_dispatch.installed):
                        if fault in ('projects', 'repository', 'board', 'stages'):
                            for operation in ('work', 'effect'):
                                with self.assertRaises(RepositoryError) as caught:
                                    readiness_gate.check(self.db, 'p', 'i', operation=operation)
                                self.assertEqual(caught.exception.code, 'readiness_blocked')
                        with patch.object(attention.HermesTransport, 'send', return_value='accepted') as send:
                            if route == 'nested':
                                with readiness_gate.admission(self.db, 'p', 'i', operation='notification'):
                                    result = self.deliver()
                            elif route == 'lifecycle': result = self.deliver(public=True)
                            else: result = attention.deliver(self.db, self.item, event_id, self.service['transport_config'])
                        self.assertEqual((result['state'], result['attempts']), ('accepted', 1))
                        send.assert_called_once()
                        self.assertEqual(send.call_args.args[0]['destination'], self.origin)
                        self.assertEqual(attention.get_record(self.db, self.item, event_id)['attempts'], 1)
                finally: restore()

    def test_notification_origin_and_policy_drift_refuse_before_send_with_truthful_attempts(self):
        original = self.github.request.side_effect
        restore = self.snapshot()
        paths = [Path(self.service_path), Path(self.service['readiness_config']),
                 Path(self.service['transport_config']), self.root / 'host-adapter']
        cases = [(phase, fault) for phase in (1, 2)
                 for fault in ('origin', 'transport', 'private-config', 'executable', 'policy')]
        cases.append((1, 'record-destination'))
        for phase, fault in cases:
            with self.subTest(phase=phase, fault=fault):
                restore()
                injected = False
                self.reset_pending()
                if fault == 'record-destination':
                    record = deepcopy(self.pending); record['destination']['thread_id'] = '999'
                    attention.save(self.db, record); self.db.commit()
                def metadata(path, *args, **kwargs):
                    nonlocal injected
                    result = original(path, *args, **kwargs)
                    state = json.loads(self.db.execute('SELECT payload FROM notifications').fetchone()[0])['state']
                    if path == '/repos/owner/product' and fault != 'record-destination':
                        if not injected and state == ('pending' if phase == 1 else 'uncertain'):
                            injected = True
                            if fault == 'origin': self.item['origin'] = {**self.origin, 'thread_id': '999'}; self.save()
                            elif fault == 'transport':
                                self.file('transport.json', {'command': [str(self.root / 'host-adapter')], 'approved_destinations': []})
                            elif fault == 'private-config': paths[0].chmod(0o666)
                            elif fault == 'executable': paths[3].chmod(0o777)
                            else: self.file('readiness.json', {**self.config, 'reference': 'changed'})
                    return result
                self.github.request.side_effect = metadata
                try:
                    with patch.object(attention.HermesTransport, 'send', return_value='accepted') as send:
                        with self.assertRaises(IntakeError if fault == 'record-destination' else (RepositoryError, IntakeError)):
                            self.deliver(public=True)
                        send.assert_not_called()
                    stored = json.loads(self.db.execute('SELECT payload FROM notifications').fetchone()[0])
                    self.assertEqual((stored['state'], stored['attempts']), ('pending', 0) if phase == 1 else ('uncertain', 1))
                    if fault == 'record-destination':
                        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM notifications').fetchone()[0])['attempts'], 0)
                    if phase == 2:
                        # Restore only local policy/records, preserving the truthful uncertain intent.
                        uncertain = deepcopy(stored)
                        restore()
                        attention.save(self.db, uncertain); self.db.commit()
                        with patch.object(attention.HermesTransport, 'send') as send:
                            self.assertEqual(self.deliver()['state'], 'uncertain')
                            send.assert_not_called()
                finally: restore()


class OperationIntegrationTests(fixtures.SupervisedFixture, unittest.TestCase):
    file = fixtures.ReadinessTests.file
    save = fixtures.ReadinessTests.save
    read = fixtures.ReadinessTests.read

    def setUp(self):
        fixtures.EnforcedReadinessTests.setUp(self)
        self.item['ticket_work'] = {'assignment_id': 'tickets', 'inputs': {}, 'request': {},
                                    'tickets': [{'key': 'one'}], 'execution': {'run_id': 'synthesis'}}
        self.save()

    def test_effect_boundaries_refuse_drift_or_denial_without_new_durable_intent(self):
        original = self.readers.execution_support.side_effect
        source = Path(self.config['slots'][0]['skills'][0]['path'])
        raw = source.read_bytes()
        restore = self.snapshot()
        cases = [('drift', resolving, stage) for resolving in (False, True)
                 for stage in ('intent', 'write', 'receipt', 'success')]
        cases += [(fault, False, 'intent') for fault in ('permission', 'health')]
        for fault, resolving, stage in cases:
            with self.subTest(fault=fault, resolving=resolving, stage=stage):
                restore()
                try:
                    if resolving:
                        self.item['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                            'blockers': [{'boundary': 'tracker', 'code': 'publication_uncertain'}]}
                    self.save()
                    resolution = effect_resolution.bound(self.db, self.item, 'ticket-publication', 'tickets') if fault == 'drift' else None
                    if fault == 'drift':
                        tickets.effect_check(self.db, self.item, resolution=resolution)
                        if stage in {'write', 'receipt', 'success'}:
                            self.item['ticket_work']['intents'] = {'one': {'issue': True}}
                            tickets.effect_checkpoint(self.db, self.item, resolution=resolution)
                        if stage in {'receipt', 'success'}:
                            fresh = deepcopy(self.item); fresh['independent_hold'] = 'retained'
                            self.db.execute('UPDATE iterations SET payload=?', (json.dumps(fresh),)); self.db.commit()
                            work = self.item['ticket_work']
                            work['intents']['one']['identity'] = {'number': 8, 'id': 80, 'node_id': 'I8'}
                            tickets.checkpoint(self.db, self.item, work=work, fields=('intents',))
                    before = self.db.execute('SELECT payload FROM iterations').fetchone()[0]
                    def drift(*args):
                        result = original(*args)
                        source.write_bytes(raw + b'changed at independent metadata read-return')
                        return result
                    if fault == 'drift': self.readers.execution_support.side_effect = drift
                    if fault == 'permission': self.write_authority['operations'].remove('projects')
                    if fault == 'health': self.health['sha'] = 'b'*40
                    with self.assertRaises(RepositoryError) as caught:
                        if stage == 'write': tickets.effect_check(self.db, self.item, resolution=resolution)
                        else:
                            self.item['ticket_work']['next_intent' if fault == 'drift' else 'issue_intent'] = 'must-not-save' if fault == 'drift' else True
                            tickets.effect_checkpoint(self.db, self.item, resolution=resolution)
                    self.assertEqual(caught.exception.code, 'readiness_inputs_changed' if fault == 'drift' else 'readiness_blocked')
                    self.assertEqual(before, self.db.execute('SELECT payload FROM iterations').fetchone()[0])
                finally: restore()


class TransportDestinationTests(unittest.TestCase):
    def test_transport_refuses_wrong_exact_destination_before_process(self):
        transport = object.__new__(messaging.HermesTransport)
        transport.destination = {'chat_id': '1', 'thread_id': '2'}
        transport.command = ['/not-executed']
        with patch.object(messaging.subprocess, 'run') as run:
            with self.assertRaises(IntakeError) as caught:
                transport.send({'destination': {'chat_id': '1', 'thread_id': '3'}})
        self.assertEqual(caught.exception.code, 'invalid_origin')
        run.assert_not_called()


class TerminalOperationTests(fixtures.SupervisedFixture, unittest.TestCase):
    setUp = OperationIntegrationTests.setUp
    file = fixtures.ReadinessTests.file
    save = fixtures.ReadinessTests.save
    read = fixtures.ReadinessTests.read
    record = fixtures.CoreReadinessTests.record
    def test_terminal_capacity_allows_existing_effects_not_new_work_and_origin_still_binds(self):
        for slot in self.config['slots']: self.record(slot, 'complete')
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        self.assertEqual(self.admit(operation='effect'), 'admitted')
        with self.assertRaises(RepositoryError): self.admit()
        self.item['ticket_work']['status'] = 'published'
        tickets.effect_checkpoint(self.db, self.item)
        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])['ticket_work']['status'], 'published')
        before = self.db.execute('SELECT payload FROM iterations').fetchone()[0]
        with self.assertRaises(RepositoryError): planning.checkpoint(self.db, self.item)
        self.assertEqual(self.db.execute('SELECT payload FROM iterations').fetchone()[0], before)
        self.health['sha'] = 'b'*40
        self.item.update(status='waiting-for-operator', completion={'report_id': 'completed'}); self.save()
        self.assertEqual(self.admit(operation='notification'), 'admitted')
        self.item['origin']['thread_id'] = 'changed'; self.save()
        with self.assertRaises(RepositoryError): self.admit(operation='notification')
