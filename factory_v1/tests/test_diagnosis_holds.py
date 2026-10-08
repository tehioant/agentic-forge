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

from factory_v1 import assignments, diagnosis, tickets


class OverlappingHoldTests(unittest.TestCase):
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
                            patch.object(tickets.gh, 'read_issue', side_effect=lambda github, repository, n: {'body': bodies[n]}), \
                            patch.object(tickets.gh, 'set_field', side_effect=set_field):
                        for number in (10, 12):
                            with closing(sqlite3.connect(state)) as database:
                                diagnosis.declare(database, 'p', 'i', '42', {'stuck_assignment': identities[number]}, None)
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
