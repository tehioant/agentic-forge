"""Focused collector and admission checks: explicit records, independent read seams."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, delivery, model_access, planning, readiness, role_skills, sandbox_policy, skills, tickets
from factory_v1 import readiness_capabilities as capabilities, repositories, spending
from factory_v1 import controls, readiness_gate, service
import io
from email.message import Message


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class SupervisedFixture:
    def bind_service(self):
        self.service.update(version=1, state=str(self.root / 'state.sqlite'), interval=30,
                            dispatch_config=None, github_config=None,
                            readiness_config=self.file('readiness.json', self.config))
        self.service_path = self.file('service.json', self.service)
        readiness_gate.register(self.db, self.service_path, self.service)
        self.stack.enter_context(patch.object(readiness, 'HostReaders', return_value=self.readers))
        self.stack.enter_context(patch.object(service, 'github_client', return_value=self.github))
        self.stack.enter_context(patch.object(readiness.time, 'time', return_value=100))
        # Explicit preselected policy fixture; selection itself is independently tested.
        self.db.execute('UPDATE supervised_readiness SET policy=?', (readiness_gate.policy(self.service, self.config, self.db),))
        self.db.commit()

    def admit(self, **kwargs):
        with controls.admission(self.db, 'p', 'i', **kwargs):
            return 'admitted'


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.db.close)
        os.chmod(self.root / 'state.sqlite', 0o600)
        self.db.executescript('''CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT);
          CREATE TABLE model_grants(grant_id TEXT, project TEXT, iteration TEXT, provider TEXT, model TEXT,
          operation TEXT, kind TEXT, remaining INTEGER, expires INTEGER);
          CREATE TABLE model_operations(project TEXT, iteration TEXT, status TEXT);''')
        self.origin = dict(platform='discord', chat_id='11', thread_id='11', parent_chat_id='12', scope_id='13')
        self.item: dict[str, Any] = dict(project_id='p', iteration_id='i', repository='owner/product', status='active',
                         execution_allowed=False, origin=self.origin,
                         approval={'operator_id': 'operator', 'reference': 'explicit-fixture-approval'},
                         repository_onboarding={'api_base': 'https://api.github.com', 'metadata': {'id': 7}})
        self.save()
        self.db.execute('INSERT INTO model_grants VALUES (?,?,?,?,?,?,?,?,?)',
                        ('fixture-grant', 'p', 'i', *model_access.SUBSCRIPTION_SCOPE, 'allowance', 10, 200))
        self.db.commit()
        self.repository = dict(id=7, full_name='owner/product', name='product', owner={'login': 'owner'}, private=True, default_branch='main')
        self.board = {'project_id': 'BOARD', 'status': {'id': 'FIELD', 'options': {'Todo': 'T', 'Doing': 'A', 'Done': 'D'}}}
        self.checks = [{'context': 'unit', 'app_id': 4}]
        self.classic = {'required_status_checks': {'strict': True, 'checks': self.checks},
                        'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False},
                        'allow_deletions': {'enabled': False}}
        executable = self.root / 'host-adapter'
        executable.write_bytes(b'explicit nonexecuted fixture adapter\n'); executable.chmod(0o700)
        self.delivery_config = dict(operator_id='operator', repository='owner/product', repository_id=7, issue=1,
             api_base='https://api.github.com', environment='test', timeout=1, required_checks=self.checks,
             required_workflows=[{'id': 5, 'event': 'push'}], contract={'reference': 'agreed', 'revision': 'a'*40,
             'health_url': 'https://health.invalid/status', 'acceptance': {'ok': True}})
        self.service = dict(operator_id='operator', project='p', iteration='i', api_base='https://api.github.com',
            delivery_config=self.file('delivery.json', self.delivery_config),
            transport_config=self.file('transport.json', {'command': [str(executable)], 'approved_destinations': [self.origin]}))
        self.socket = str(self.root / 'broker.sock')
        artifacts = self.root / 'artifacts'; artifacts.mkdir(mode=0o700)
        launcher = self.file('launcher.json', {'image': sandbox_policy.IMAGE, 'uid': 1000,
            'subscription_socket': self.socket, 'artifacts_root': str(artifacts),
            'limits': dict(seconds=60, max_calls=2, memory_mb=256, cpus=1, pids=16, scratch_mb=16)})
        # Independent exact-byte fixture source catalog, not installed-runtime qualification.
        selected = {}
        for name in role_skills.SELECTED:
            raw = ('fixture source: ' + name).encode()
            path = self.root / 'engineering' / name
            if '/' not in name:
                path = path / 'SKILL.md'
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            selected[name] = (str(path), sha(raw))
        self.stack.enter_context(patch.object(role_skills, 'SELECTED', selected))
        self.stack.enter_context(patch.object(skills, 'INSTALLED_ROOT', self.root / 'planning'))
        planning_pins, planning_descriptors = {}, []
        for name in ('grill-me', 'grilling', 'to-spec'):
            raw = ('fixture source: ' + name).encode(); path = skills.INSTALLED_ROOT / name / 'SKILL.md'
            path.parent.mkdir(parents=True); path.write_bytes(raw); planning_pins[name] = sha(raw)
            planning_descriptors.append(dict(name=name, path=str(path), sha256=sha(raw)))
        self.stack.enter_context(patch.object(skills, 'INSTALLED_PINS', planning_pins))
        raw = b'explicit ticket fixture'; path = skills.INSTALLED_ROOT / 'to-tickets/SKILL.md'
        path.parent.mkdir(); path.write_bytes(raw)
        self.stack.enter_context(patch.object(tickets, 'PIN', sha(raw)))
        slots = []
        for stage, role in assignments.ROLES.items():
            descriptors = [dict(name=n, source=selected[n][0], path=selected[n][0], sha256=selected[n][1],
                                dependencies=role_skills.DEPENDENCIES.get(n, [])) for n in role_skills.closure(stage)]
            profile = dict(name=stage, home=str(self.root / ('fresh-' + stage)), role=role,
                           provider=model_access.SUBSCRIPTION_SCOPE[0], model=model_access.SUBSCRIPTION_SCOPE[1],
                           operation=model_access.SUBSCRIPTION_SCOPE[2], reasoning='low')
            slots.append(dict(stage=stage, profile=profile, skills=descriptors, claim_id=stage, launcher=launcher))
        self.config: dict[str, Any] = dict(operator_id='operator', project='p', iteration='i', reference='explicit-config', slots=slots,
             planning_skills=planning_descriptors, ticket_skill=dict(name='to-tickets', path=str(path), sha256=sha(raw)),
             board=dict(project_id='BOARD', status_field='Status', statuses=dict(ready='Todo', active='Doing', done='Done'), triage_label='triage'),
             board_pin=deepcopy(self.board), protection=dict(base='main', required_checks=self.checks,
             protection_sha256=assignments.digest({'rules': [], 'protection': self.classic})),
             synthesis_config=self.file('synthesis.json', dict(operator_id='operator', reference='fixture', execution_reference='fixture-execution',
                 argv=[str(executable)], sha256=sha(executable.read_bytes()), timeout=1)))
        self.github = SimpleNamespace(base='https://api.github.com', request=Mock(side_effect=self.read))
        self.readers: Any = Mock(spec=readiness.HostReaders)
        self.write_authority = {'scope': {'project': 'p', 'iteration': 'i', 'operator_id': 'operator',
            'repository': 'owner/product', 'repository_id': 7, 'api_base': 'https://api.github.com',
            'board': deepcopy(self.board)}, 'operations': list(readiness.WRITES),
            'route': 'independent-host-fixture-route'}
        self.execution_authority = {'scope': deepcopy(self.write_authority['scope']),
            'stages': ['grill-me', 'grilling', 'to-spec', 'to-tickets', *assignments.ROLES],
            'executor': 'independent-host-fixture-executor', 'verifier': 'independent-host-fixture-verifier'}
        def write_authority(scope, operations):
            if scope != self.write_authority['scope'] or set(operations) != set(self.write_authority['operations']):
                raise ValueError('insufficient independent host authority')
            return assignments.digest(self.write_authority)
        def execution_authority(scope, stages):
            if scope != self.execution_authority['scope'] or set(stages) != set(self.execution_authority['stages']):
                raise ValueError('unsupported independent executor/verifier')
            return assignments.digest(self.execution_authority)
        self.readers.github_authority.side_effect = write_authority
        self.readers.execution_support.side_effect = execution_authority
        real_lstat = os.lstat
        self.stack.enter_context(patch.object(model_access.os, 'lstat', side_effect=lambda p, *a, **kw:
             SimpleNamespace(st_mode=stat.S_IFSOCK, st_uid=os.getuid()) if str(p) == self.socket else real_lstat(p, *a, **kw)))
        self.stack.enter_context(patch.object(sandbox_policy.shutil, 'which', return_value='/fixture/docker'))
        self.stack.enter_context(patch.object(sandbox_policy.Docker, 'call', return_value=json.dumps([{
            'Id': 'fixture-image', 'RepoDigests': [sandbox_policy.IMAGE], 'Config': {'Entrypoint': sandbox_policy.IMAGE_ENTRYPOINT,
            'WorkingDir': '/opt/hermes', 'Volumes': {'/opt/data': {}}}}]).encode()))
        self.health = delivery._expected_health(self.delivery_config, 'a'*40, 30)
        self.stack.enter_context(patch.object(delivery, 'http_json', side_effect=lambda *_: deepcopy(self.health)))
        # If any collector path attempts an effect, the test fails immediately.
        self.effects = []
        invoke_code = capabilities.requirement_execution.invoke.__code__
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.socket.connect', 'socket.socket.bind',
                       'factory_v1.messaging.HermesTransport.send', 'factory_v1.model_access.SubscriptionTransport.call',
                       'factory_v1.spending.add_grant', 'factory_v1.spending._prepare',
                       'factory_v1.requirement_execution.invoke'):
            self.effects.append(self.stack.enter_context(patch(target, side_effect=AssertionError('forbidden effect'))))
        self.effects[-1].__code__ = invoke_code  # Preserve inspected source identity, never execute it.

    def file(self, name, value):
        path = self.root / name; path.write_text(json.dumps(value)); path.chmod(0o600)
        return str(path)

    def save(self):
        self.db.execute('DELETE FROM iterations')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(self.item)))
        self.db.commit()

    def read(self, path, payload=None, method=None):
        if path == '/graphql':
            assert isinstance(payload, dict)
            self.assertTrue(payload['query'].lstrip().startswith('query '))
            if 'LinkedProjects' in payload['query']:
                return {'data': {'repository': {'projectsV2': {'nodes': [{'id': 'BOARD', 'closed': False}], 'pageInfo': {'hasNextPage': False}}}}}
            return {'data': {'node': {'id': 'BOARD', 'fields': {'nodes': [{'id': self.board['status']['id'], 'name': 'Status',
                'options': [{'name': n, 'id': i} for n, i in self.board['status']['options'].items()]}], 'pageInfo': {'hasNextPage': False}}}}}
        self.assertIsNone(payload); self.assertIn(method, (None, 'GET'))
        suffix = path.removeprefix('/repos/owner/product')
        if suffix == '': return deepcopy(self.repository)
        if suffix == '/rules/branches/main': return []
        if suffix == '/branches/main/protection': return deepcopy(self.classic)
        if suffix == '/git/ref/heads/main': return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'a'*40}}
        if suffix.startswith('/commits/'):
            return {'total_count': 1, 'check_runs': [dict(id=20, name='unit', app={'id': 4}, head_sha='a'*40, status='completed', conclusion='success')]}
        if suffix.startswith('/actions/'):
            return {'total_count': 1, 'workflow_runs': [dict(id=21, workflow_id=5, head_sha='a'*40, head_branch='main', event='push',
                status='completed', conclusion='success', repository={'id': 7, 'full_name': 'owner/product'})]}
        if suffix.startswith('/deployments?'): return [dict(id=30, sha='a'*40, ref='main', environment='test')]
        if suffix.startswith('/deployments/30/'): return [{'id': 31, 'state': 'success'}]
        raise AssertionError('unexpected read')

    def collect(self, **kwargs):
        before = '\n'.join(self.db.iterdump())
        result = readiness.collect(self.db, self.service, self.config, self.github, readers=self.readers, now=100, **kwargs)
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        for effect in self.effects:
            effect.assert_not_called()
        self.assertFalse(result['dispatch_allowed']); self.assertFalse(result['iteration_runnable'])
        return result

    def test_ready_mocked_inventory_is_not_execution_authority(self):
        result = self.collect(running=False)
        self.assertTrue(result['capability_ready'], result['checks'])
        self.assertTrue(result['iteration_eligible']); self.assertFalse(result['service_running'])
        self.readers.github_authority.assert_called_once_with({
            'project': 'p', 'iteration': 'i', 'operator_id': 'operator', 'repository': 'owner/product',
            'repository_id': 7, 'api_base': 'https://api.github.com', 'board': self.config['board_pin']}, readiness.WRITES)
        self.assertEqual(set(self.readers.execution_support.call_args.args[1]),
                         {'grill-me', 'grilling', 'to-spec', 'to-tickets', *assignments.ROLES})

    def test_default_unsupported_and_unverified_booleans_are_denied(self):
        self.readers = None
        result = self.collect(running=True)
        self.assertEqual(result['checks']['github_authority']['state'], 'blocked')
        self.assertEqual(result['checks']['execution']['state'], 'blocked')
        self.assertEqual(result['checks']['github_authority']['code'], 'github_route_unsupported')
        self.assertEqual(result['checks']['execution']['code'], 'native_planning_unavailable')
        self.readers = Mock(spec=readiness.HostReaders, github_authority=Mock(return_value=True), execution_support=Mock(return_value=True))
        self.assertFalse(self.collect()['capability_ready'])

    def test_private_identity_board_and_access_refusals(self):
        self.write_authority['operations'].remove('projects')
        self.assertEqual(self.collect()['checks']['github_authority']['state'], 'blocked')
        self.write_authority['operations'].append('projects')
        for key, value in [('private', False), ('id', 8)]:
            with self.subTest(key=key):
                old = self.repository[key]; self.repository[key] = value
                self.assertEqual(self.collect()['checks']['repository']['state'], 'blocked'); self.repository[key] = old
        self.board['status']['id'] = 'REPLACED'
        self.assertEqual(self.collect()['checks']['board']['state'], 'blocked')
        self.board['status']['id'] = 'FIELD'
        self.board['status']['options']['Todo'] = 'REPLACED'
        self.assertEqual(self.collect()['checks']['board']['state'], 'blocked')
        self.github.request.side_effect = RuntimeError('sensitive arbitrary provider error')
        result = self.collect()
        self.assertEqual(result['checks']['repository']['state'], 'blocked')
        self.assertNotIn('sensitive', json.dumps(result))

    def test_pin_dependency_profile_and_slot_refusals(self):
        baseline = deepcopy(self.config)
        for mutation in ('bytes', 'review-bytes', 'dependency', 'provider', 'collision', 'missing-stage'):
            with self.subTest(mutation=mutation):
                self.config = deepcopy(baseline); slot = self.config['slots'][0]
                if mutation == 'bytes': Path(slot['skills'][0]['path']).write_text('drift')
                if mutation == 'review-bytes':
                    Path(role_skills.SELECTED['code-review'][0]).write_text('drift')
                if mutation == 'dependency': slot['skills'][0]['dependencies'] = ['invented']
                if mutation == 'provider': slot['profile']['provider'] = 'paid-fallback'
                if mutation == 'collision':
                    self.config['slots'][1]['profile']['name'] = slot['profile']['name']
                if mutation == 'missing-stage': self.config['slots'].pop()
                self.assertEqual(self.collect()['checks']['stages']['state'], 'blocked')
                if mutation == 'bytes': Path(slot['skills'][0]['path']).write_text('fixture source: implement')
                if mutation == 'review-bytes':
                    Path(role_skills.SELECTED['code-review'][0]).write_text('fixture source: code-review')

    def test_health_notification_scope_and_spending_refusals(self):
        self.health['sha'] = 'b'*40
        self.assertEqual(self.collect()['checks']['delivery']['state'], 'blocked')
        self.health['sha'] = 'a'*40
        self.delivery_config['repository_id'] = 8
        self.file('delivery.json', self.delivery_config)
        self.assertEqual(self.collect()['checks']['delivery']['state'], 'blocked')
        self.delivery_config['repository_id'] = 7
        self.file('delivery.json', self.delivery_config)
        self.item['origin']['thread_id'] = '999'; self.save()
        self.assertEqual(self.collect()['checks']['notification']['state'], 'blocked')
        self.config['iteration'] = 'sibling'
        self.assertEqual(set(self.collect()['checks']), {'scope'})
        self.config['iteration'] = 'i'
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        self.assertEqual(self.collect()['checks']['spending']['state'], 'blocked')

    def test_config_identity_changes_and_paused_controls(self):
        first = self.collect()['input_identity']
        self.config['reference'] = 'changed explicit config'
        self.assertNotEqual(first, self.collect()['input_identity'])
        self.item['status'] = 'paused'; self.save()
        result = self.collect(running=True)
        self.assertTrue(result['service_running']); self.assertFalse(result['iteration_eligible'])
        self.assertEqual(result['checks']['controls']['state'], 'blocked')
        self.assertTrue(result['capability_ready'], result['checks'])

    def test_paused_capacity_preserves_call_admission_and_uncertainty(self):
        before = self.collect()['input_identity']
        self.item.update(status='paused', restart_observation={'sample': 'benign'})
        self.save()
        self.assertEqual(before, self.collect()['input_identity'])
        scope = ('p', 'i', *model_access.SUBSCRIPTION_SCOPE)
        self.assertFalse(spending.capacity_preview(self.db, scope, now=100)['reserved'])
        with self.assertRaises(spending.SpendingError) as caught:
            spending.admission_preview(self.db, scope, now=100)
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.db.execute('INSERT INTO model_operations VALUES (?,?,?)', ('p', 'i', 'pending'))
        self.db.commit()
        with self.assertRaises(spending.SpendingError) as caught:
            spending.capacity_preview(self.db, scope, now=100)
        self.assertEqual(caught.exception.code, 'outcome_uncertain')
        self.db.execute('DELETE FROM model_operations'); self.db.commit()
        self.item['model_access'] = {'reason': 'quota_exhausted'}; self.save()
        with self.assertRaises(spending.SpendingError) as caught:
            spending.capacity_preview(self.db, scope, now=100)
        self.assertEqual(caught.exception.code, 'quota_exhausted')

    def test_consumed_history_does_not_fence_remaining_or_reuse_retired_slots(self):
        scope = self.write_authority['scope']
        slot = self.config['slots'][0]
        handoff = {'stage': slot['stage'], 'profile': deepcopy(slot['profile']), 'workspace': str(self.root / 'workspace')}
        row: dict[str, Any] = dict(assignment_id='a1', claim_id=slot['claim_id'], handoff=handoff,
                   handoff_digest=assignments.digest(handoff), ticket_scope=deepcopy(scope),
                   runtime={'status': 'complete', 'run_id': 'run1', 'container_removed': True, 'artifacts': str(self.root / 'artifacts')})
        with patch.object(assignments, 'rows', return_value=[row]):
            result = self.collect()
            self.assertTrue(result['capability_ready'], result['checks'])
            capacity = readiness.slot_capacity(self.db, self.config['slots'], scope, stage='simplify')
            self.assertNotIn(slot['claim_id'], [v['claim_id'] for v in capacity['available']])
            with self.assertRaises(repositories.RepositoryError):
                readiness.slot_capacity(self.db, self.config['slots'], scope, stage=slot['stage'])
            row['runtime']['status'] = 'running'
            readiness.slot_capacity(self.db, self.config['slots'], scope, stage=slot['stage'], assignment_id='a1', run_id='run1')
            for key, value in [('run_id', 'wrong'), ('assignment_id', 'wrong')]:
                selected = dict(stage=slot['stage'], assignment_id='a1', run_id='run1'); selected[key] = value
                with self.assertRaises(repositories.RepositoryError):
                    readiness.slot_capacity(self.db, self.config['slots'], scope, **selected)
            row['restart_disposition'] = {'reference': 'retired'}
            with self.assertRaises(repositories.RepositoryError):
                readiness.slot_capacity(self.db, self.config['slots'], scope, stage=slot['stage'], assignment_id='a1', run_id='run1')
            row.pop('restart_disposition')
            row['handoff']['profile']['home'] += '-wrong'
            with self.assertRaises(repositories.RepositoryError):
                readiness.slot_capacity(self.db, self.config['slots'], scope)
        # A preexisting unowned home consumes only that slot, not stage policy.
        Path(slot['profile']['home']).mkdir()
        result = self.collect()
        self.assertEqual(result['checks']['stages']['state'], 'ready')
        with self.assertRaises(repositories.RepositoryError):
            readiness.slot_capacity(self.db, self.config['slots'], scope, stage=slot['stage'])
        for other in self.config['slots'][1:]: Path(other['profile']['home']).mkdir()
        self.assertEqual(self.collect()['checks']['capacity']['code'], 'slot_capacity_unavailable')

    def test_concrete_execution_reader_inspects_native_binding_and_source_only(self):
        selected = skills.resolve_installed(self.config['planning_skills'])
        native = dict(assignment_id='planning1', session_id='session1', operator_id='operator', binding_reference='original',
                      sources=selected, adaptation=planning.INSTRUCTIONS, origin=self.origin, run_id='native1',
                      task_id='task1', turn_id='turn1', observations=[{'role': 'user', 'content': 'Original input'}])
        self.item['planning'] = dict(status='pending', assignment_id='planning1', origin=self.origin,
            request={'skills': self.config['planning_skills']}, skills=selected, instructions=planning.INSTRUCTIONS, native_execution=native)
        self.save()
        binding = dict(state=str(self.root / 'state.sqlite'), project='p', iteration='i', operator_id='operator',
                       session_id='session1', reference='original', origin=self.origin)
        approval: dict[str, Any] = dict(binding=binding, assignment_id='planning1', profile_name='native-profile',
                        source_digest=assignments.digest({'skills': selected, 'adaptation': planning.INSTRUCTIONS}),
                        allowance=dict(reference='existing', provider_route='existing-native', model='fixture-model',
                                       expires_at=2**40, max_wakeups=1))
        path = self.file('planning.json', approval)
        self.service['dispatch_config'] = self.file('dispatch.json', dict(project='p', iteration='i', operator_id='operator',
             planning_config=path, synthesis_config=self.config['synthesis_config']))
        root = self.root / 'native'; root.mkdir()
        source = root / 'contract.py'; source.write_bytes(b'fixture native source\n'); source.chmod(0o600)
        reader = capabilities.HostReaders(self.db, self.service, self.config, self.github, native_root=root)
        stages = self.execution_authority['stages']
        # Independent fixture contract replaces installed-host metadata only.
        original_physical = capabilities.physical
        # Independent host-source metadata fixture, never chmod the checkout.
        host_source = self.root / 'installed-host-source.py'
        host_source.write_bytes(b'explicit mocked built-in source metadata\n'); host_source.chmod(0o600)
        def host_physical(path):
            return host_source if Path(path).parent == Path(readiness.__file__).parent else original_physical(path)
        with patch.object(capabilities, 'NATIVE_FILES', {'contract.py': sha(b'fixture native source\n')}), \
                patch.object(capabilities, 'physical', side_effect=host_physical):
            first = reader.execution_support(self.write_authority['scope'], stages)
            self.assertTrue(assignments.sha(first))
            self.assertNotIn('native_wakeup', self.item['planning'])
            self.item['planning']['status'] = 'complete'; self.save()
            approval['allowance']['expires_at'] = 1; self.file('planning.json', approval)
            reader.execution_support(self.write_authority['scope'], stages)
            self.item['planning']['status'] = 'pending'; self.save()
            with self.assertRaises(repositories.RepositoryError): reader.execution_support(self.write_authority['scope'], stages)
            approval['allowance']['expires_at'] = 2**40; self.file('planning.json', approval)
            source.write_bytes(b'drift')
            with self.assertRaises(repositories.RepositoryError) as caught: reader.execution_support(self.write_authority['scope'], stages)
            self.assertEqual(caught.exception.code, 'native_contract_unavailable')
            source.write_bytes(b'fixture native source\n')
            approval['binding']['session_id'] = 'wrong'; self.file('planning.json', approval)
            with self.assertRaises(repositories.RepositoryError): reader.execution_support(self.write_authority['scope'], stages)
        for effect in self.effects: effect.assert_not_called()

    def test_independent_authority_and_executor_denials_are_redacted(self):
        for name, family in [('github_authority', 'github_authority'), ('execution_support', 'execution')]:
            with self.subTest(name=name):
                reader = getattr(self.readers, name)
                reader.side_effect = RuntimeError('raw secret credential material')
                result = self.collect()
                self.assertEqual(result['checks'][family]['state'], 'blocked')
                self.assertNotIn('credential material', json.dumps(result))
                reader.side_effect = None


class GitHubCapabilityTests(unittest.TestCase):
    """Explicit API response/header fixtures: no real requests or credentials."""
    def setUp(self):
        self.github: Any = repositories.GitHub('https://api.github.com', 'gho_explicit-test-fixture-not-a-secret', 1)
        self.scope = dict(project='p', iteration='i', operator_id='o', repository='owner/product', repository_id=7,
                          api_base=self.github.base, board={'project_id': 'BOARD'})
        self.user = dict(node_id='USER', login='operator')
        self.repo: dict[str, Any] = dict(id=7, full_name='owner/product', private=True, archived=False, disabled=False,
                         permissions={'pull': True, 'push': True})
        self.graph: dict[str, Any] = {'data': dict(viewer={'id': 'USER', 'login': 'operator'},
            repository=dict(databaseId=7, nameWithOwner='owner/product', isPrivate=True, viewerPermission='WRITE'),
            node=dict(id='BOARD', closed=False, viewerCanUpdate=True))}
        self.scopes = 'repo, project'
        self.requests = []
        self.github.opener = SimpleNamespace(open=Mock(side_effect=self.response))
        self.reader = capabilities.HostReaders(None, {}, {}, self.github)

    def response(self, request, **kwargs):
        self.requests.append(request)
        self.assertEqual(request.get_method(), 'POST' if request.full_url.endswith('/graphql') else 'GET')
        if request.data:
            self.assertTrue(json.loads(request.data)['query'].startswith('query ReadinessAuthority'))
        body = self.graph if request.data else self.user if request.full_url.endswith('/user') else self.repo
        response: Any = io.BytesIO(json.dumps(body).encode())
        response.status = 200
        response.geturl = lambda: request.full_url
        response.headers = Message()
        if self.scopes is not None: response.headers['X-OAuth-Scopes'] = self.scopes
        # Accepted scopes deliberately do not prove the token has scopes.
        response.headers['X-Accepted-OAuth-Scopes'] = 'repo, project'
        return response

    def check(self):
        return self.reader.github_authority(self.scope, readiness.WRITES)

    def test_classic_effective_scope_and_exact_permission_path(self):
        self.assertTrue(assignments.sha(self.check()))
        self.assertEqual(len(self.requests), 3)
        self.assertNotIn('gho_', self.check())
        for key, value, code in [('scopes', None, 'github_scopes_unavailable'),
                                  ('scopes', 'read:project, repo', 'github_scopes_insufficient')]:
            setattr(self, key, value)
            with self.assertRaises(repositories.RepositoryError) as caught: self.check()
            self.assertEqual(caught.exception.code, code)
        self.scopes = 'repo, project'
        for token in ('github_pat_test', 'ghs_test', 'unknown'):
            self.github.bearer = token
            before = len(self.requests)
            with self.assertRaises(repositories.RepositoryError) as caught: self.check()
            self.assertEqual(caught.exception.code, 'github_route_unsupported')
            self.assertEqual(before, len(self.requests))
        self.github.bearer = 'gho_explicit-test-fixture-not-a-secret'
        self.github.base = 'https://proxy.invalid'
        with self.assertRaises(repositories.RepositoryError) as caught: self.check()
        self.assertEqual(caught.exception.code, 'github_route_unsupported')

    def test_read_success_or_role_alone_never_proves_effective_writes(self):
        original_repo, original_graph = deepcopy(self.repo), deepcopy(self.graph)
        for mutation in ('push', 'pull', 'board-write', 'repo-id', 'board-id', 'principal', 'archived', 'graphql-error'):
            self.repo, self.graph = deepcopy(original_repo), deepcopy(original_graph)
            if mutation in ('push', 'pull'): self.repo['permissions'][mutation] = False
            if mutation == 'board-write': self.graph['data']['node']['viewerCanUpdate'] = False
            if mutation == 'repo-id': self.graph['data']['repository']['databaseId'] = 8
            if mutation == 'board-id': self.graph['data']['node']['id'] = 'OTHER'
            if mutation == 'principal': self.graph['data']['viewer']['id'] = 'OTHER'
            if mutation == 'archived': self.repo['archived'] = True
            if mutation == 'graphql-error': self.graph['errors'] = [{'message': 'secret-bearing-error-never-returned'}]
            with self.subTest(mutation=mutation), self.assertRaises(repositories.RepositoryError): self.check()

    def test_principal_and_transport_cannot_change_during_observation(self):
        self.check()
        self.user['node_id'] = 'OTHER'; self.graph['data']['viewer']['id'] = 'OTHER'
        with self.assertRaises(repositories.RepositoryError) as caught: self.check()
        self.assertEqual(caught.exception.code, 'github_identity_changed')
        self.reader.principal = None
        original = self.response
        def drift(request, **kwargs):
            response = original(request, **kwargs)
            if request.full_url.endswith('/graphql'): self.github.bearer = 'gho_changed'
            return response
        self.github.opener.open.side_effect = drift
        with self.assertRaises(repositories.RepositoryError) as caught: self.check()
        self.assertEqual(caught.exception.code, 'github_identity_changed')


class EnforcedReadinessTests(SupervisedFixture, unittest.TestCase):
    file = ReadinessTests.file
    save = ReadinessTests.save
    read = ReadinessTests.read

    def setUp(self):
        ReadinessTests.setUp(self)
        for name, kind in (('operation_id', 'TEXT'), ('reservation', 'INTEGER'), ('actual_units', 'INTEGER')):
            self.db.execute('ALTER TABLE model_operations ADD COLUMN ' + name + ' ' + kind)
        self.db.commit()
        self.bind_service()

    def test_service_component_liveness_capability_and_runnable_are_separate(self):
        from factory_v1 import service_dispatch
        self.assertFalse(service.inspect_readiness(self.service, running=True)['iteration_runnable'])
        dispatch_config = dict.fromkeys(service_dispatch.FIELDS)
        dispatch_config.update(operator_id='operator', project='p', iteration='i', reference='fixture', roles={}, launchers={})
        self.service['dispatch_config'] = self.file('dispatch.json', dispatch_config)
        self.item['planning'] = {'status': 'complete'}; self.save()
        self.file('service.json', self.service)
        self.db.execute('UPDATE supervised_readiness SET policy=?',
                        (readiness_gate.policy(self.service, self.config, self.db),)); self.db.commit()
        for running in (True, False, None):
            report = service.inspect_readiness(self.service, running=running)
            self.assertTrue(report['capability_ready'], report)
            self.assertEqual(report['iteration_runnable'], running is True)
        self.item['status'] = 'paused'; self.save()
        report = service.inspect_readiness(self.service, running=True)
        self.assertTrue(report['capability_ready']); self.assertFalse(report['iteration_runnable'])
        self.item.update(status='waiting-for-operator', completion={'report_id': 'done'}); self.save()
        self.assertFalse(service.inspect_readiness(self.service, running=True)['iteration_runnable'])

    def test_real_shared_admission_recollects_without_nested_remote_io(self):
        with controls.admission(self.db, 'p', 'i'):
            count = self.github.request.call_count
            with controls.admission(self.db, 'p', 'i'):
                self.assertEqual(count, self.github.request.call_count)
        self.assertGreater(count, 0)
        self.write_authority['operations'].remove('projects')
        with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
        self.assertEqual(caught.exception.code, 'readiness_blocked')

    def test_direct_public_lane_cannot_evade_persisted_missing_configuration(self):
        self.service['readiness_config'] = None
        self.file('service.json', self.service)
        before = '\n'.join(self.db.iterdump())
        with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
        self.assertEqual(caught.exception.code, 'readiness_configuration_required')
        self.assertEqual(before, '\n'.join(self.db.iterdump()))
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'missing-config-stop')
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))

    def test_last_read_source_config_and_local_ownership_drift_refuse_claim(self):
        original = self.readers.execution_support.side_effect
        for mutation in ('source', 'config', 'ownership'):
            with self.subTest(mutation=mutation):
                source = Path(self.config['slots'][0]['skills'][0]['path'])
                raw = source.read_bytes()
                def drift(*args):
                    result = original(*args)
                    if mutation == 'source': source.write_bytes(raw + b'drift')
                    if mutation == 'config':
                        changed = deepcopy(self.config); changed['reference'] = 'unreviewed'
                        self.file('readiness.json', changed)
                    if mutation == 'ownership':
                        self.item['reservation'] = {'issue_number': 999}; self.save()
                    return result
                self.readers.execution_support.side_effect = drift
                with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
                self.assertEqual(caught.exception.code, 'readiness_inputs_changed')
                source.write_bytes(raw); self.file('readiness.json', self.config)
                self.item.pop('reservation', None); self.save()
        self.readers.execution_support.side_effect = original

    def test_selected_stage_claim_profile_and_current_capacity_are_real(self):
        slot = deepcopy(self.config['slots'][0])
        request = {k: slot[k] for k in ('stage', 'claim_id', 'profile', 'skills')}
        self.assertEqual(self.admit(selection=request), 'admitted')
        for key, value in (('stage', 'simplify'), ('claim_id', 'unselected'), ('profile', dict(request['profile'], name='other'))):
            changed = deepcopy(request); changed[key] = value
            with self.subTest(key=key), self.assertRaises(repositories.RepositoryError): self.admit(selection=changed)
        Path(request['profile']['home']).mkdir()
        with self.assertRaises(repositories.RepositoryError): self.admit(selection=request)

    def test_allowance_use_is_not_a_sealed_identity_change_but_exhaustion_blocks(self):
        first = readiness_gate.policy(self.service, self.config, self.db)
        self.assertEqual(self.admit(), 'admitted')
        self.db.execute('UPDATE model_grants SET remaining=remaining-1'); self.db.commit()
        self.assertEqual(first, readiness_gate.policy(self.service, self.config, self.db))
        self.assertEqual(self.admit(), 'admitted')
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
        self.assertEqual(caught.exception.code, 'readiness_blocked')

    def test_health_denial_and_pause_during_read_return_stop_new_claims(self):
        self.health['sha'] = 'b'*40
        with self.assertRaises(repositories.RepositoryError): self.admit()
        self.health['sha'] = 'a'*40
        original = self.readers.execution_support.side_effect
        def pause(*args):
            result = original(*args)
            controls.request(self.db, 'p', 'i', 'operator', 'pause', 'read-return-pause')
            return result
        self.readers.execution_support.side_effect = pause
        with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))

    def test_explicit_policy_selection_requires_pause_without_granting_execution(self):
        with self.assertRaises(repositories.RepositoryError): readiness_gate.seal(self.db, Path(self.service_path))
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'select-policy')
        readiness_gate.seal(self.db, Path(self.service_path))
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))
        self.assertFalse(service.inspect_readiness(self.service, running=True)['iteration_runnable'])

    def test_ready_and_denied_cycle_reach_only_existing_dispatch_seam(self):
        from factory_v1 import service_dispatch
        self.item['planning'] = {'status': 'complete'}; self.save()
        dispatch_config = dict.fromkeys(service_dispatch.FIELDS)
        dispatch_config.update(operator_id='operator', project='p', iteration='i', reference='fixture', roles={}, launchers={})
        self.service['dispatch_config'] = self.file('dispatch.json', dispatch_config)
        self.file('service.json', self.service)
        self.db.execute('UPDATE supervised_readiness SET policy=?',
                        (readiness_gate.policy(self.service, self.config, self.db),)); self.db.commit()
        with patch.object(delivery, 'sample', return_value={'evidence': {'status': 'healthy'}}), \
             patch.object(service.attention, 'records', return_value=[]), \
             patch.object(service, 'dispatch', return_value={'status': 'selected-existing-seam'}) as dispatch:
            self.assertEqual(service.cycle(self.service, self.github)['status'], 'selected-existing-seam')
            dispatch.assert_called_once()
            dispatch.reset_mock()
            self.write_authority['operations'].remove('projects')
            self.assertEqual(service.cycle(self.service, self.github)['blockers'], ['readiness_blocked'])
            dispatch.assert_not_called()

    def test_direct_public_tick_refuses_missing_readiness_before_selection(self):
        from factory_v1 import service_dispatch
        self.service['readiness_config'] = None
        self.file('service.json', self.service)
        with patch.object(service_dispatch, 'select') as select:
            with self.assertRaises(repositories.RepositoryError) as caught:
                service_dispatch.tick(self.service, self.github)
        self.assertEqual(caught.exception.code, 'readiness_configuration_required')
        select.assert_not_called()


class CoreReadinessTests(SupervisedFixture, unittest.TestCase):
    setUp = EnforcedReadinessTests.setUp
    file = ReadinessTests.file
    save = ReadinessTests.save
    read = ReadinessTests.read

    def record(self, slot, status=None):
        from factory_v1 import requirements
        handoff = {k: deepcopy(slot[k]) for k in ('stage', 'profile', 'skills')}
        for descriptor in handoff['skills']:
            descriptor['instructions'] = Path(descriptor['path']).read_text()
        handoff['workspace'] = str(self.root / 'workspace')
        handoff['issue'] = dict(number=1, id=10, node_id='I1', body_sha256='c'*64)
        row = dict(assignment_id=assignments.digest(slot['claim_id']), claim_id=slot['claim_id'],
                   handoff=handoff, handoff_digest=assignments.digest(handoff),
                   revision=requirements.authority(self.item, 1),
                   ticket_scope=dict(project='p', iteration='i', repository='owner/product', repository_id=7,
                                     issue_number=1, issue_id=10))
        if status:
            row['runtime'] = dict(run_id='run-' + slot['claim_id'], status=status,
                artifacts=str(self.root / ('result-' + slot['claim_id'])), container_removed=status == 'complete',
                controller_pid=123, controller_start='fixture-start')
        self.db.execute('CREATE TABLE IF NOT EXISTS role_assignments (assignment_id TEXT, payload TEXT)')
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.db.commit()
        return row

    def test_exact_prepared_running_terminal_run_and_launcher_selection(self):
        slot = self.config['slots'][0]
        row = self.record(slot)
        selection = dict(assignment_id=row['assignment_id'], run_id=None, launcher=slot['launcher'])
        self.assertEqual(self.admit(selection=selection), 'admitted')
        for changes in ({'run_id': 'invented'}, {'launcher': '/unselected.json'}):
            with self.subTest(changes=changes), self.assertRaises(repositories.RepositoryError):
                self.admit(selection={**selection, **changes})
        row['runtime'] = dict(run_id='actual-run', status='running', controller_pid=123,
                             controller_start='fixture-start', artifacts=str(self.root / 'result'))
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),)); self.db.commit()
        with patch('factory_v1.restart.process_identity', return_value='fixture-start'):
            self.assertEqual(self.admit(selection={**selection, 'run_id': 'actual-run'}), 'admitted')
            with self.assertRaises(repositories.RepositoryError): self.admit(selection=selection)
        row['runtime'].update(status='complete', container_removed=True)
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),)); self.db.commit()
        terminal = {**selection, 'run_id': 'actual-run'}
        self.assertEqual(self.admit(selection=terminal, operation='effect'), 'admitted')
        with self.assertRaises(repositories.RepositoryError): self.admit(selection=terminal)

    def test_terminal_slots_allow_effect_and_notification_but_not_new_work(self):
        for slot in self.config['slots']: self.record(slot, 'complete')
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        self.assertEqual(self.admit(operation='effect'), 'admitted')
        with self.assertRaises(repositories.RepositoryError): self.admit()
        self.health['sha'] = 'b'*40
        self.item.update(status='waiting-for-operator', completion={'report_id': 'completed'}); self.save()
        self.assertEqual(self.admit(operation='notification'), 'admitted')
        self.item['origin']['thread_id'] = 'changed'; self.save()
        with self.assertRaises(repositories.RepositoryError): self.admit(operation='notification')

    def test_nested_local_revalidation_checks_pins_origin_and_ownership_without_remote_reads(self):
        for change in ('source', 'mode', 'origin', 'ownership'):
            with self.subTest(change=change):
                path = Path(self.config['slots'][0]['skills'][0]['path'])
                raw, mode = path.read_bytes(), path.stat().st_mode
                with controls.admission(self.db, 'p', 'i'):
                    calls = self.github.request.call_count
                    if change == 'source': path.write_bytes(raw + b'drift')
                    if change == 'mode': path.chmod(0o600)
                    if change == 'origin': self.item['origin']['thread_id'] = 'wrong'; self.save()
                    if change == 'ownership':
                        other = deepcopy(self.item); other['iteration_id'] = 'other'
                        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'other', json.dumps(other))); self.db.commit()
                    with self.assertRaises(repositories.RepositoryError):
                        readiness_gate.check(self.db, 'p', 'i')
                    self.assertEqual(calls, self.github.request.call_count)
                path.write_bytes(raw); path.chmod(stat.S_IMODE(mode))
                self.item['origin']['thread_id'] = '11'; self.save()

    def test_mutable_destinations_and_accounting_do_not_change_policy(self):
        first = readiness_gate.policy(self.service, self.config, self.db)
        Path(self.config['slots'][0]['profile']['home']).write_text('mutable home observation')
        artifacts = Path(json.loads(Path(self.config['slots'][0]['launcher']).read_text())['artifacts_root'])
        (artifacts / 'new-result').write_text('mutable result')
        self.db.execute('UPDATE model_grants SET remaining=remaining-1'); self.db.commit()
        self.assertEqual(first, readiness_gate.policy(self.service, self.config, self.db))
        self.assertEqual(self.admit(operation='effect'), 'admitted')

    def repair(self):
        evidence = dict(repository='owner/product', repository_id=7, main='a'*40, checks=[], workflows=[],
                        deployment=None, health=None, status='failed', failure='fixture-failure')
        evidence_id = assignments.digest(evidence)
        authority = dict(operator_id='operator', reference='host-scoped-registration', incident_id='incident',
                         evidence_id=evidence_id, issue=dict(number=1, id=10, node_id='I1', body_sha256=sha(b'fixture')),
                         paths=['app.py'], removable_assignments=[])
        incident = dict(incident_id='incident', status='active', monitor_sha256=assignments.digest(self.delivery_config),
                        first_evidence=evidence_id, latest_evidence=evidence_id,
                        recovery_assignment=assignments.digest(authority), recovery_issue=1)
        self.item['ticket_work'] = {'inputs': {'handoff': {}}}
        self.item.update(main_incident=incident, main_recoveries={'incident': dict(status='registered', config=authority,
            evidence=evidence, assignment=None, parked=[], removable={}, removed={}, clearance=None)},
            delivery_monitor=dict(config=self.delivery_config, config_sha256=assignments.digest(self.delivery_config),
                latest=dict(evidence_id=evidence_id, status='failed', main='a'*40), sequence=1),
            restart_reconciliation=dict(status='blocked', observations={}, blockers=[dict(boundary='delivery', code='main_frozen')]))
        self.db.executescript('CREATE TABLE delivery_observations(project TEXT, iteration TEXT, id TEXT, payload TEXT);'
                              'CREATE TABLE delivery_incidents(project TEXT, iteration TEXT, id TEXT, payload TEXT);')
        self.db.execute('INSERT INTO delivery_observations VALUES (?,?,?,?)', ('p', 'i', evidence_id, json.dumps(evidence)))
        self.db.execute('INSERT INTO delivery_incidents VALUES (?,?,?,?)', ('p', 'i', 'incident', json.dumps(incident)))
        self.db.commit(); self.save()
        self.health['sha'] = 'b'*40
        # Independent tracker/publication observations only; durable registration stays real.
        self.stack.enter_context(patch.object(tickets.gh, 'read_issue', return_value={**authority['issue'], 'body': 'fixture', 'state': 'open'}))
        self.stack.enter_context(patch.object(tickets, 'pinned_inputs', return_value={'handoff': {}}))
        return authority

    def test_registered_repair_failed_health_is_exact_and_preserves_unrelated_holds(self):
        authority = self.repair()
        slot = next(s for s in self.config['slots'] if s['stage'] == 'repair')
        selection = {k: deepcopy(slot[k]) for k in ('stage', 'profile', 'skills', 'claim_id')}
        selection['issue'] = authority['issue']
        self.assertEqual(self.admit(selection=selection), 'admitted')
        self.assertEqual(self.admit(operation='effect'), 'admitted')
        with self.assertRaises(repositories.RepositoryError): self.admit()
        wrong = {**selection, 'issue': {**selection['issue'], 'id': 999}}
        with self.assertRaises(repositories.RepositoryError): self.admit(selection=wrong)
        self.item['restart_reconciliation']['blockers'].append(dict(boundary='model', code='outcome_uncertain')); self.save()
        with self.assertRaises(repositories.RepositoryError): self.admit(selection=selection)

    def test_selected_revision_and_candidate_rejection_are_not_runnable(self):
        row = self.record(self.config['slots'][0], 'complete')
        selection = dict(assignment_id=row['assignment_id'], run_id=row['runtime']['run_id'])
        for mutation in ('revision', 'rejection'):
            with self.subTest(mutation=mutation):
                if mutation == 'revision':
                    self.item['requirement_changes'] = {'change': dict(kind='material', status='pending', affected=[1])}; self.save()
                else:
                    row['review_holds'] = {'axis': {'verdict': 'reject'}}
                    self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),)); self.db.commit()
                with self.assertRaises(repositories.RepositoryError): self.admit(selection=selection, operation='effect')
                self.item.pop('requirement_changes', None); self.save()

    def test_private_credential_client_selection_and_unsupported_route_refusal(self):
        # Synthetic public fixture string, never an actual credential or provider call.
        config = {**self.service, 'github_config': self.file('github.json', dict(api_base='https://api.github.com', bearer='gho_fixture_not_a_credential'))}
        # bind_service mocks independent clients; call the original selector explicitly.
        from factory_v1.service import GitHub
        with patch.object(service, 'github_client', wraps=CoreReadinessTests.original_client):
            selected = service.github_client(config)
            self.assertIsInstance(selected, GitHub)
            self.assertEqual(selected.base, 'https://api.github.com')
            Path(config['github_config']).chmod(0o644)
            with self.assertRaises(repositories.RepositoryError): service.github_client(config)
            Path(config['github_config']).chmod(0o600)
            self.file('github.json', dict(api_base='https://other.invalid', bearer='gho_fixture_not_a_credential'))
            with self.assertRaises(repositories.RepositoryError): service.github_client(config)

    original_client = staticmethod(service.github_client)

    def test_public_child_launch_checks_exact_selected_launcher_without_worker_journey(self):
        from factory_v1 import service_dispatch
        slot = self.config['slots'][0]
        row = self.record(slot)
        child = Mock(pid=321, wait=Mock(return_value=0))
        with patch.object(service_dispatch.subprocess, 'Popen', return_value=child) as spawn:
            service_dispatch._launch_child(self.service, row['assignment_id'], slot['launcher'])
            self.assertEqual(spawn.call_count, 1)
            with self.assertRaises(repositories.RepositoryError):
                service_dispatch._launch_child(self.service, row['assignment_id'], '/unselected.json')
            self.assertEqual(spawn.call_count, 1)
        self.assertNotIn('runtime', assignments.inspect(self.db, 'p', 'i', 'operator', row['assignment_id']))

    def test_selected_final_effect_is_runnable_with_exhausted_slots_and_held_revision_is_not(self):
        from factory_v1 import service_dispatch
        for slot in self.config['slots']: self.record(slot, 'complete')
        self.item.update(planning={'status': 'complete'}, ticket_work={'status': 'synthesis_complete', 'publication_complete': False})
        self.save()
        config = dict.fromkeys(service_dispatch.FIELDS)
        config.update(operator_id='operator', project='p', iteration='i', reference='fixture', roles={}, launchers={})
        self.service['dispatch_config'] = self.file('dispatch.json', config)
        self.file('service.json', self.service)
        self.db.execute('UPDATE supervised_readiness SET policy=?',
                        (readiness_gate.policy(self.service, self.config, self.db),)); self.db.commit()
        result = service.inspect_readiness(self.service, running=True)
        self.assertFalse(result['capability_ready'])
        self.assertTrue(result['iteration_runnable'], result)
        self.assertEqual(result['selected_operation'], 'effect')
        self.assertEqual(result['selected_stage'], 'publish-tickets')
        self.item['restart_reconciliation'] = dict(status='blocked', observations={},
            blockers=[dict(boundary='requirements', code='requirements_held')]); self.save()
        self.assertFalse(service.inspect_readiness(self.service, running=True)['iteration_runnable'])

    original_prepare = staticmethod(spending._prepare)

    def spending_records(self):
        self.db.executescript('DROP TABLE model_grants; DROP TABLE model_operations;')
        spending.initialize(self.db)
        self.db.execute('INSERT INTO model_grants VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ('grant', 'p', 'i', *model_access.SUBSCRIPTION_SCOPE, 'allowance', 2, 2, 200, 'explicit-host-fixture'))
        self.db.commit()
        self.db.execute('UPDATE supervised_readiness SET policy=?',
                        (readiness_gate.policy(self.service, self.config, self.db),)); self.db.commit()

    def test_actual_model_call_consumes_allowance_without_policy_fence_and_quota_still_blocks(self):
        self.spending_records()
        adapter = SimpleNamespace(reservation=1, payload_fields=None, call=Mock(return_value=(1, {'fixture': True})))
        policy = readiness_gate.policy(self.service, self.config, self.db)
        with patch.object(spending, '_prepare', wraps=CoreReadinessTests.original_prepare):
            result = spending.execute_controlled(self.db, ('p', 'i', *model_access.SUBSCRIPTION_SCOPE),
                'call1', {'input': 'fixture'}, 1, now=100, adapter=adapter)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(policy, readiness_gate.policy(self.service, self.config, self.db))
            adapter.call.side_effect = model_access.ModelAccessError('quota_exhausted')
            with self.assertRaises(spending.SpendingError) as caught:
                spending.execute_controlled(self.db, ('p', 'i', *model_access.SUBSCRIPTION_SCOPE),
                    'call2', {'input': 'fixture'}, 1, now=100, adapter=adapter)
            self.assertEqual(caught.exception.code, 'quota_exhausted')
            self.assertEqual(adapter.call.call_count, 2)
            with self.assertRaises((spending.SpendingError, repositories.RepositoryError)):
                spending.execute_controlled(self.db, ('p', 'i', *model_access.SUBSCRIPTION_SCOPE),
                    'call3', {'input': 'fixture'}, 1, now=100, adapter=adapter)
            self.assertEqual(adapter.call.call_count, 2)

    def test_last_model_forward_rechecks_policy_and_retains_known_no_effect(self):
        self.spending_records()
        source = Path(self.config['slots'][0]['skills'][0]['path'])
        def drift(*args, **kwargs):
            result = CoreReadinessTests.original_prepare(*args, **kwargs)
            source.write_bytes(source.read_bytes() + b'drift')
            return result
        adapter = SimpleNamespace(reservation=1, payload_fields=None, call=Mock())
        with patch.object(spending, '_prepare', side_effect=drift):
            with self.assertRaises(spending.SpendingError) as caught:
                spending.execute_controlled(self.db, ('p', 'i', *model_access.SUBSCRIPTION_SCOPE),
                    'call1', {'input': 'fixture'}, 1, now=100, adapter=adapter)
        self.assertEqual(caught.exception.code, 'readiness_inputs_changed')
        adapter.call.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status,actual_units FROM model_operations').fetchone(), ('failed', 0))

    def test_unknown_operation_cannot_be_a_bypass(self):
        for operation in ('repair', '', None, {}, []):
            with self.subTest(operation=operation), self.assertRaises(repositories.RepositoryError): self.admit(operation=operation)
