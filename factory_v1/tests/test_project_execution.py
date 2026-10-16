"""One direct configuration/registration invariant; no precursor lifecycle execution."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, controls, project_execution as execution, readiness_gate, service
from factory_v1.conversation_enrollment import DraftStore
from factory_v1.tests import test_readiness


class ProjectExecutionTests(unittest.TestCase):
    def test_project_configuration_and_registration_invariants(self):
        # Fresh preparation has no delivery anchor, standards or running unit.
        self.assertTrue(callable(getattr(execution.ProjectExecution, 'prepare_planning', None)),
                        'Missing public preplanning preparation')
        cases = ('ready', 'installation', 'no-deployment', 'policy', 'capacity', 'health', 'source-drift', 'replay-drift', 'consumed-home', 'active', 'preplanning', 'managed', 'unpublished', 'start-denied')
        for case in cases:
            with self.subTest(case=case):
                # Existing fixture seeds records and mocks external reads only;
                # it performs no preparation/worker/controller chain.
                fixture: Any = test_readiness.ReadinessTests()
                fixture.setUp()
                try:
                    root = fixture.root
                    for name in ('drafts', 'setup', 'tracker', 'execution', 'roles', 'caps', 'workspaces', 'state'):
                        (root / name).mkdir(mode=0o700)
                    workspace = root / 'workspaces/product'
                    workspace.mkdir(mode=0o700)
                    (workspace / 'AGENTS.md').write_text('Meaningful focused unit checks; human-controlled delivery.')
                    state = root / 'state/controller.sqlite'
                    db = sqlite3.connect(state)
                    fixture.db.backup(db)
                    fixture.db.close()
                    fixture.db = db
                    state.chmod(0o600)
                    fixture.item['planning'] = {'assignment_id': 'native-plan', 'status': 'pending'}
                    db.execute('UPDATE iterations SET payload=?', (json.dumps(fixture.item),))
                    db.execute('UPDATE model_grants SET remaining=7, expires=20000000000')
                    db.commit()
                    fixture.service = dict(fixture.service, version=1, state=str(state), interval=30,
                                           dispatch_config=None, readiness_config=None, github_config=None)
                    path = root / 'state/service.json'
                    service.atomic(path, fixture.service)
                    draft_id = 'a' * 64
                    store = DraftStore(root / 'drafts')
                    attached = {'status': 'ready', 'draft_revision': 3, 'repository': 'owner/product',
                        'repository_id': 7, 'project': 'p', 'iteration': 'i', 'workspace': str(workspace),
                        'commit': 'a' * 40, 'binding': {'state': str(state)}, 'transfer': {'assignment_id': 'native-plan'}}
                    draft = {'draft_id': draft_id, 'revision': 3, 'operator_id': 'operator', 'sufficient': True,
                        'pending_questions': [], 'transfer': attached['transfer'], 'product': {'acceptance': 'Unit checks and deployed search.'}}
                    store.save(draft)
                    setup = SimpleNamespace(root=root / 'setup', workspaces=root / 'workspaces',
                        config={'state': str(state)}, enrollment=SimpleNamespace(store=store), read=Mock(return_value=attached))
                    tracker_cap = root / 'caps/tracker.json'
                    service.atomic(tracker_cap, {'operator_id': 'operator', 'repository': 'owner/product'})
                    tracked = {'status': 'ready', 'draft_revision': 3, 'repository_id': 7,
                        'binding': attached['binding'], 'tracker': fixture.config['board'],
                        'identities': dict(fixture.board, milestone={'id': 90, 'number': 1, 'title': 'i'})}
                    tracker = SimpleNamespace(root=root / 'tracker', read=Mock(return_value=tracked),
                        tracker_config=Mock(return_value=fixture.config['board']), config={'capability_config': str(tracker_cap)})
                    host = {'operator_id': 'operator', 'repository': 'owner/product', 'repository_id': 7,
                        'draft_revision': 3, 'reference': 'reviewed-host-fixture', 'service_config': str(path),
                        'role_root': str(root / 'roles'), 'planning_skills': fixture.config['planning_skills'],
                        'ticket_skill': fixture.config['ticket_skill'], 'synthesis_config': fixture.config['synthesis_config'],
                        'planning_config': str(root / 'caps/native.json'),
                        'authorities': {k: [] for k in ('review', 'diagnosis', 'publication', 'merge')},
                        'protection': fixture.config['protection'],
                        'policy': {'acceptance': draft['product']['acceptance'], 'verification_commands': ['python -m unittest'],
                            'merge': 'human-approved', 'production': 'human-approved', 'deployment': True},
                        'templates': [{'stage': s['stage'], 'count': 1, 'skills': s['skills'], 'launcher': s['launcher'],
                            'profile': {k: s['profile'][k] for k in ('provider', 'model', 'operation', 'reasoning')}}
                            for s in fixture.config['slots']]}
                    if case == 'no-deployment': host['policy']['deployment'] = False
                    if case == 'policy': host['policy']['acceptance'] = 'Different agreement'
                    if case == 'health': fixture.health['acceptance']['ok'] = False
                    if case == 'capacity':
                        db.execute('UPDATE model_grants SET remaining=0'); db.commit()
                    if case == 'source-drift':
                        source = Path(host['templates'][0]['skills'][0]['path']); source.write_text('unreviewed drift')
                    managed = case in ('preplanning', 'managed', 'unpublished', 'start-denied')
                    extra = {}
                    inputs = {'handoff': {'commit': 'b' * 40, 'issue': {'number': 5}}}
                    issue = {'number': 1, 'id': 10, 'node_id': 'I_1', 'body': 'Actual read-only fixture anchor'}
                    if managed:
                        native = root / 'caps/native.json'
                        service.atomic(native, {})
                        template = root / 'caps/service-template.json'
                        service.atomic(template, {k: v for k, v in fixture.service.items() if k not in
                            {'state', 'operator_id', 'project', 'iteration'}})
                        units = root / 'units'; units.mkdir(mode=0o700)
                        installation = root / 'caps/installation.json'
                        service.atomic(installation, {'operator_id': 'operator', 'reference': 'reviewed-platform',
                            'state_root': str(root / 'state'), 'workspace_root': str(root / 'workspaces'),
                            'service_template': str(template), 'unit_directory': str(units),
                            'actions': ['install'] if case == 'start-denied' else ['install', 'start']})
                        extra['installation_config'] = str(installation)
                        host = {k: v for k, v in host.items() if k in execution.PLATFORM_FIELDS}
                        host['policy'].pop('acceptance')
                        path.unlink()
                        if case in ('preplanning', 'unpublished'):
                            (workspace / 'AGENTS.md').unlink()
                        else:
                            ticket = {'key': 'first', 'blockers': [], 'references': [],
                                'desired_behavior': 'Actual product change', 'acceptance_criteria': ['Focused check'],
                                'triage_label': 'ready-for-agent'}
                            fixture.item['planning']['status'] = 'complete'
                            fixture.item['ticket_work'] = {'assignment_id': 'tickets-native', 'status': 'published',
                                'publication_complete': True, 'tickets': [ticket], 'inputs': inputs,
                                'published': {'first': {'number': 2, 'id': 20, 'node_id': 'I_2'}}}
                            issue = {'number': 2, 'id': 20, 'node_id': 'I_2', 'state': 'open',
                                'body': execution.tickets.body(fixture.item, ticket, {'first': 2})}
                            db.execute('UPDATE iterations SET payload=?', (json.dumps(fixture.item),)); db.commit()
                    cap = root / 'caps/execution.json'
                    service.atomic(cap, host)
                    component = execution.ProjectExecution(setup, tracker, {'root': str(root / 'execution'), 'capability_config': str(cap), **extra})
                    paused = {'project': 'p', 'iteration': 'i', 'operator_id': 'operator', 'action': 'pause',
                        'state': 'completed', 'reference': 'intentional-pause', 'request_id': 'pause-1', 'history': [],
                        'cleanup': {}, 'checkpoint': {'iteration': {'project_id': 'p', 'iteration_id': 'i'}}}
                    control_root, control_path = controls.paths(db, 'p', 'i')
                    assert control_root is not None and control_path is not None
                    control_root.mkdir(mode=0o700)
                    if case != 'active':
                        controls.write(db, paused)
                    pause_bytes = control_path.read_bytes() if control_path.exists() else None
                    consumed = root / 'roles' / ('forge-' + assignments.digest([draft_id, 'implementation', 0])[:32])
                    if case == 'consumed-home': consumed.mkdir(mode=0o700)
                    original_grants = db.execute('SELECT * FROM model_grants').fetchall()
                    live = {'ActiveState': 'active', 'SubState': 'running', 'MainPID': '77'}
                    if case == 'installation': live['ActiveState'] = 'inactive'
                    observed = [dict(live, ActiveState='inactive', MainPID='0'), None, live] if managed else None
                    with patch.object(service, 'supervisor', side_effect=observed, return_value=live) as supervisor, \
                         patch.object(service, 'install') as install, \
                         patch.object(execution.tickets, 'pinned_inputs', return_value=inputs), \
                         patch.object(service, 'github_client', return_value=fixture.github), \
                         patch.object(execution.readiness, 'HostReaders', return_value=fixture.readers), \
                         patch('factory_v1.project_tracker.TrackerHost', return_value=SimpleNamespace(github=fixture.github)), \
                         patch.object(execution.tickets.gh, 'resolve_milestone', return_value=tracked['identities']['milestone']), \
                         patch.object(execution.tickets.gh, 'read_issue', return_value=issue):
                        if case == 'preplanning':
                            result = component.prepare_planning(draft_id, 3)
                            self.assertEqual(result['status'], 'planning_prepared', result.get('blocker'))
                            self.assertIsNone(readiness_gate.binding(db, 'p', 'i'))
                            self.assertIsNone(service.load(str(path))['dispatch_config'])
                            self.assertIsNone(service.load(str(path))['delivery_config'])
                            supervisor.assert_not_called(); install.assert_not_called()
                        elif case in ('managed', 'unpublished', 'start-denied'):
                            result = component.register(draft_id, 3) if case == 'managed' else component.prepare(draft_id, 3)
                            expected = {'managed': 'registered', 'unpublished': 'blocked', 'start-denied': 'blocked'}[case]
                            self.assertEqual(result['status'], expected, result.get('blocker'))
                            if case == 'managed':
                                dispatch = execution.readiness.load_config(result['service']['dispatch_config'])
                                self.assertEqual(dispatch['implementation']['issue']['number'], 2)
                                self.assertEqual(dispatch['implementation']['spec_commit'], 'b' * 40)
                                install.assert_called_once()
                                self.assertEqual(supervisor.call_args_list[1].args[1], 'start')
                                self.assertEqual(supervisor.call_args_list[1].kwargs['unit'], service.instance(result['service']))
                            else:
                                self.assertEqual(result['blocker']['code'], 'execution_publication_required' if case == 'unpublished'
                                                 else 'execution_lifecycle_required')
                                self.assertFalse(any(c.args[1] == 'start' for c in supervisor.call_args_list))
                        else:
                            result = component.prepare(draft_id, 3) if case == 'active' else component.register(draft_id, 3)
                        if managed:
                            pass
                        elif case == 'active':
                            self.assertEqual(result['status'], 'prepared', result.get('blocker'))
                            tracker.tracker_config.assert_called_once_with(draft_id)
                            self.assertIsNone(service.load(str(path))['dispatch_config'])
                        elif case in ('ready', 'replay-drift', 'consumed-home'):
                            self.assertEqual(result['status'], 'registered', result.get('blocker'))
                            selected = execution.readiness.load_config(result['service']['dispatch_config'])
                            slots = selected['continuation']['slots']
                            self.assertEqual({s['stage'] for s in slots}, set(assignments.ROLES))
                            self.assertEqual(len({s['profile']['home'] for s in slots}), 7)
                            if case == 'consumed-home':
                                self.assertEqual({Path(s['profile']['home']) for s in slots if Path(s['profile']['home']).exists()}, {consumed})
                                available = result['readiness']['slot_capacity']['available']
                                self.assertNotIn('implementation', {s['stage'] for s in available})
                            else:
                                self.assertFalse(any(Path(s['profile']['home']).exists() for s in slots))
                            self.assertEqual(selected['continuation']['authorities'], host['authorities'])
                            for slot in slots:
                                launcher = execution.sandbox_policy.load_config(slot['launcher'])
                                self.assertEqual(launcher['verification_commands'], ['python -m unittest'])
                                self.assertEqual(Path(slot['launcher']).stat().st_mode & 0o777, 0o600)
                            before = {p: p.read_bytes() for p in (root / 'execution' / draft_id).glob('*.json')}
                            if case == 'replay-drift':
                                bad = Path(slots[0]['launcher']); value = json.loads(bad.read_text())
                                value['limits']['max_calls'] += 1; service.atomic(bad, value)
                                self.assertEqual(component.resume(draft_id, 3)['blocker']['code'], 'execution_inputs_changed')
                            else:
                                self.assertEqual(component.resume(draft_id, 3)['status'], 'registered')
                                self.assertEqual(before, {p: p.read_bytes() for p in before})
                                bound = readiness_gate.binding(db, 'p', 'i')
                                assert bound is not None
                                self.assertIsNotNone(bound[1])
                        else:
                            self.assertEqual(result['status'], 'blocked', result)
                            self.assertIsNone(service.load(str(path))['dispatch_config'])
                            if case == 'no-deployment': self.assertEqual(result['blocker']['code'], 'nondeployment_unsupported')
                            if case == 'health': self.assertEqual(result['readiness']['checks']['delivery']['state'], 'blocked')
                            if case == 'source-drift': self.assertEqual(result['blocker']['code'], 'skill_blocked')
                        self.assertEqual(control_path.read_bytes() if control_path.exists() else None, pause_bytes)
                        self.assertEqual(db.execute('SELECT * FROM model_grants').fetchall(), original_grants)
                        self.assertTrue(managed or all(call.args[1] == 'status' for call in supervisor.call_args_list))
                        self.assertNotIn('bearer', json.dumps(component.read(draft_id)))
                        self.assertEqual(store.read(draft_id), draft)
                    db.close()
                finally:
                    fixture.doCleanups()
