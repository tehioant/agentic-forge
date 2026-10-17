"""Hold bookkeeping units; only GitHub and retained responsible evidence are simulated."""
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from factory_v1 import assignments, controls, diagnosis, repositories, requirements, tickets
from factory_v1.tests.test_tickets import FrontierFixture


class OverlappingHoldTests(unittest.TestCase):
    def test_final_hold_effect_admission_native_and_legacy_matrix(self):
        for native in (True, False):
            for release in (True, False):
                for fault in ('none', 'pause', 'body', 'identity', 'progress', 'readback'):
                    with self.subTest(native=native, release=release, fault=fault):
                        fixture = FrontierFixture(self, native=native)
                        database, item, github = fixture.database, fixture.item, fixture.github
                        record = {'pins': {'stuck_assignment': 'original'}, 'status': 'release-pending' if release else 'diagnosis',
                            'affected': [10], 'previous_statuses': {'10': 'active'},
                            'issues': {'10': requirements.issue_pin(fixture.issues[10])}, 'blocks_verified': False}
                        item['stuck_work'] = {'original': record}
                        if release:
                            fixture.members[10]['fields']['STATUS'] = 'In Progress' if native else 'Blocked'
                        fixture.persist()
                        reads = 0
                        def read_issue(*args):
                            nonlocal reads
                            reads += 1
                            issue = copy.deepcopy(fixture.issues[10])
                            if reads == 2:
                                if fault == 'pause': fixture.pause()
                                if fault == 'body': issue['body'] += 'changed'
                                if fault == 'identity': issue['id'] = 999
                            return issue
                        calls = 0
                        def frontier(db, value, gh):
                            nonlocal calls
                            calls += 1
                            observed = copy.deepcopy(value)
                            row = copy.deepcopy(fixture.work['frontier']['items'][0])
                            options = fixture.start_request['tracker']['statuses']
                            row['status'] = next(role for role, name in options.items() if name == fixture.members[10]['fields']['STATUS'])
                            if fault == 'progress' and calls == 2: row['status'] = 'done'
                            if fault == 'readback' and calls == 3: row['membership_id'] = 'replaced'
                            observed['ticket_work']['frontier'] = {'items': [row]}
                            return observed
                        with patch.object(tickets, 'frontier', side_effect=frontier), patch.object(tickets.gh, 'read_issue', side_effect=read_issue):
                            if fault == 'none':
                                result = diagnosis.sync_blocks(database, item, record, github, release=release)
                                self.assertEqual(fixture.members[10]['fields']['STATUS'],
                                    'Todo' if native and release else 'Ready' if release else 'In Progress' if native else 'Blocked')
                                self.assertIn('hold_representation', result['stuck_work']['original'])
                            else:
                                with self.assertRaises(repositories.RepositoryError):
                                    diagnosis.sync_blocks(database, item, record, github, release=release)
                                if fault != 'readback': fixture.set_field.assert_not_called()
                                self.assertFalse(fixture.stored()['stuck_work']['original']['blocks_verified'])
                                if fault == 'pause': self.assertTrue(controls.blocked(database, 'product', 'm1'))
                        fixture.stack.close()

    def test_overlap_restores_underlying_status_in_either_release_order_after_restart(self):
        for order in ((10, 12), (12, 10)):
            for underlying in ('ready', 'blocked', 'deferred', 'active'):
                with self.subTest(order=order, underlying=underlying), tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
                    state = Path(directory) / 'holds.sqlite'
                    statuses = {10: 'ready', 11: underlying, 12: 'ready'}
                    bodies = {n: f'LABELED original requirement {n}' for n in statuses}
                    identities = {10: 'a' * 64, 12: 'b' * 64}
                    item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'example/product',
                            'status': 'active', 'execution_allowed': False,
                            'approval': {'operator_id': '42', 'reference': 'LABELED approval'},
                            'ticket_work': {'board': {'project_id': 'P', 'status': {'id': 'STATUS', 'options': {s: s for s in statuses.values()} | {'blocked': 'blocked'}}},
                                            'request': {'tracker': {'statuses': {s: s for s in ('ready', 'blocked', 'deferred', 'active')}}}}}
                    with closing(sqlite3.connect(state)) as database:
                        database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
                        database.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(item)))
                        database.commit()

                    def frontier(database, observed, github):
                        observed = copy.deepcopy(observed)
                        observed['ticket_work']['frontier'] = {'items': [
                            {'number': n, 'id': 1000 + n, 'node_id': f'I{n}', 'membership_id': str(n),
                             'issue_state': 'open', 'status': statuses[n], 'blockers': [10, 12] if n == 11 else []}
                            for n in statuses]}
                        tickets.checkpoint(database, observed)
                        return observed

                    def source(database, project, iteration, operator, identity):
                        number = next(n for n, value in identities.items() if value == identity)
                        prior = {'handoff': {'issue': {'number': number}}, 'ticket_scope': {'issue_number': number}}
                        pins = {'stuck_assignment': identity, 'issue_sha256': hashlib.sha256(bodies[number].encode()).hexdigest()}
                        return prior, None, {}, {}, {}, pins

                    def set_field(github, board, membership, field, option):
                        statuses[int(membership)] = option

                    with patch.object(tickets, 'frontier', side_effect=frontier), patch.object(diagnosis, 'source', side_effect=source), \
                            patch.object(tickets.gh, 'read_issue', side_effect=lambda github, repository, n: {'body': bodies[n], 'state': 'open', 'id': 1000+n, 'node_id': f'I{n}'}), \
                            patch.object(tickets.gh, 'set_field', side_effect=set_field):
                        item['stuck_work'] = {identities[n]: {'status': 'diagnosis', 'sequence': index + 1,
                            'pins': source(None, None, None, None, identities[n])[-1], 'affected': [n, 11],
                            'issues': {str(k): {'id': 1000+k, 'node_id': f'I{k}',
                                'body_sha256': hashlib.sha256(bodies[k].encode()).hexdigest()} for k in (n, 11)},
                            'previous_statuses': {str(n): 'ready', '11': underlying},
                            'retired_assignments': [], 'blocks_verified': True} for index, n in enumerate((10, 12))}
                        statuses.update({n: 'blocked' for n in statuses})
                        with closing(sqlite3.connect(state)) as database:
                            tickets.checkpoint(database, item)
                        self.assertEqual(statuses[11], 'blocked')
                        for index, number in enumerate(order):
                            with closing(sqlite3.connect(state)) as database:
                                current = assignments.current(database, 'p', 'i', '42')
                                record = current['stuck_work'][identities[number]]
                                observed = diagnosis.sync_blocks(database, current, record, None, release=True)
                                observed['stuck_work'][identities[number]]['status'] = 'released'
                                tickets.checkpoint(database, observed)
                            expected = 'blocked' if index == 0 else ('ready' if underlying == 'active' else underlying)
                            self.assertEqual(statuses[11], expected)
                        with closing(sqlite3.connect(state)) as database:
                            self.assertEqual(diagnosis.held_numbers(assignments.current(database, 'p', 'i', '42')), set())


if __name__ == '__main__':
    unittest.main()
