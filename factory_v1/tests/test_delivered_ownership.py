"""Direct ownership records and independent reads; no worker/controller journeys."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from factory_v1 import assignments, controls, iteration, merge, requirements, restart, tickets
from factory_v1.repositories import RepositoryError, save_iteration


class DeliveredOwnershipComponents(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = sqlite3.connect(Path(temporary.name) / 'state.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        self.issue = {'number': 1, 'id': 10, 'node_id': 'I_1', 'state': 'closed',
                      'state_reason': 'completed', 'body': 'accepted requirements'}
        self.pin = {k: self.issue[k] for k in ('number', 'id', 'node_id')}
        self.pin['body_sha256'] = hashlib.sha256(self.issue['body'].encode()).hexdigest()
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'approval': {'operator_id': '42', 'reference': 'approved'},
                     'status': 'active', 'execution_allowed': False, 'correlation': {'stage': 'delivery'},
                     'handoff': {'commit': 'a' * 40},
                     'repository_onboarding': {'status': 'verified', 'metadata': {'id': 7}},
                     'ticket_work': {'assignment_id': 'batch', 'input_digest': 'b' * 64,
                                     'publication_complete': True,
                                     'board': {'project_id': 'P', 'status': {'id': 'F', 'options': {'Active': 'A'}}},
                                     'request': {'tracker': {'statuses': {'active': 'Active'}}}},
                     'reservation': {'issue_number': 1, 'run_id': 'reservation-1', 'status': 'reserved',
                                     'assignment_id': 'batch', 'input_digest': 'b' * 64}}
        self.revision = requirements.authority(self.item, 1)
        self.scope = {'project': 'product', 'iteration': 'm1', 'repository': 'example/product',
                      'repository_id': 7, 'issue_number': 1, 'issue_id': 10, 'workspace': '/fixture/workspace'}
        self.config = {'issue': 1, 'base': 'main', 'repository': 'example/product', 'repository_id': 7,
                       'api_base': 'https://api.github.com', 'bearer': 'fixture', 'pr_number': 3,
                       'candidate_assignment': 'candidate', 'standards_assignment': 'standards',
                       'spec_assignment': 'spec', 'head': 'c' * 40}
        self.evidence = {'candidate': 'c' * 40, 'integrated': 'd' * 40, 'tree': 'e' * 40,
                         'parents': ['a' * 40, 'c' * 40], 'main': 'd' * 40}
        self.authority = {k: v for k, v in self.config.items() if k != 'bearer'}
        self.records = {}
        for identity, stage in [('builder', 'implementation'), ('candidate', 'simplify'),
                                ('standards', 'review-standards'), ('spec', 'review-spec')]:
            handoff = {'stage': stage, 'issue': copy.deepcopy(self.pin), 'skills': [],
                       'profile': {'name': identity, 'home': '/fixture/' + identity}}
            self.records[identity] = {'assignment_id': identity, 'claim_id': 'claim-' + identity,
                                      'handoff': handoff, 'handoff_digest': assignments.digest(handoff),
                                      'ticket_scope': copy.deepcopy(self.scope), 'revision': self.revision,
                                      'runtime': {'status': 'complete', 'container_removed': True, 'run_id': 'run-' + identity}}
        self.records['candidate']['merge'] = {'status': 'delivered', 'failure': None, 'close_allowed': True,
                                              'authority': copy.deepcopy(self.authority),
                                              'integrated_evidence': copy.deepcopy(self.evidence),
                                              'delivery_checks': ['integrated-check']}
        self.row = {'number': 1, 'id': 10, 'node_id': 'I_1', 'status': 'done', 'issue_state': 'closed',
                    'state_reason': 'completed', 'contract_valid': True, 'held': False, 'admissible': False}
        self.observed = {'authority': 'github', 'items': [self.row], 'active': [], 'foreign_active': [], 'eligible': [2]}
        self.github = SimpleNamespace(base=self.config['api_base'])
        self.db.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'm1', json.dumps(self.item)))
        self.store()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.reads = {}
        api = Mock()
        api.read.return_value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'd' * 40}}
        for module, name, value in [(iteration, 'frontier', self.observed),
                                    (requirements, 'revalidate_inputs', {}),
                                    (merge, 'load_config', self.config), (merge, 'MergeGitHub', api),
                                    (merge, 'chain', (self.records['candidate'], {}, Path('/fixture'))),
                                    (merge, 'issue', self.issue), (merge, 'require_publication', None),
                                    (merge, 'mapped_candidate', {}), (merge, 'target', {}),
                                    (merge, 'merged_evidence', self.evidence), (merge, 'protection', {}),
                                    (merge, 'checks', ['integrated-check'])]:
            self.reads[name] = self.stack.enter_context(patch.object(module, name, return_value=value))

    def store(self):
        save_iteration(self.db, self.item)
        for identity, record in self.records.items():
            self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?, ?)', (identity, json.dumps(record)))
        self.db.commit()

    def stored(self):
        return json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])

    def rows(self):
        return self.db.execute('SELECT assignment_id,payload FROM role_assignments ORDER BY assignment_id').fetchall()

    def advance(self):
        return tickets.advance_delivered(self.db, self.item, self.github, '/fixture/merge.json')

    def seed_receipt(self):
        delivered = {'issue': {'number': 1, 'id': 10, 'url': 'https://github.com/example/product/issues/1'},
                     'pr': {'number': 3, 'url': 'https://github.com/example/product/pull/3'},
                     'assignment_id': 'candidate', 'requirements': self.revision,
                     'reviews': ['standards', 'spec'], 'integration': self.evidence, 'checks': ['integrated-check']}
        proof = {'reservation': self.item['reservation'], 'scope': self.scope, 'issue': self.pin,
                 'revision': self.revision, 'candidate': 'candidate', 'merge_authority': self.authority,
                 'delivered': delivered,
                 'lineage': {identity: {'handoff_digest': a['handoff_digest'], 'claim_id': a['claim_id'],
                                       'revision': a['revision'], 'run_id': a['runtime']['run_id'],
                                       'record_sha256': assignments.digest(a)} for identity, a in self.records.items()}}
        self.item['delivered_ticket_retirements'] = {'candidate': {'receipt_id': assignments.digest(proof), 'proof': proof}}
        self.item['reservation_history'] = [self.item.pop('reservation')]
        self.store()

    def test_contract_exists_before_building_the_dispatcher(self):
        self.assertTrue(callable(tickets.advance_delivered))
        self.assertTrue(callable(assignments.delivered_ticket_claim))

    def test_exact_delivery_atomically_archives_reservation_and_is_idempotent(self):
        before_rows, reservation = self.rows(), copy.deepcopy(self.item['reservation'])
        result = self.advance()
        self.assertNotIn('reservation', result)
        self.assertEqual(result['reservation_history'], [reservation])
        self.assertEqual(result['status'], 'active')
        proof = result['delivered_ticket_retirements']['candidate']['proof']
        self.assertEqual(proof['issue'], self.pin)
        self.assertEqual(proof['merge_authority'], self.authority)
        self.assertNotIn('bearer', proof['merge_authority'])
        self.assertEqual(set(proof['lineage']), set(self.records))
        self.assertEqual(self.advance(), result)
        self.assertEqual(self.stored(), result)
        self.assertEqual(self.rows(), before_rows)
        self.assertTrue(all(assignments.delivered_ticket_claim(self.db, a) for a in self.records.values()))
        self.assertEqual(restart.ownership(self.db, result)['assignments'], [])
        self.reads['chain'].assert_called()
        self.reads['checks'].assert_called()

    def test_delivery_and_cleanup_refusals_leave_rows_and_reservation_unchanged(self):
        cases = [('issue', 'id', 999), ('issue', 'state', 'open'), ('issue', 'body', 'changed'),
                 ('evidence', 'candidate', 'f' * 40), ('config', 'repository_id', 99),
                 ('config', 'head', 'f' * 40), ('config', 'candidate_assignment', 'other'),
                 ('runtime', 'container_removed', None), ('runtime', 'container_removed', False),
                 ('runtime', 'status', 'running'), ('journal', 'status', 'merged'),
                 ('journal', 'failure', {'code': 'rejected'})]
        for target, field, value in cases:
            obj = {'issue': self.issue, 'evidence': self.evidence, 'config': self.config,
                   'runtime': self.records['builder']['runtime'], 'journal': self.records['candidate']['merge']}[target]
            old = obj[field]
            obj[field] = value
            self.store()
            before, rows = self.stored(), self.rows()
            with self.subTest(target=target, field=field), self.assertRaises(RepositoryError):
                self.advance()
            self.assertEqual(self.stored(), before)
            self.assertEqual(self.rows(), rows)
            obj[field] = old
        self.store()
        self.reads['checks'].return_value = ['wrong-check']
        before = self.stored()
        with self.assertRaises(RepositoryError):
            self.advance()
        self.assertEqual(self.stored(), before)

    def test_missing_runtime_is_not_unknown_cleanup_but_only_retired_unlaunched_history(self):
        self.records['historical'] = copy.deepcopy(self.records['builder'])
        self.records['historical']['assignment_id'] = 'historical'
        self.records['historical'].pop('runtime')
        self.store()
        with self.assertRaises(RepositoryError) as refused:
            self.advance()
        self.assertEqual(refused.exception.code, 'cleanup_required')
        self.records['historical']['requirement_disposition'] = {'change_id': 'old'}
        self.item['requirement_changes'] = {'old': {'kind': 'material', 'status': 'resumed', 'affected': [1], 'blocks_verified': True}}
        self.store()
        self.assertIn('delivered_ticket_retirements', self.advance())

    def test_shared_predicate_rejects_resealed_identity_revision_authority_and_cleanup_drift(self):
        self.seed_receipt()
        original = copy.deepcopy(self.item)
        for field, value in [('scope', dict(self.scope, issue_id=999)), ('revision', {'spec': {}}),
                             ('candidate', 'other'), ('merge_authority', dict(self.authority, head='f' * 40))]:
            proof = self.item['delivered_ticket_retirements']['candidate']['proof']
            proof[field] = value
            self.item['delivered_ticket_retirements']['candidate']['receipt_id'] = assignments.digest(proof)
            self.store()
            with self.subTest(field=field):
                self.assertFalse(assignments.delivered_ticket_claim(self.db, self.records['builder']))
                with self.assertRaises(RepositoryError):
                    assignments.ticket_claims(self.db, dict(self.scope, issue_number=2, issue_id=20))
            self.item = copy.deepcopy(original)
        self.records['builder']['runtime']['container_removed'] = None
        proof = self.item['delivered_ticket_retirements']['candidate']['proof']
        proof['lineage']['builder']['record_sha256'] = assignments.digest(self.records['builder'])
        self.item['delivered_ticket_retirements']['candidate']['receipt_id'] = assignments.digest(proof)
        self.store()
        self.assertFalse(assignments.delivered_ticket_claim(self.db, self.records['builder']))

    def test_current_requirement_and_final_frontier_guards_are_not_masked_by_checksums(self):
        self.seed_receipt()
        revised = dict(self.revision, ticket_inputs='f' * 64)
        candidate = self.records['candidate']
        candidate['revision'] = revised
        receipt = self.item['delivered_ticket_retirements']['candidate']
        proof = receipt['proof']
        proof['revision'] = proof['delivered']['requirements'] = revised
        proof['lineage']['candidate']['revision'] = revised
        proof['lineage']['candidate']['record_sha256'] = assignments.digest(candidate)
        receipt['receipt_id'] = assignments.digest(proof)
        self.store()
        self.assertFalse(assignments.delivered_ticket_claim(self.db, candidate))
        changed = copy.deepcopy(self.observed)
        changed['items'][0]['held'] = True
        self.reads['frontier'].side_effect = [copy.deepcopy(self.observed), changed]
        # Restore the candidate before isolating the final frontier check.
        candidate['revision'] = self.revision
        self.store()
        before, rows = self.stored(), self.rows()
        with self.assertRaises(RepositoryError) as refused:
            self.advance()
        self.assertEqual(refused.exception.code, 'delivery_race')
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.rows(), rows)

    def test_late_pause_and_independent_holds_refuse_without_overwriting_them(self):
        holds = [{'stuck_work': {'old': {'status': 'awaiting-direction', 'affected': [1]}}},
                 {'requirement_changes': {'old': {'kind': 'material', 'status': 'held', 'affected': [1]}}},
                 {'main_recoveries': {'incident': {'status': 'registered', 'removed': {}}}},
                 {'bug_work': {'defect': {'status': 'discovered', 'discovery': {'scope': 'current',
                    'affected': [1], 'symptoms': 'x', 'expected': 'y', 'actual': 'z'}}}}]
        for hold in holds:
            with self.subTest(hold=hold):
                original = copy.deepcopy(self.item)
                def inject(*args):
                    latest = self.stored()
                    latest.update(copy.deepcopy(hold))
                    save_iteration(self.db, latest)
                    self.db.commit()
                self.reads['revalidate_inputs'].side_effect = inject
                before_rows = self.rows()
                with self.assertRaises(RepositoryError):
                    self.advance()
                self.assertEqual(self.stored(), dict(original, **hold))
                self.assertEqual(self.rows(), before_rows)
                self.item = original
                self.store()
        def pause(*args):
            with controls.lane(self.db, 'product', 'm1'):
                controls.write(self.db, {'project': 'product', 'iteration': 'm1', 'operator_id': '42',
                    'action': 'pause', 'state': 'requested', 'reference': 'late pause', 'request_id': 'pause1',
                    'history': [], 'cleanup': {}, 'checkpoint': controls.snapshot(self.db, 'product', 'm1')})
        self.reads['revalidate_inputs'].side_effect = pause
        before = self.stored()
        with self.assertRaises(RepositoryError) as refused:
            self.advance()
        self.assertEqual(refused.exception.code, 'iteration_paused')
        self.assertTrue(controls.blocked(self.db, 'product', 'm1'))
        self.assertEqual(self.stored(), before)

    def reserve_second(self):
        state = {'active': False}
        def observed(db, item, github):
            item = copy.deepcopy(item)
            row = dict(self.row, number=2, id=20, node_id='I_2', membership_id='M_2',
                       status='active' if state['active'] else 'ready', issue_state='open', admissible=True)
            item['ticket_work']['frontier'] = {'items': [row], 'foreign_active': [],
                'active': [2] if state['active'] else [], 'eligible': [] if state['active'] else [2]}
            return item
        with patch.object(restart, 'before_dispatch'), patch.object(tickets, 'frontier', side_effect=observed), \
             patch.object(tickets.gh, 'set_field', side_effect=lambda *a: state.update(active=True)) as mutation, \
             patch.object(assignments, 'ticket_claims', wraps=assignments.ticket_claims) as guard:
            result = tickets.reserve(self.db, self.item, self.github, 2)
        guard.assert_called_once()
        mutation.assert_called_once()
        self.assertEqual(self.stored()['reservation']['issue_number'], 2)
        self.assertEqual(result['reservation']['status'], 'reserved')
        return result

    def test_second_reservation_reaches_real_ownership_guard_with_direct_receipt(self):
        self.seed_receipt()
        result = self.reserve_second()
        scope = dict(self.scope, issue_number=2, issue_id=20)
        request = {'stage': 'implementation', 'claim_id': 'fresh', 'profile': {'name': 'fresh', 'home': '/fixture/fresh'}}
        assignments.claims(self.db, request, scope, 'new')
        for key, value in [('claim_id', 'claim-builder'), ('profile', self.records['builder']['handoff']['profile'])]:
            with self.subTest(reuse=key), self.assertRaises(RepositoryError):
                assignments.claims(self.db, dict(request, **{key: value}), scope, 'new')
        self.assertEqual(restart.ownership(self.db, result)['assignments'], [])

    def test_role_claim_then_second_reservation_uses_the_same_retirement_authority(self):
        self.seed_receipt()
        scope = dict(self.scope, issue_number=2, issue_id=20)
        profile = {'name': 'fresh', 'home': '/fixture/fresh'}
        assignments.claims(self.db, {'stage': 'implementation', 'claim_id': 'fresh', 'profile': profile}, scope, 'fresh')
        handoff = {'stage': 'implementation', 'profile': profile}
        self.records['fresh'] = {'assignment_id': 'fresh', 'claim_id': 'fresh', 'ticket_scope': scope,
                                 'handoff': handoff, 'handoff_digest': assignments.digest(handoff)}
        self.store()
        self.reserve_second()
        self.assertEqual(restart.ownership(self.db, self.stored())['assignments'], ['fresh'])

    def test_without_receipt_second_ticket_conflicts_and_retired_results_cannot_be_reused(self):
        before, rows = self.stored(), self.rows()
        with self.assertRaises(RepositoryError) as refused:
            assignments.ticket_claims(self.db, dict(self.scope, issue_number=2, issue_id=20))
        self.assertEqual(refused.exception.code, 'claim_conflict')
        with patch.object(restart, 'before_dispatch'), patch.object(tickets, 'frontier') as observation:
            with self.assertRaises(RepositoryError) as refused:
                tickets.reserve(self.db, self.item, self.github, 2)
            self.assertEqual(refused.exception.code, 'claim_conflict')
            observation.assert_not_called()
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.rows(), rows)
        self.seed_receipt()
        with self.assertRaises(RepositoryError) as refused:
            assignments.store_result(self.db, 'product', 'm1', '42', 'builder', {}, self.github)
        self.assertEqual(refused.exception.code, 'stale_assignment')
