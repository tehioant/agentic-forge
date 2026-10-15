"""Direct records and individual public dispatcher seams; no lifecycle journeys."""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, continuation_dispatch as c, controls, iteration, recovery, restart
from factory_v1 import service, service_dispatch as dispatch
from factory_v1.repositories import RepositoryError, save_iteration


def item():
    return {'project_id': 'p', 'iteration_id': 'm1', 'repository': 'example/product',
            'repository_onboarding': {'metadata': {'id': 7}}, 'approval': {'operator_id': '42', 'reference': 'approved'},
            'status': 'active', 'execution_allowed': False, 'planning': {'status': 'complete'},
            'ticket_work': {'publication_complete': True}}


def row(identity='root', stage='implementation', number=1, **contract):
    return {'assignment_id': identity, 'claim_id': 'claim-' + identity,
            'ticket_scope': {'project': 'p', 'iteration': 'm1', 'repository': 'example/product', 'repository_id': 7},
            'handoff': {'stage': stage, 'issue': {'number': number}, 'profile': {'name': identity, 'home': '/unused/' + identity},
                        **contract}, 'runtime': {'status': 'complete', 'container_removed': True},
            'submitted_result': {'status': 'done'}}


def frontier():
    return {'items': [{'number': n, 'admissible': True, 'contract_valid': True, 'held': False} for n in [3, 2]],
            'eligible': [3, 2], 'foreign_active': [], 'active': []}


def config():
    return {'project': 'p', 'iteration': 'm1', 'operator_id': '42', 'reference': 'approved',
            'implementation': {'claim_id': 'stale-seed'}, 'continuation': {
                'reference': 'approved-frontier', 'slots': [], 'authorities': {k: [] for k in c.KINDS}}}


class SelectionComponents(unittest.TestCase):
    def test_next_eligible_ignores_old_seed_and_consumed_retired_roots(self):
        saved = item()
        saved['delivered_ticket_retirements'] = {'candidate': {'proof': {'lineage': {'old': {}}}}}
        self.assertEqual(c.select(saved, [row('old')], frontier()), c.action('implementation', 2))
        observed = frontier()
        observed['items'][1]['held'] = True
        self.assertEqual(c.select(saved, [row('old')], observed), c.action('implementation', 3))
        observed['foreign_active'] = [99]
        self.assertEqual(c.select(saved, [], observed)['stage'], 'blocked')

    def test_correction_descendant_restarts_simplification_then_both_fresh_axes(self):
        rows = [row(), row('simple', 'simplify', simplification={'implementation_assignment': 'root'}),
                row('old-standards', 'review-standards', review={'pins': {'candidate_assignment': 'simple'}}),
                row('old-spec', 'review-spec', review={'pins': {'candidate_assignment': 'simple'}}),
                row('fix', 'corrections', corrections={'pins': {'candidate_assignment': 'simple'}})]
        self.assertEqual(c.select(item(), rows, frontier()), c.action('simplify', 'fix'))
        rows.append(row('fresh-simple', 'simplify', simplification={'implementation_assignment': 'fix'}))
        self.assertEqual(c.select(item(), rows, frontier()), c.action('review-standards', 'fresh-simple'))
        rows.append(row('fresh-standards', 'review-standards', review={'pins': {'candidate_assignment': 'fresh-simple'}}))
        self.assertEqual(c.select(item(), rows, frontier()), c.action('review-spec', 'fresh-simple'))

    def test_runtime_failure_is_not_stuck_and_separate_debug_cannot_recurse(self):
        original = row()
        for status in ('failed', 'stopped', 'unconfirmed', 'running'):
            original['runtime']['status'] = status
            self.assertEqual(c.select(item(), [original], frontier())['target'], 'attempt_requires_exact_host_retry')
        original['runtime']['status'] = 'complete'
        original['submitted_result']['status'] = 'stuck'
        self.assertEqual(c.select(item(), [original], frontier()), c.action('declare-stuck', 'root'))
        saved = item()
        saved['stuck_work'] = {'root': {'status': 'diagnosis', 'diagnosis_assignment': 'debug'}}
        debug = row('debug', 'diagnosis')
        debug['submitted_result']['status'] = 'stuck'
        self.assertEqual(c.select(saved, [original, debug], frontier()), c.action('blocked', 'diagnosis_requires_operator'))

    def test_registered_repair_wins_over_parked_ordinary_work(self):
        saved = item()
        saved['main_incident'] = {'incident_id': 'incident'}
        self.assertEqual(c.select(saved, [row()], frontier()), c.action('blocked', 'registered_repair_required'))
        saved['main_recoveries'] = {'incident': {'status': 'registered', 'assignment': None, 'config': {'issue': {'number': 9}}}}
        self.assertEqual(c.select(saved, [row()], frontier()), c.action('repair', 9))
        saved['main_recoveries']['incident']['assignment'] = 'repair'
        repair = row('repair', 'repair', 9)
        self.assertEqual(c.select(saved, [row(), repair], frontier()), c.action('simplify', 'repair'))

    def test_waiting_completion_never_selects_more_work(self):
        saved = item()
        saved.update(status='waiting-for-operator', completion={'report_id': 'report'})
        self.assertEqual(c.select(saved, [row()], frontier()), c.action('wait', 'report'))


class CapacityComponents(unittest.TestCase):
    def slot(self, claim, stage='implementation', commands=None):
        return {'claim_id': claim, 'stage': stage, 'profile': {'name': claim, 'home': '/unused/' + claim},
                'skills': [], 'launcher': '/private/' + claim + '.json'}

    def test_unused_literal_capacity_and_exact_inherited_checks(self):
        conf = config()
        slots = [self.slot('used'), self.slot('wrong', 'review-spec'), self.slot('fresh', 'review-spec')]
        conf['continuation']['slots'] = slots
        used = row()
        used['claim_id'] = 'used'
        with patch.object(dispatch, 'installed'), patch.object(c.sandbox_policy, 'load_config', side_effect=lambda path: {
                'verification_commands': ['wrong'] if 'wrong' in path else ['approved']}):
            selected = c.slot_for(conf, [used], 'review-spec', ['approved'])
            self.assertEqual(selected, slots[2])
            with self.assertRaises(RepositoryError) as refused:
                c.slot_for(conf, [used], 'implementation')
            self.assertEqual(refused.exception.code, 'execution_capacity_unavailable')

    def test_full_request_rebinds_live_issue_scoped_revision_and_current_main(self):
        conf = config()
        conf['implementation'].update(repository='example/product', repository_id=7, baseline='a' * 40,
                                      spec_commit='b' * 40, candidate=None, standards=['unchanged'], workspace='/approved/workspace')
        issue = {'number': 2, 'id': 20, 'node_id': 'I_2', 'body': 'fresh body'}
        github = Mock(base='https://api.github.com')
        github.request.return_value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'c' * 40}}
        with patch.object(c.tickets.gh, 'read_issue', return_value=issue), patch.object(c.requirements, 'authority', return_value={'spec': {'commit': 'd' * 40}}):
            bounded = c.request(conf, item(), 2, self.slot('fresh'), github)
        self.assertEqual(bounded['baseline'], 'c' * 40)
        self.assertEqual(bounded['spec_commit'], 'd' * 40)
        self.assertEqual(bounded['issue']['body_sha256'], hashlib.sha256(b'fresh body').hexdigest())
        self.assertEqual(bounded['claim_id'], 'fresh')
        self.assertEqual(bounded['workspace'], '/approved/workspace')
        self.assertEqual(bounded['standards'], ['unchanged'])
        self.assertEqual(conf['implementation']['claim_id'], 'stale-seed')

    def test_literal_slot_schema_rejects_reused_identity_and_wrong_role(self):
        from factory_v1.model_access import SUBSCRIPTION_SCOPE
        provider, model, operation = SUBSCRIPTION_SCOPE
        profile = {'name': 'fresh', 'home': '/unused/fresh', 'role': 'implementation',
                   'provider': provider, 'model': model, 'operation': operation, 'reasoning': 'high'}
        seed = {'stage': 'implementation', 'profile': profile, 'repository': 'example/product', 'repository_id': 7,
                'workspace': '/approved/workspace', 'issue': {'number': 1, 'id': 10, 'node_id': 'I_1', 'body_sha256': 'e' * 64},
                'spec_commit': 'a' * 40, 'baseline': 'b' * 40, 'candidate': None,
                'standards': [c.simplification.artifact('AGENTS.md', 'approved standards')], 'preceding': [], 'skills': [],
                'capabilities': ['read_workspace', 'scratch', 'model', 'write_workspace'],
                'result_contract': assignments.CONTRACT, 'claim_id': 'seed'}
        conf = config()
        conf['implementation'] = seed
        slot = {'stage': 'implementation', 'profile': profile, 'skills': [], 'claim_id': 'fresh', 'launcher': '/private/launcher'}
        conf['continuation']['slots'] = [slot]
        with patch.object(assignments.role_skills, 'snapshots', return_value=[]), patch.object(dispatch, 'installed'):
            self.assertEqual(c.validate(conf, conf), conf)
            conf['continuation']['slots'].append(deepcopy(slot))
            with self.assertRaises(RepositoryError):
                c.validate(conf, conf)
            conf['continuation']['slots'].pop()
            slot['profile']['role'] = 'debug'
            with self.assertRaises(RepositoryError) as refused:
                c.validate(conf, conf)
            self.assertEqual(refused.exception.code, 'profile_blocked')

    def test_real_launcher_limits_are_checked_before_slot_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / 'artifacts'
            artifacts.mkdir(mode=0o700)
            path = root / 'launcher.json'
            launcher = {'image': c.sandbox_policy.IMAGE, 'uid': 1000, 'subscription_socket': str(root / 'broker.sock'),
                        'artifacts_root': str(artifacts), 'limits': {'seconds': 10, 'max_calls': 1, 'memory_mb': 256,
                        'cpus': 0.1, 'pids': 16, 'scratch_mb': 16}, 'verification_commands': ['approved']}
            path.write_text(json.dumps(launcher))
            path.chmod(0o600)
            conf = config()
            slot = self.slot('fresh', 'review-spec')
            slot['launcher'] = str(path)
            conf['continuation']['slots'] = [slot]
            with patch.object(dispatch, 'installed'):
                self.assertEqual(c.slot_for(conf, [], 'review-spec', ['approved']), slot)
                launcher['limits']['max_calls'] = 0
                path.write_text(json.dumps(launcher))
                with self.assertRaises(RepositoryError) as refused:
                    c.slot_for(conf, [], 'review-spec', ['approved'])
                self.assertEqual(refused.exception.code, 'invalid_launcher')

    def test_authority_requires_exact_operator_binding_and_no_ambiguity(self):
        conf = config()
        conf['continuation']['authorities']['review'] = ['/private/one', '/private/two']
        values = {'/private/one': {'operator_id': 'other', 'axes': ['correct']},
                  '/private/two': {'operator_id': '42', 'axes': ['correct']}}
        with patch.object(c.merge, 'controller_paths'), patch.object(c.merge, 'private_file', side_effect=lambda p: p), \
             patch.object(c.publication, 'read_json', side_effect=lambda p: values[p]):
            self.assertEqual(c.authority(Mock(), conf, 'review', lambda v: v.get('axes') == ['correct']), '/private/two')
            values['/private/one']['operator_id'] = '42'
            with self.assertRaises(RepositoryError):
                c.authority(Mock(), conf, 'review', lambda v: v.get('axes') == ['correct'])
            values['/private/two']['axes'] = ['stale']
            values['/private/one']['axes'] = ['stale']
            with self.assertRaises(RepositoryError):
                c.authority(Mock(), conf, 'review', lambda v: v.get('axes') == ['correct'])


class ExecutionComponents(unittest.TestCase):
    def test_rejection_calls_existing_corrections_with_exact_axes_and_fresh_slot(self):
        conf = config()
        candidate = row('simple', 'simplify', simplification={'verification_commands': ['checks']})
        rows = [candidate, row('standards', 'review-standards'), row('spec', 'review-spec')]
        selected = c.action('evaluate', 'simple', axes={'standards_assignment': 'standards', 'spec_assignment': 'spec'})
        slot = {'profile': {'name': 'fix'}, 'skills': [], 'claim_id': 'fresh-fix', 'launcher': '/private/launcher'}
        with patch.object(c.review, 'execution_binding', side_effect=lambda a: {'id': a['assignment_id']}), \
             patch.object(c, 'authority', return_value='/private/review'), \
             patch.object(c.review, 'evaluate', return_value={'status': 'corrections', 'advance_allowed': False}), \
             patch.object(c, 'slot_for', return_value=slot), patch.object(c.merge, 'controller_paths'), \
             patch.object(c.review, 'prepare_corrections') as prepare:
            c.execute(Mock(), ('p', 'm1', '42'), conf, item(), rows, selected, Mock())
        self.assertEqual(prepare.call_args.args[4], {**selected['axes'], 'profile': slot['profile'], 'skills': [], 'claim_id': 'fresh-fix'})

    def test_silence_is_quiet_and_only_stored_response_can_apply_direction(self):
        selected = c.action('direction', 'root', event_id='event')
        with patch.object(c.attention, 'get_record', return_value={'decision_response': None}), \
             patch.object(c.diagnosis, 'apply_direction') as apply:
            result = c.execute(Mock(), ('p', 'm1', '42'), config(), item(), [], selected, Mock())
        self.assertEqual(result['result']['status'], 'waiting')
        apply.assert_not_called()
        with patch.object(c.attention, 'get_record', return_value={'decision_response': {'response': 'actual operator input'}}), \
             patch.object(c.diagnosis, 'apply_direction') as apply:
            c.execute(Mock(), ('p', 'm1', '42'), config(), item(), [], selected, Mock())
        self.assertEqual(apply.call_args.args[4], {'stuck_assignment': 'root', 'event_id': 'event'})

    def test_complete_uses_real_completion_caller_and_readback_not_next_iteration(self):
        completed = {'report_id': 'report', 'event_id': 'event', 'report': {}}
        saved = item()
        saved.update(status='waiting-for-operator', completion=completed)
        delivered = row('candidate', 'simplify')
        delivered['merge'] = {'status': 'delivered'}
        with patch.object(c, 'observe', return_value=frontier()), \
             patch.object(c.iteration, 'required_items', return_value=([{'number': 1}], [], {})), \
             patch.object(c, 'merge_authority', return_value='/private/merge'), \
             patch.object(c.iteration, 'complete', return_value=completed) as complete, \
             patch.object(c.assignments, 'current', return_value=saved), patch.object(c.iteration, 'next_iteration') as next_iteration:
            result = c.execute(Mock(), ('p', 'm1', '42'), config(), item(), [delivered], c.action('complete'), Mock())
        self.assertEqual(result['result']['status'], 'await_next_interview')
        self.assertEqual(complete.call_args.args[4], {'merge_configs': ['/private/merge']})
        next_iteration.assert_not_called()


class PersistentAdmissionComponents(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'state.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.saved = item()
        self.db.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('p', 'm1', json.dumps(self.saved)))
        self.db.commit()
        self.service = {'state': str(self.path), 'project': 'p', 'iteration': 'm1', 'operator_id': '42',
                        'dispatch_config': '/private/dispatch', 'delivery_config': None, 'transport_config': None}

    def test_actual_persistent_pause_blocks_tick_before_any_selection_or_executor(self):
        controls.request(self.db, 'p', 'm1', '42', 'pause', 'operator pause')
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
             patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(c, 'observe') as observe, \
             patch.object(dispatch, 'run_assignment') as launch:
            with self.assertRaises(RepositoryError) as refused:
                dispatch.continuation_tick(self.service, Mock())
        self.assertEqual(refused.exception.code, 'iteration_paused')
        observe.assert_not_called()
        launch.assert_not_called()

    def test_completed_wait_is_idempotent_without_restart_or_worker(self):
        self.saved.update(status='waiting-for-operator', completion={'report_id': 'report'})
        save_iteration(self.db, self.saved)
        self.db.commit()
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
             patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(restart, 'before_dispatch') as before, \
             patch.object(dispatch, 'run_assignment') as launch:
            for _ in range(2):
                self.assertEqual(dispatch.continuation_tick(self.service, Mock())['status'], 'await_next_interview')
        before.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0]), self.saved)

    def test_delivered_retirement_precedes_restart_even_with_persisted_old_hold(self):
        self.saved['reservation'] = {'issue_number': 1}
        self.saved['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                                                'blockers': [{'boundary': 'tracker', 'code': 'reservation_conflict'}]}
        save_iteration(self.db, self.saved)
        self.db.commit()
        delivered = row('candidate', 'simplify')
        delivered['merge'] = {'status': 'delivered'}
        calls = []
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
             patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(assignments, 'rows', return_value=[delivered]), \
             patch.object(c, 'merge_authority', return_value='/private/merge'), \
             patch.object(c.tickets, 'advance_delivered', side_effect=lambda *a: calls.append('retire')) as advance, \
             patch.object(restart, 'reconcile', side_effect=lambda *a: calls.append('restart')), patch.object(c, 'observe') as observe:
            result = dispatch.continuation_tick(self.service, Mock())
        self.assertEqual(result['stage'], 'retire-delivered')
        self.assertEqual(calls, ['retire', 'restart'])
        self.assertEqual(advance.call_args.args[3], '/private/merge')
        observe.assert_not_called()

    def test_next_ticket_tick_prepares_fresh_envelope_without_old_claim(self):
        slot = {'stage': 'implementation', 'profile': {'name': 'fresh', 'home': '/unused/fresh'},
                'skills': [], 'claim_id': 'fresh', 'launcher': '/private/launcher'}
        conf = config()
        conf['implementation'].update(repository='example/product', repository_id=7, workspace='/approved/workspace',
                                      baseline='a' * 40, spec_commit='b' * 40, candidate=None)
        github = Mock(base='https://api.github.com')
        github.request.return_value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'c' * 40}}
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=conf), \
             patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(c, 'observe', return_value=frontier()), \
             patch.object(c, 'slot_for', return_value=slot), patch.object(restart, 'before_dispatch') as before, \
             patch.object(c.tickets.gh, 'read_issue', return_value={'number': 2, 'id': 20, 'node_id': 'I_2', 'body': 'current requirement'}), \
             patch.object(c.requirements, 'authority', return_value={'spec': {'commit': 'd' * 40}}), \
             patch.object(c.tickets, 'reserve') as reserve, patch.object(c.assignments, 'prepare') as prepare:
            self.assertEqual(dispatch.continuation_tick(self.service, github)['stage'], 'implementation')
        bounded = prepare.call_args.args[4]
        self.assertEqual(bounded['claim_id'], 'fresh')
        self.assertEqual(bounded['issue']['number'], 2)
        self.assertEqual(bounded['baseline'], 'c' * 40)
        self.assertEqual(bounded['spec_commit'], 'd' * 40)
        self.assertEqual(before.call_args.kwargs['request'], bounded)
        self.assertEqual(reserve.call_args.args[3], 2)

    def test_selected_launch_calls_actual_existing_executor_and_requires_readback(self):
        prepared = row()
        prepared.pop('runtime')
        finished = row()
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
             patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(assignments, 'rows', return_value=[prepared]), \
             patch.object(c, 'observe', return_value=frontier()), patch.object(restart, 'before_dispatch') as before, \
             patch.object(c, 'launcher_for', return_value='/private/launcher'), \
             patch.object(dispatch, 'run_assignment') as launch, patch.object(assignments, 'inspect', return_value=finished):
            result = dispatch.continuation_tick(self.service, Mock())
        self.assertEqual(result['stage'], 'launch')
        launch.assert_called_once_with(self.service, 'root', '/private/launcher', stop_event=None)
        self.assertEqual(before.call_args.kwargs['assignment_id'], 'root')

    def test_registered_health_exception_cannot_release_other_holds_or_pause(self):
        self.saved.update(main_incident={'incident_id': 'incident'},
                          main_recoveries={'incident': {'status': 'registered'}})
        self.saved['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                                                'blockers': [{'boundary': 'delivery', 'code': 'main_frozen'}]}
        save_iteration(self.db, self.saved)
        self.db.commit()
        restart.require_reconciled(self.db, 'p', 'm1', allow_recovery=True)
        with self.assertRaises(RepositoryError):
            restart.require_reconciled(self.db, 'p', 'm1')
        self.saved['restart_reconciliation']['blockers'].append({'boundary': 'model', 'code': 'operation_uncertain'})
        save_iteration(self.db, self.saved)
        self.db.commit()
        with self.assertRaises(RepositoryError):
            restart.require_reconciled(self.db, 'p', 'm1', allow_recovery=True)
        controls.request(self.db, 'p', 'm1', '42', 'pause', 'pause')
        with self.assertRaises(RepositoryError):
            restart.require_reconciled(self.db, 'p', 'm1', allow_recovery=True)

    def test_registered_repair_request_reaches_real_before_dispatch_scope_guards(self):
        pin = {'number': 9, 'id': 90, 'node_id': 'I_9', 'body_sha256': 'a' * 64}
        record = {'status': 'registered', 'assignment': None, 'parked': [], 'removable': [],
                  'config': {'incident_id': 'incident', 'evidence_id': 'e' * 64, 'issue': pin,
                             'paths': ['app.py'], 'removable_assignments': []}, 'evidence': {'main': 'b' * 40}}
        self.saved.update(main_incident={'incident_id': 'incident'}, main_recoveries={'incident': record})
        report = {'status': 'ready', 'blockers': [], 'observations': {
            'delivery': {'evidence': {'main': 'b' * 40}}, 'final-recovery': recovery.contract(record)}}
        self.saved['restart_reconciliation'] = report
        save_iteration(self.db, self.saved)
        self.db.commit()
        bounded = {'stage': 'repair', 'issue': pin, 'baseline': 'b' * 40, 'candidate': None,
                   'preceding': recovery.preceding(record), 'claim_id': 'fresh'}
        with patch.object(restart, 'reconcile', return_value=report):
            restart.before_dispatch(self.db, 'p', 'm1', '42', Mock(), force=True, request=bounded)
            for drift in ({'baseline': 'c' * 40}, {'issue': {**pin, 'number': 1}}, {'stage': 'implementation'}):
                with self.subTest(drift=drift), self.assertRaises(RepositoryError):
                    restart.before_dispatch(self.db, 'p', 'm1', '42', Mock(), force=True, request={**bounded, **drift})

    def test_integrated_repair_calls_existing_verifier_before_retirement(self):
        record = {'status': 'repair', 'assignment': 'repair',
                  'config': {'incident_id': 'incident', 'evidence_id': 'e' * 64,
                             'paths': ['app.py'], 'removable_assignments': []}, 'evidence': {'main': 'b' * 40}}
        self.saved.update(reservation={'issue_number': 9}, main_incident={'incident_id': 'incident'},
                          main_recoveries={'incident': record})
        save_iteration(self.db, self.saved)
        self.db.commit()
        delivered = row('candidate', 'simplify', 9, recovery=recovery.contract(record))
        delivered['merge'] = {'status': 'delivered'}
        calls = []
        def clear(*args):
            calls.append('verify-recovery')
            self.saved.pop('main_incident')
            save_iteration(self.db, self.saved)
            self.db.commit()
        with patch.object(c, 'merge_authority', return_value='/private/merge'), \
             patch.object(restart, 'before_dispatch') as before, patch.object(recovery, 'verify', side_effect=clear), \
             patch.object(c.tickets, 'advance_delivered', side_effect=lambda *a: calls.append('retire')), \
             patch.object(restart, 'reconcile', side_effect=lambda *a: calls.append('restart')):
            self.assertTrue(c.retire_delivered(self.db, ('p', 'm1', '42'), config(), self.saved, [delivered], Mock()))
        self.assertEqual(calls, ['verify-recovery', 'retire', 'restart'])
        self.assertEqual(before.call_args.kwargs, {'force': True, 'assignment_id': 'candidate', 'verify_recovery': True})

    def test_unhealthy_cycle_dispatches_only_for_registered_repair_selection(self):
        self.service['delivery_config'] = '/private/delivery'
        with patch.object(service.delivery, 'load_config', return_value={}), patch.object(service.delivery, 'sample', return_value={'evidence': {'status': 'unknown'}}), \
             patch.object(dispatch.merge, 'controller_paths'), patch.object(service, 'dispatch') as invoke:
            self.assertEqual(service.cycle(self.service, Mock())['blockers'], ['delivery_not_healthy'])
            invoke.assert_not_called()
            self.saved.update(main_incident={'incident_id': 'incident'}, main_recoveries={'incident': {'status': 'registered'}})
            save_iteration(self.db, self.saved)
            self.db.commit()
            service.cycle(self.service, Mock())
            invoke.assert_called_once()
