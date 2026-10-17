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
            'repository_onboarding': {'metadata': {'id': 7}, 'api_base': 'https://api.github.com'},
            'approval': {'operator_id': '42', 'reference': 'approved'},
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
    def test_selection_frontier_and_terminal_wait_matrix(self):
        retired = item()
        retired['delivered_ticket_retirements'] = {'candidate': {'proof': {'lineage': {'old': {}}}}}
        held = frontier()
        held['items'][1]['held'] = True
        foreign = frontier()
        foreign['foreign_active'] = [99]
        incident = dict(item(), main_incident={'incident_id': 'incident'})
        registered = deepcopy(incident)
        registered['main_recoveries'] = {'incident': {'status': 'registered', 'assignment': None, 'config': {'issue': {'number': 9}}}}
        repairing = deepcopy(registered)
        repairing['main_recoveries']['incident']['assignment'] = 'repair'
        waiting = dict(item(), status='waiting-for-operator', completion={'report_id': 'report'})
        cases = (
            ('retired-root', retired, [row('old')], frontier(), c.action('implementation', 2)),
            ('held-frontier', retired, [row('old')], held, c.action('implementation', 3)),
            ('foreign-active', retired, [], foreign, {'stage': 'blocked'}),
            ('unregistered', incident, [row()], frontier(), c.action('blocked', 'registered_repair_required')),
            ('registered', registered, [row()], frontier(), c.action('repair', 9)),
            ('repair-ready', repairing, [row(), row('repair', 'repair', 9)], frontier(), c.action('simplify', 'repair')),
            ('completed-wait', waiting, [row()], frontier(), c.action('wait', 'report')),
        )
        for label, saved, rows, observed, expected in cases:
            with self.subTest(case=label):
                selected = c.select(deepcopy(saved), deepcopy(rows), deepcopy(observed))
                self.assertEqual({key: selected[key] for key in expected}, expected)

    def test_correction_and_failure_routing_matrix(self):
        base = [row(), row('simple', 'simplify', simplification={'implementation_assignment': 'root'}),
                row('old-standards', 'review-standards', review={'pins': {'candidate_assignment': 'simple'}}),
                row('old-spec', 'review-spec', review={'pins': {'candidate_assignment': 'simple'}}),
                row('fix', 'corrections', corrections={'pins': {'candidate_assignment': 'simple'}})]
        simple = row('fresh-simple', 'simplify', simplification={'implementation_assignment': 'fix'})
        standards = row('fresh-standards', 'review-standards', review={'pins': {'candidate_assignment': 'fresh-simple'}})
        cases = [('correction', item(), base, c.action('simplify', 'fix')),
                 ('fresh-simplification', item(), base + [simple], c.action('review-standards', 'fresh-simple')),
                 ('fresh-standards', item(), base + [simple, standards], c.action('review-spec', 'fresh-simple'))]
        for status in ('failed', 'stopped', 'unconfirmed', 'running'):
            failed = row()
            failed['runtime']['status'] = status
            cases.append((status, item(), [failed], {'target': 'attempt_requires_exact_host_retry'}))
        stuck = row()
        stuck['submitted_result']['status'] = 'stuck'
        debug = row('debug', 'diagnosis')
        debug['submitted_result']['status'] = 'stuck'
        cases += [('stuck', item(), [stuck], c.action('declare-stuck', 'root')),
                  ('nonrecursive-debug', dict(item(), stuck_work={'root': {'status': 'diagnosis', 'diagnosis_assignment': 'debug'}}),
                   [stuck, debug], c.action('blocked', 'diagnosis_requires_operator'))]
        for label, saved, rows, expected in cases:
            with self.subTest(case=label):
                selected = c.select(deepcopy(saved), deepcopy(rows), frontier())
                self.assertEqual({key: selected[key] for key in expected}, expected)





class CapacityComponents(unittest.TestCase):
    def slot(self, claim, stage='implementation', commands=None):
        return {'claim_id': claim, 'stage': stage, 'profile': {'name': claim, 'home': '/unused/' + claim},
                'skills': [], 'launcher': '/private/' + claim + '.json'}

    def test_literal_capacity_and_launcher_limits_matrix(self):
        from factory_v1.model_access import SUBSCRIPTION_SCOPE
        provider, model, operation = SUBSCRIPTION_SCOPE
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / 'artifacts'
            artifacts.mkdir(mode=0o700)
            path = root / 'launcher.json'
            launcher = {'image': c.sandbox_policy.IMAGE, 'uid': 1000, 'subscription_socket': str(root / 'broker.sock'),
                        'artifacts_root': str(artifacts), 'limits': {'seconds': 10, 'max_calls': 1, 'memory_mb': 256,
                        'cpus': 0.1, 'pids': 16, 'scratch_mb': 16}, 'verification_commands': ['approved']}
            profile = {'name': 'fresh', 'home': str(root / 'unused'), 'role': 'implementation',
                       'provider': provider, 'model': model, 'operation': operation, 'reasoning': 'high'}
            seed = {'stage': 'implementation', 'profile': profile, 'repository': 'example/product', 'repository_id': 7,
                    'workspace': '/approved/workspace', 'issue': {'number': 1, 'id': 10, 'node_id': 'I_1', 'body_sha256': 'e' * 64},
                    'spec_commit': 'a' * 40, 'baseline': 'b' * 40, 'candidate': None,
                    'standards': [c.simplification.artifact('AGENTS.md', 'approved standards')], 'preceding': [], 'skills': [],
                    'capabilities': ['read_workspace', 'scratch', 'model', 'write_workspace'],
                    'result_contract': assignments.CONTRACT, 'claim_id': 'seed'}
            for defect, expected in (('valid', None), ('duplicate', 'invalid_dispatch'), ('role', 'profile_blocked'),
                                     ('limit', 'invalid_launcher'), ('consumed', 'execution_capacity_unavailable'),
                                     ('checks', 'execution_capacity_unavailable')):
                with self.subTest(defect=defect):
                    conf = config()
                    conf['implementation'] = deepcopy(seed)
                    slot = {'stage': 'implementation', 'profile': deepcopy(profile), 'skills': [], 'claim_id': 'fresh', 'launcher': str(path)}
                    conf['continuation']['slots'] = [slot]
                    loaded = deepcopy(launcher)
                    rows = []
                    if defect == 'duplicate':
                        conf['continuation']['slots'].append(deepcopy(slot))
                    if defect == 'role':
                        slot['profile']['role'] = 'debug'
                    if defect == 'limit':
                        loaded['limits']['max_calls'] = 0
                    if defect == 'checks':
                        loaded['verification_commands'] = ['wrong']
                    if defect == 'consumed':
                        used = row()
                        used['claim_id'] = 'fresh'
                        rows = [used]
                    path.write_text(json.dumps(loaded))
                    path.chmod(0o600)
                    with patch.object(assignments.role_skills, 'snapshots', return_value=[]), patch.object(dispatch, 'installed'):
                        with self.assertRaises(RepositoryError) if expected else nullcontext() as refused:
                            self.assertEqual(c.validate(conf, conf), conf)
                            self.assertEqual(c.slot_for(conf, rows, 'implementation', ['approved']), slot)
                        if expected:
                            self.assertEqual(refused.exception.code, expected)
            conf = config()
            slots = [self.slot('used'), self.slot('wrong', 'review-spec'), self.slot('fresh', 'review-spec')]
            conf['continuation']['slots'] = slots
            used = row()
            used['claim_id'] = 'used'
            with patch.object(dispatch, 'installed'), patch.object(c.sandbox_policy, 'load_config', side_effect=lambda path: {
                    'verification_commands': ['wrong'] if 'wrong' in path else ['approved']}):
                self.assertEqual(c.slot_for(conf, [used], 'review-spec', ['approved']), slots[2])
                with self.assertRaises(RepositoryError) as refused:
                    c.slot_for(conf, [used], 'implementation')
                self.assertEqual(refused.exception.code, 'execution_capacity_unavailable')

    def test_request_live_scope_binding_matrix(self):
        for label, first_operator, axes, expected in (
                ('exact', 'other', ['correct'], '/private/two'),
                ('ambiguous', '42', ['correct'], None),
                ('stale', '42', ['stale'], None)):
            with self.subTest(case=label):
                conf = config()
                conf['implementation'].update(repository='example/product', repository_id=7, baseline='a' * 40,
                    spec_commit='b' * 40, candidate=None, standards=['unchanged'], workspace='/approved/workspace')
                conf['continuation']['authorities']['review'] = ['/private/one', '/private/two']
                values = {'/private/one': {'operator_id': first_operator, 'axes': axes},
                          '/private/two': {'operator_id': '42', 'axes': axes}}
                github = Mock(base='https://api.github.com')
                github.request.return_value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'c' * 40}}
                with patch.object(c.tickets.gh, 'read_issue', return_value={'number': 2, 'id': 20, 'node_id': 'I_2', 'body': 'fresh body'}), \
                     patch.object(c.requirements, 'authority', return_value={'spec': {'commit': 'd' * 40}}), \
                     patch.object(c.merge, 'controller_paths'), patch.object(c.merge, 'private_file', side_effect=lambda p: p), \
                     patch.object(c.publication, 'read_json', side_effect=lambda p: values[p]):
                    bounded = c.request(conf, item(), 2, self.slot('fresh'), github)
                    with self.assertRaises(RepositoryError) if expected is None else nullcontext():
                        self.assertEqual(c.authority(Mock(), conf, 'review', lambda v: v.get('axes') == ['correct']), expected)
                self.assertEqual(bounded['baseline'], 'c' * 40)
                self.assertEqual(bounded['spec_commit'], 'd' * 40)
                self.assertEqual(bounded['issue']['body_sha256'], hashlib.sha256(b'fresh body').hexdigest())
                self.assertEqual(bounded['claim_id'], 'fresh')
                self.assertEqual(bounded['workspace'], '/approved/workspace')
                self.assertEqual(bounded['standards'], ['unchanged'])
                self.assertEqual(conf['implementation']['claim_id'], 'stale-seed')





class ExecutionComponents(unittest.TestCase):
    def test_execution_correction_and_direction_matrix(self):
        for stage, response in (('evaluate', None), ('direction', None), ('direction', {'response': 'actual operator input'})):
            with self.subTest(stage=stage, response=response):
                candidate = row('simple', 'simplify', simplification={'verification_commands': ['checks']})
                rows = [candidate, row('standards', 'review-standards'), row('spec', 'review-spec')]
                selected = c.action(stage, 'simple' if stage == 'evaluate' else 'root', event_id='event',
                                    axes={'standards_assignment': 'standards', 'spec_assignment': 'spec'})
                slot = {'profile': {'name': 'fix'}, 'skills': [], 'claim_id': 'fresh-fix', 'launcher': '/private/launcher'}
                with patch.object(c.review, 'execution_binding', side_effect=lambda a: {'id': a['assignment_id']}), \
                     patch.object(c, 'authority', return_value='/private/review'), \
                     patch.object(c.review, 'evaluate', return_value={'status': 'corrections', 'advance_allowed': False}), \
                     patch.object(c, 'slot_for', return_value=slot), patch.object(c.merge, 'controller_paths'), \
                     patch.object(c.review, 'prepare_corrections') as prepare, \
                     patch.object(c.attention, 'get_record', return_value={'decision_response': response}), \
                     patch.object(c.diagnosis, 'apply_direction') as apply:
                    result = c.execute(Mock(), ('p', 'm1', '42'), config(), item(), rows, selected, Mock())
                if stage == 'evaluate':
                    self.assertEqual(prepare.call_args.args[4], {**selected['axes'], 'profile': slot['profile'], 'skills': [], 'claim_id': 'fresh-fix'})
                    apply.assert_not_called()
                else:
                    prepare.assert_not_called()
                    if response is None:
                        self.assertEqual(result['result']['status'], 'waiting')
                        apply.assert_not_called()
                    else:
                        self.assertEqual(apply.call_args.args[4], {'stuck_assignment': 'root', 'event_id': 'event'})

        from factory_v1.tests.test_simplification import LocalStageEvidence
        for kind in ('correctness', 'behavior', 'out-of-scope'):
            with self.subTest(simplifier_finding=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                e = LocalStageEvidence(root)
                finding = {'kind': kind, 'path': 'hello.py', 'evidence': 'Bounded local finding', 'proposal': 'Correct or revise scope'}
                e.assignment['simplification_result'] = c.simplification.validate_result(e.assignment,
                    e.result(e.report(outcome='findings', findings=[finding])))
                for key in assignments.REQUEST_FIELDS:
                    e.assignment['handoff'].setdefault(key, None)
                e.assignment['handoff'].update(stage='simplify', repository='example/product', repository_id=7,
                    workspace=str(root / 'workspace'), standards=[], profile={'name': 'simple', 'home': str(root / 'profile')})
                e.assignment['handoff']['issue'].update(number=10, id=110, node_id='I_10')
                e.assignment['handoff']['simplification']['implementation_assignment'] = 'root'
                e.assignment['ticket_scope'].update(project='p', iteration='m1', repository='example/product', repository_id=7)
                e.seal_handoff()
                saved = item()
                saved['correlation'] = {'project_id': 'p', 'iteration_id': 'm1', 'stage': 'intake_registered',
                    'control_run_id': 'control-1', 'evidence_id': 'intake-1', 'worker_run_id': None}
                saved['origin'] = {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '122', 'scope_id': '121'}
                db = sqlite3.connect(root / 'state.sqlite')
                self.addCleanup(db.close)
                db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
                db.execute('CREATE TABLE notifications (project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project, iteration, event_id))')
                db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'm1', json.dumps(saved)))
                db.commit()
                rows = [row(), e.assignment]
                selected = c.select(saved, rows, frontier())
                self.assertEqual(selected['stage'], 'simplification-corrections' if kind == 'correctness' else 'simplification-direction')
                slot = {'profile': {'name': 'fresh-fix'}, 'skills': [], 'claim_id': 'fix', 'launcher': '/private/launcher'}
                prepared = []
                def admit(database, project, iteration, operator, request, github, dry_run=False):
                    contract = c.review.corrections_handoff(database, project, iteration, operator, request, github)
                    self.assertEqual(contract['source'], 'simplification')
                    self.assertEqual(contract['findings'], [finding])
                    self.assertEqual(contract['pins']['candidate_assignment'], e.assignment['assignment_id'])
                    self.assertEqual(contract['rerun_required'], ['implementation-checks', 'simplify', 'Standards', 'Spec'])
                    prepared.append(request)
                    return contract
                with patch.object(assignments, 'inspect', return_value=e.assignment), patch.object(assignments, 'rows', return_value=rows), \
                     patch.object(c, 'slot_for', return_value=slot), patch.object(c.merge, 'controller_paths'), \
                     patch.object(assignments, 'prepare', autospec=True, side_effect=admit):
                    with self.assertRaises(RepositoryError) as refused:
                        c.review.candidate(db, 'p', 'm1', '42', e.assignment['assignment_id'])
                    self.assertEqual(refused.exception.code, 'candidate_unverified')
                    result = c.execute(db, ('p', 'm1', '42'), config(), saved, rows, selected, Mock())
                    if kind == 'correctness':
                        self.assertEqual(len(prepared), 1)
                        self.assertEqual(prepared[0]['stage'], 'corrections')
                    else:
                        self.assertEqual(result['result']['status'], 'waiting')
                        records = c.attention.records(db, 'p', 'm1', saved)
                        self.assertEqual(len(records), 1)
                        self.assertEqual(records[0]['event']['kind'], 'decision')
                        self.assertEqual(records[0]['destination'], saved['origin'])
                        self.assertTrue(records[0]['event']['evidence_ids'])
                        self.assertEqual(prepared, [])
                        # Re-polling creates no duplicate decision or execution authority.
                        c.execute(db, ('p', 'm1', '42'), config(), saved, rows, selected, Mock())
                        self.assertEqual(len(c.attention.records(db, 'p', 'm1', saved)), 1)





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

    def test_persistent_pause_and_health_exception_matrix(self):
        for phase, allow, blocked in (('health', True, False), ('health', False, True),
                                      ('other-hold', True, True), ('pause', True, True)):
            with self.subTest(phase=phase, allow=allow):
                self.saved = item()
                self.saved.update(main_incident={'incident_id': 'incident'}, main_recoveries={'incident': {'status': 'registered'}})
                self.saved['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                    'blockers': [{'boundary': 'delivery', 'code': 'main_frozen'}]}
                if phase == 'other-hold':
                    self.saved['restart_reconciliation']['blockers'].append({'boundary': 'model', 'code': 'operation_uncertain'})
                save_iteration(self.db, self.saved)
                self.db.commit()
                _, marker = controls.paths(self.db, 'p', 'm1')
                if marker is not None:
                    marker.unlink(missing_ok=True)
                if phase == 'pause':
                    controls.request(self.db, 'p', 'm1', '42', 'pause', 'operator pause')
                with self.assertRaises(RepositoryError) if blocked else nullcontext():
                    restart.require_reconciled(self.db, 'p', 'm1', allow_recovery=allow)
                self.assertEqual(json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0]), self.saved)
                if phase == 'pause':
                    with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
                         patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(c, 'observe') as observe, \
                         patch.object(dispatch, 'run_assignment') as launch:
                        with self.assertRaises(RepositoryError) as refused:
                            dispatch.continuation_tick(self.service, Mock())
                    self.assertEqual(refused.exception.code, 'iteration_paused')
                    observe.assert_not_called()
                    launch.assert_not_called()

    def test_completion_readback_only_matrix(self):
        completed = {'report_id': 'report', 'event_id': 'event', 'report': {}}
        for already_complete in (False, True):
            with self.subTest(already_complete=already_complete):
                self.saved = item()
                if already_complete:
                    self.saved.update(status='waiting-for-operator', completion=completed)
                save_iteration(self.db, self.saved)
                self.db.commit()
                delivered = row('candidate', 'simplify')
                delivered['merge'] = {'status': 'delivered', 'close_allowed': True, 'failure': None}
                def complete(*args):
                    self.saved.update(status='waiting-for-operator', completion=completed)
                    save_iteration(self.db, self.saved)
                    self.db.commit()
                    return completed
                observed = {'items': [], 'eligible': [], 'foreign_active': [], 'active': []}
                with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
                     patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(restart, 'before_dispatch') as before, \
                     patch.object(assignments, 'rows', return_value=[delivered]), patch.object(c, 'observe', return_value=observed), \
                     patch.object(c.iteration, 'required_items', return_value=([{'number': 1}], [], {})), \
                     patch.object(c, 'merge_authority', return_value='/private/merge'), patch.object(c.iteration, 'complete', side_effect=complete) as completion, \
                     patch.object(dispatch, 'run_assignment') as launch, patch.object(c.iteration, 'next_iteration') as next_iteration:
                    for _ in range(2):
                        self.assertEqual(dispatch.continuation_tick(self.service, Mock())['status'], 'await_next_interview')
                if already_complete:
                    before.assert_not_called()
                    completion.assert_not_called()
                else:
                    completion.assert_called_once()
                    self.assertEqual(completion.call_args.args[4], {'merge_configs': ['/private/merge']})
                launch.assert_not_called()
                next_iteration.assert_not_called()
                self.assertEqual(json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0]), self.saved)

    def test_retirement_before_restart_matrix(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                self.saved = item()
                self.saved['reservation'] = {'issue_number': 9 if repair else 1}
                self.saved['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                    'blockers': [{'boundary': 'tracker', 'code': 'reservation_conflict'}]}
                record = {'status': 'repair', 'assignment': 'repair',
                          'config': {'incident_id': 'incident', 'evidence_id': 'e' * 64,
                                     'paths': ['app.py'], 'removable_assignments': []}, 'evidence': {'main': 'b' * 40}}
                if repair:
                    self.saved['restart_reconciliation'] = {'status': 'ready', 'observations': {}, 'blockers': []}
                    self.saved.update(main_incident={'incident_id': 'incident'}, main_recoveries={'incident': record})
                save_iteration(self.db, self.saved)
                self.db.commit()
                delivered = row('candidate', 'simplify', 9 if repair else 1, **({'recovery': recovery.contract(record)} if repair else {}))
                delivered['merge'] = {'status': 'delivered', 'close_allowed': True, 'failure': None}
                calls = []
                def clear(*args):
                    calls.append('verify-recovery')
                    self.saved.pop('main_incident')
                    save_iteration(self.db, self.saved)
                    self.db.commit()
                with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=config()), \
                     patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(assignments, 'rows', return_value=[delivered]), \
                     patch.object(c, 'merge_authority', return_value='/private/merge'), patch.object(restart, 'before_dispatch') as before, \
                     patch.object(recovery, 'verify', side_effect=clear) as verify, \
                     patch.object(c.tickets, 'advance_delivered', side_effect=lambda *a: calls.append('retire')) as advance, \
                     patch.object(restart, 'reconcile', side_effect=lambda *a: calls.append('restart')), patch.object(c, 'observe') as observe:
                    if repair:
                        self.assertTrue(c.retire_delivered(self.db, ('p', 'm1', '42'), config(), self.saved, [delivered], Mock()))
                        result = {'stage': 'retire-delivered'}
                    else:
                        result = dispatch.continuation_tick(self.service, Mock())
                self.assertEqual(result['stage'], 'retire-delivered')
                self.assertEqual(calls, ['verify-recovery', 'retire', 'restart'] if repair else ['retire', 'restart'])
                self.assertEqual(advance.call_args.args[3], '/private/merge')
                observe.assert_not_called()
                if repair:
                    self.assertEqual(before.call_args.kwargs, {'force': True, 'assignment_id': 'candidate', 'verify_recovery': True})
                else:
                    verify.assert_not_called()

    def test_fresh_request_and_selected_launch_matrix(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                slot = {'stage': 'implementation', 'profile': {'name': 'fresh', 'home': '/unused/fresh'},
                        'skills': [], 'claim_id': 'fresh', 'launcher': '/private/launcher'}
                conf = config()
                conf['implementation'].update(repository='example/product', repository_id=7, workspace='/approved/workspace',
                    baseline='a' * 40, spec_commit='b' * 40, candidate=None)
                github = Mock(base='https://api.github.com')
                github.request.return_value = {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'c' * 40}}
                prepared = row()
                prepared.pop('runtime')
                with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json', return_value=conf), \
                     patch.object(dispatch, 'validate', side_effect=lambda value, _: value), patch.object(assignments, 'rows', return_value=[prepared] if existing else []), \
                     patch.object(c, 'observe', return_value=frontier()), patch.object(restart, 'before_dispatch') as before, \
                     patch.object(c, 'slot_for', return_value=slot), patch.object(c, 'launcher_for', return_value='/private/launcher'), \
                     patch.object(c.tickets.gh, 'read_issue', return_value={'number': 2, 'id': 20, 'node_id': 'I_2', 'body': 'current requirement'}), \
                     patch.object(c.requirements, 'authority', return_value={'spec': {'commit': 'd' * 40}}), \
                     patch.object(c.tickets, 'reserve') as reserve, patch.object(c.assignments, 'prepare') as prepare, \
                     patch.object(dispatch, 'run_assignment') as launch, patch.object(assignments, 'inspect', return_value=row()):
                    result = dispatch.continuation_tick(self.service, github)
                self.assertEqual(result['stage'], 'launch' if existing else 'implementation')
                if existing:
                    launch.assert_called_once_with(self.service, 'root', '/private/launcher', stop_event=None)
                    self.assertEqual(before.call_args.kwargs['assignment_id'], 'root')
                    prepare.assert_not_called()
                    reserve.assert_not_called()
                else:
                    bounded = prepare.call_args.args[4]
                    self.assertEqual(bounded['claim_id'], 'fresh')
                    self.assertEqual(bounded['issue']['number'], 2)
                    self.assertEqual(bounded['baseline'], 'c' * 40)
                    self.assertEqual(bounded['spec_commit'], 'd' * 40)
                    self.assertEqual(before.call_args.kwargs['request'], bounded)
                    self.assertEqual(reserve.call_args.args[3], 2)
                    launch.assert_not_called()



    def test_registered_repair_real_scope_and_health_matrix(self):
        pin = {'number': 9, 'id': 90, 'node_id': 'I_9', 'body_sha256': 'a' * 64}
        record = {'status': 'registered', 'assignment': None, 'parked': [], 'removable': [],
                  'config': {'incident_id': 'incident', 'evidence_id': 'e' * 64, 'issue': pin,
                             'paths': ['app.py'], 'removable_assignments': []}, 'evidence': {'main': 'b' * 40}}
        report = {'status': 'ready', 'blockers': [], 'observations': {
            'delivery': {'evidence': {'main': 'b' * 40}}, 'final-recovery': recovery.contract(record)}}
        bounded = {'stage': 'repair', 'issue': pin, 'baseline': 'b' * 40, 'candidate': None,
                   'preceding': recovery.preceding(record), 'claim_id': 'fresh'}
        self.service['delivery_config'] = '/private/delivery'
        for registered, drift in ((False, None), (True, {}), (True, {'baseline': 'c' * 40}),
                                   (True, {'issue': {**pin, 'number': 1}}), (True, {'stage': 'implementation'})):
            with self.subTest(registered=registered, drift=drift):
                self.saved = item()
                if registered:
                    self.saved.update(main_incident={'incident_id': 'incident'}, main_recoveries={'incident': deepcopy(record)}, restart_reconciliation=deepcopy(report))
                save_iteration(self.db, self.saved)
                self.db.commit()
                with patch.object(service.delivery, 'load_config', return_value={}), \
                     patch.object(service.delivery, 'sample', return_value={'evidence': {'status': 'unknown'}}), \
                     patch.object(dispatch.merge, 'controller_paths'), patch.object(service, 'inspect_readiness', return_value={'iteration_runnable': True}), patch.object(service, 'dispatch') as invoke:
                    observed = service.cycle(self.service, Mock())
                if registered:
                    invoke.assert_called_once()
                    with patch.object(restart, 'reconcile', return_value=report):
                        with self.assertRaises(RepositoryError) if drift else nullcontext():
                            restart.before_dispatch(self.db, 'p', 'm1', '42', Mock(), force=True, request={**bounded, **drift})
                else:
                    self.assertEqual(observed['blockers'], ['delivery_not_healthy'])
                    invoke.assert_not_called()
