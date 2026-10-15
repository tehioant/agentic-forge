"""Focused collector checks: explicit records, independent mocked read seams only."""
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
import io
from email.message import Message


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
