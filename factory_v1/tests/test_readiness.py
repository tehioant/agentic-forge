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

from factory_v1 import assignments, delivery, model_access, readiness, role_skills, sandbox_policy, skills, tickets


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
        self.item = dict(project_id='p', iteration_id='i', repository='owner/product', status='active',
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
        self.readers: Any = Mock(spec=readiness.UnsupportedReaders)
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
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.socket.connect', 'socket.socket.bind',
                       'factory_v1.messaging.HermesTransport.send', 'factory_v1.model_access.SubscriptionTransport.call',
                       'factory_v1.spending.add_grant', 'factory_v1.spending._prepare',
                       'factory_v1.requirement_execution.invoke'):
            self.effects.append(self.stack.enter_context(patch(target, side_effect=AssertionError('forbidden effect'))))

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
        self.readers = readiness.UnsupportedReaders()
        result = self.collect(running=True)
        self.assertEqual(result['checks']['github_authority']['state'], 'blocked')
        self.assertEqual(result['checks']['execution']['state'], 'blocked')
        self.readers = Mock(spec=readiness.UnsupportedReaders, github_authority=Mock(return_value=True), execution_support=Mock(return_value=True))
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
        for mutation in ('bytes', 'review-bytes', 'dependency', 'provider', 'collision', 'consumed', 'missing-stage'):
            with self.subTest(mutation=mutation):
                self.config = deepcopy(baseline); slot = self.config['slots'][0]
                if mutation == 'bytes': Path(slot['skills'][0]['path']).write_text('drift')
                if mutation == 'review-bytes':
                    Path(role_skills.SELECTED['code-review'][0]).write_text('drift')
                if mutation == 'dependency': slot['skills'][0]['dependencies'] = ['invented']
                if mutation == 'provider': slot['profile']['provider'] = 'paid-fallback'
                if mutation == 'collision':
                    self.config['slots'][1]['profile']['name'] = slot['profile']['name']
                if mutation == 'consumed': Path(slot['profile']['home']).mkdir()
                if mutation == 'missing-stage': self.config['slots'].pop()
                self.assertEqual(self.collect()['checks']['stages']['state'], 'blocked')
                if mutation == 'bytes': Path(slot['skills'][0]['path']).write_text('fixture source: implement')
                if mutation == 'review-bytes':
                    Path(role_skills.SELECTED['code-review'][0]).write_text('fixture source: code-review')
                if mutation == 'consumed': Path(slot['profile']['home']).rmdir()

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

    def test_independent_authority_and_executor_denials_are_redacted(self):
        for name, family in [('github_authority', 'github_authority'), ('execution_support', 'execution')]:
            with self.subTest(name=name):
                reader = getattr(self.readers, name)
                reader.side_effect = RuntimeError('raw secret credential material')
                result = self.collect()
                self.assertEqual(result['checks'][family]['state'], 'blocked')
                self.assertNotIn('credential material', json.dumps(result))
                reader.side_effect = None
