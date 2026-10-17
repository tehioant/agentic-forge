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


    def snapshot(self):
        """Restore independent matrix rows without rebuilding host fixtures."""
        backup = sqlite3.connect(':memory:')
        self.db.backup(backup)
        self.addCleanup(backup.close)
        names = ('item', 'config', 'service', 'origin', 'write_authority',
                 'execution_authority', 'health', 'repository', 'board', 'delivery_config')
        bundle = deepcopy({name: getattr(self, name) for name in names})
        paths = {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                 for path in self.root.rglob('*') if path.is_file()
                 and 'state.sqlite' not in path.name}
        existing = set(self.root.rglob('*'))
        readers = self.readers
        mocks = [self.github.request, readers.github_authority, readers.execution_support]
        behavior = [(mock.side_effect, mock.return_value) for mock in mocks]
        def restore():
            self.db.rollback()
            backup.backup(self.db)
            for path in sorted(set(self.root.rglob('*')) - existing,
                               key=lambda path: len(path.parts), reverse=True):
                if path.is_dir(): path.rmdir()
                else: path.unlink()
            for path, (raw, mode) in paths.items():
                path.write_bytes(raw)
                path.chmod(mode)
            for name, value in deepcopy(bundle).items(): setattr(self, name, value)
            self.readers = readers
            for mock, (side_effect, return_value) in zip(mocks, behavior):
                mock.side_effect, mock.return_value = side_effect, return_value
                mock.reset_mock()
        return restore


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        from factory_v1.tests.test_sandbox import bounded_storage_observation
        self.stack.enter_context(patch.object(sandbox_policy, 'storage_observation', bounded_storage_observation))
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
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.chmod(0o644)
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


    original_client = staticmethod(service.github_client)


    original_prepare = staticmethod(spending._prepare)

    def spending_records(self):
        self.db.executescript('DROP TABLE model_grants; DROP TABLE model_operations;')
        spending.initialize(self.db)
        self.db.execute('INSERT INTO model_grants VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            ('grant', 'p', 'i', *model_access.SUBSCRIPTION_SCOPE, 'allowance', 2, 2, 200, 'explicit-host-fixture'))
        self.db.commit()
        self.db.execute('UPDATE supervised_readiness SET policy=?',
                        (readiness_gate.policy(self.service, self.config, self.db),)); self.db.commit()


class ConsolidatedReadinessTests(CoreReadinessTests):
    config: dict[str, Any]

    # Fixtures remain public for sibling focused components; each invariant picks
    # its smallest fixture explicitly rather than initializing a host for API rows.
    def setUp(self):
        pass

    collect = ReadinessTests.collect
    response = GitHubCapabilityTests.response
    check = GitHubCapabilityTests.check

    def test_collector_requires_independent_nonboolean_redacted_authority(self):
        ReadinessTests.setUp(self)
        restore = self.snapshot()
        for outcome in ('supported', 'default', 'boolean', 'github_authority', 'execution_support'):
            with self.subTest(outcome=outcome):
                restore()
                try:
                    if outcome == 'default': self.readers = None
                    elif outcome == 'boolean':
                        self.readers = Mock(spec=readiness.HostReaders,
                            github_authority=Mock(return_value=True), execution_support=Mock(return_value=True))
                    elif outcome != 'supported':
                        getattr(self.readers, outcome).side_effect = RuntimeError('raw secret credential material')
                    result = self.collect(running=outcome == 'default')
                    if outcome == 'supported':
                        self.assertTrue(result['capability_ready'], result['checks'])
                        self.assertTrue(result['iteration_eligible']); self.assertFalse(result['service_running'])
                        self.readers.github_authority.assert_called_once_with({
                            'project': 'p', 'iteration': 'i', 'operator_id': 'operator', 'repository': 'owner/product',
                            'repository_id': 7, 'api_base': 'https://api.github.com', 'board': self.config['board_pin']}, readiness.WRITES)
                        self.assertEqual(set(self.readers.execution_support.call_args.args[1]),
                            {'grill-me', 'grilling', 'to-spec', 'to-tickets', *assignments.ROLES})
                    elif outcome == 'default':
                        self.assertEqual(result['checks']['github_authority']['state'], 'blocked')
                        self.assertEqual(result['checks']['execution']['state'], 'blocked')
                        self.assertEqual(result['checks']['github_authority']['code'], 'github_route_unsupported')
                        self.assertEqual(result['checks']['execution']['code'], 'native_planning_unavailable')
                    elif outcome == 'boolean': self.assertFalse(result['capability_ready'])
                    else:
                        family = 'execution' if outcome == 'execution_support' else 'github_authority'
                        self.assertEqual(result['checks'][family]['state'], 'blocked')
                        self.assertNotIn('credential material', json.dumps(result))
                finally: restore()

    def test_collector_preserves_exact_private_repository_and_board(self):
        ReadinessTests.setUp(self)
        restore = self.snapshot()
        cases = [('projects', 'github_authority'), ('private', 'repository'), ('id', 'repository'),
                 ('field', 'board'), ('option', 'board'), ('provider', 'repository')]
        cases += [(fault, 'stages') for fault in
                  ('bytes', 'review-bytes', 'dependency', 'stage-provider', 'collision', 'missing-stage')]
        cases += [('health', 'delivery'), ('delivery-id', 'delivery'), ('origin', 'notification'),
                  ('scope', 'scope'), ('allowance', 'spending')]
        for fault, family in cases:
            with self.subTest(fault=fault):
                try:
                    if fault == 'projects': self.write_authority['operations'].remove('projects')
                    if fault == 'private': self.repository['private'] = False
                    if fault == 'id': self.repository['id'] = 8
                    if fault == 'field': self.board['status']['id'] = 'REPLACED'
                    if fault == 'option': self.board['status']['options']['Todo'] = 'REPLACED'
                    if fault == 'provider': self.github.request.side_effect = RuntimeError('sensitive arbitrary provider error')
                    slot = self.config['slots'][0]
                    if fault == 'bytes': Path(slot['skills'][0]['path']).write_text('drift')
                    if fault == 'review-bytes': Path(role_skills.SELECTED['code-review'][0]).write_text('drift')
                    if fault == 'dependency': slot['skills'][0]['dependencies'] = ['invented']
                    if fault == 'stage-provider': slot['profile']['provider'] = 'paid-fallback'
                    if fault == 'collision': self.config['slots'][1]['profile']['name'] = slot['profile']['name']
                    if fault == 'missing-stage': self.config['slots'].pop()
                    if fault == 'health': self.health['sha'] = 'b'*40
                    if fault == 'delivery-id':
                        self.delivery_config['repository_id'] = 8; self.file('delivery.json', self.delivery_config)
                    if fault == 'origin': self.item['origin']['thread_id'] = '999'; self.save()
                    if fault == 'scope': self.config['iteration'] = 'sibling'
                    if fault == 'allowance': self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
                    result = self.collect()
                    if fault == 'scope': self.assertEqual(set(result['checks']), {'scope'})
                    else: self.assertEqual(result['checks'][family]['state'], 'blocked')
                    if fault == 'provider': self.assertNotIn('sensitive', json.dumps(result))
                finally: restore()

    def test_paused_inventory_changes_neither_authority_nor_budget_admission(self):
        ReadinessTests.setUp(self)
        first = self.collect()['input_identity']
        self.config['reference'] = 'changed explicit config'
        self.assertNotEqual(first, self.collect()['input_identity'])
        self.config['reference'] = 'explicit-config'
        self.item.update(status='paused', restart_observation={'sample': 'benign'})
        self.save()
        result = self.collect(running=True)
        self.assertEqual(first, result['input_identity'])
        self.assertTrue(result['service_running']); self.assertFalse(result['iteration_eligible'])
        self.assertEqual(result['checks']['controls']['state'], 'blocked')
        self.assertTrue(result['capability_ready'], result['checks'])
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

    def test_capacity_never_reuses_consumed_or_unowned_slots(self):
        ReadinessTests.setUp(self)
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

    def test_concrete_native_reader_checks_original_binding_without_execution(self):
        ReadinessTests.setUp(self)
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

    def test_classic_authority_needs_effective_scopes_and_exact_permissions(self):
        GitHubCapabilityTests.setUp(self)
        result = self.check()
        self.assertTrue(assignments.sha(result))
        self.assertEqual(len(self.requests), 3)
        self.assertNotIn('gho_', result)
        baseline = deepcopy((self.repo, self.graph))
        for fault in ('no-scopes', 'read-scope', 'github_pat_test', 'ghs_test', 'unknown', 'proxy',
                      'push', 'pull', 'board-write', 'repo-id', 'board-id', 'principal', 'archived', 'graphql-error'):
            with self.subTest(fault=fault):
                self.repo, self.graph = deepcopy(baseline)
                self.scopes = 'repo, project'
                self.github.bearer = 'gho_explicit-test-fixture-not-a-secret'
                self.github.base = 'https://api.github.com'
                code = None
                if fault == 'no-scopes': self.scopes, code = None, 'github_scopes_unavailable'
                if fault == 'read-scope': self.scopes, code = 'read:project, repo', 'github_scopes_insufficient'
                if fault in ('github_pat_test', 'ghs_test', 'unknown'):
                    self.github.bearer, code = fault, 'github_route_unsupported'
                if fault == 'proxy': self.github.base, code = 'https://proxy.invalid', 'github_route_unsupported'
                if fault in ('push', 'pull'): self.repo['permissions'][fault] = False
                if fault == 'board-write': self.graph['data']['node']['viewerCanUpdate'] = False
                if fault == 'repo-id': self.graph['data']['repository']['databaseId'] = 8
                if fault == 'board-id': self.graph['data']['node']['id'] = 'OTHER'
                if fault == 'principal': self.graph['data']['viewer']['id'] = 'OTHER'
                if fault == 'archived': self.repo['archived'] = True
                if fault == 'graphql-error': self.graph['errors'] = [{'message': 'secret-bearing-error-never-returned'}]
                before = len(self.requests)
                with self.assertRaises(repositories.RepositoryError) as caught: self.check()
                if code: self.assertEqual(caught.exception.code, code)
                if fault in ('github_pat_test', 'ghs_test', 'unknown'): self.assertEqual(before, len(self.requests))

    def test_classic_authority_pins_principal_and_transport_across_observation(self):
        GitHubCapabilityTests.setUp(self)
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

    def test_service_dispatch_uses_current_selected_operation_not_liveness_or_inventory(self):
        EnforcedReadinessTests.setUp(self)
        from factory_v1 import service_dispatch
        self.assertFalse(service.inspect_readiness(self.service, running=True)['iteration_runnable'])
        dispatch_config = dict.fromkeys(service_dispatch.FIELDS)
        dispatch_config.update(operator_id='operator', project='p', iteration='i', reference='fixture', roles={}, launchers={})
        self.service['dispatch_config'] = self.file('dispatch.json', dispatch_config)
        self.item['planning'] = {'status': 'complete'}; self.save()
        self.file('service.json', self.service)
        self.db.execute('UPDATE supervised_readiness SET policy=?',
                        (readiness_gate.policy(self.service, self.config, self.db),)); self.db.commit()
        restore = self.snapshot()
        cases = [(str(running), running) for running in (True, False, None)]
        cases += [(name, True) for name in ('paused', 'waiting', 'ready-cycle', 'denied-cycle', 'terminal', 'held-terminal')]
        for scenario, running in cases:
            with self.subTest(scenario=scenario):
                restore()
                try:
                    if scenario == 'paused': self.item['status'] = 'paused'
                    if scenario == 'waiting': self.item.update(status='waiting-for-operator', completion={'report_id': 'done'})
                    if scenario == 'denied-cycle': self.write_authority['operations'].remove('projects')
                    terminal = scenario in ('terminal', 'held-terminal')
                    if terminal:
                        for slot in self.config['slots']: self.record(slot, 'complete')
                        self.item['ticket_work'] = {'status': 'synthesis_complete', 'publication_complete': False}
                    if scenario == 'held-terminal':
                        self.item['restart_reconciliation'] = dict(status='blocked', observations={},
                            blockers=[dict(boundary='requirements', code='requirements_held')])
                    self.save()
                    result = service.inspect_readiness(self.service, running=running)
                    self.assertEqual(result['capability_ready'], not terminal and scenario != 'denied-cycle', result)
                    expected = running is True and scenario not in ('paused', 'waiting', 'denied-cycle', 'held-terminal')
                    self.assertEqual(result['iteration_runnable'], expected, result)
                    if scenario == 'terminal':
                        self.assertTrue(result['iteration_runnable'], result)
                        self.assertEqual(result['selected_operation'], 'effect')
                        self.assertEqual(result['selected_stage'], 'publish-tickets')
                    if scenario.endswith('-cycle'):
                        with patch.object(delivery, 'sample', return_value={'evidence': {'status': 'healthy'}}),                              patch.object(service.attention, 'records', return_value=[]),                              patch.object(service, 'dispatch', return_value={'status': 'selected-existing-seam'}) as dispatch:
                            cycle = service.cycle(self.service, self.github)
                            if scenario == 'ready-cycle':
                                self.assertEqual(cycle['status'], 'selected-existing-seam'); dispatch.assert_called_once()
                            else:
                                self.assertEqual(cycle['blockers'], ['readiness_blocked']); dispatch.assert_not_called()
                finally: restore()

    def test_nested_admission_rechecks_local_pins_and_ownership_without_remote_io(self):
        EnforcedReadinessTests.setUp(self)
        restore = self.snapshot()
        path = Path(self.config['slots'][0]['skills'][0]['path'])
        raw, mode = path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
        with controls.admission(self.db, 'p', 'i'):
            count = self.github.request.call_count
            with controls.admission(self.db, 'p', 'i'):
                self.assertEqual(count, self.github.request.call_count)
            for change in ('source', 'mode', 'origin', 'ownership'):
                with self.subTest(change=change):
                    restore()
                    calls = self.github.request.call_count
                    try:
                        if change == 'source': path.write_bytes(raw + b'drift')
                        if change == 'mode': path.chmod(0o600)
                        if change == 'origin': self.item['origin']['thread_id'] = 'wrong'; self.save()
                        if change == 'ownership':
                            other = deepcopy(self.item); other['iteration_id'] = 'other'
                            self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'other', json.dumps(other))); self.db.commit()
                        with self.assertRaises(repositories.RepositoryError): readiness_gate.check(self.db, 'p', 'i')
                        self.assertEqual(calls, self.github.request.call_count)
                    finally: restore()
        self.assertGreater(count, 0)
        self.write_authority['operations'].remove('projects')
        with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
        self.assertEqual(caught.exception.code, 'readiness_blocked')

    def test_missing_persisted_readiness_blocks_lane_and_tick_before_side_effect(self):
        EnforcedReadinessTests.setUp(self)
        from factory_v1 import service_dispatch
        self.service['readiness_config'] = None
        self.file('service.json', self.service)
        for route in ('admit', 'tick'):
            with self.subTest(route=route), patch.object(service_dispatch, 'select') as select:
                before = '\n'.join(self.db.iterdump())
                with self.assertRaises(repositories.RepositoryError) as caught:
                    if route == 'admit': self.admit()
                    else: service_dispatch.tick(self.service, self.github)
                self.assertEqual(caught.exception.code, 'readiness_configuration_required')
                self.assertEqual(before, '\n'.join(self.db.iterdump()))
                select.assert_not_called()
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'missing-config-stop')
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))

    def test_last_reader_drift_and_pause_prevent_new_claims(self):
        EnforcedReadinessTests.setUp(self)
        self.health['sha'] = 'b'*40
        with self.assertRaises(repositories.RepositoryError): self.admit()
        self.health['sha'] = 'a'*40
        restore = self.snapshot()
        original = self.readers.execution_support.side_effect
        source = Path(self.config['slots'][0]['skills'][0]['path'])
        raw = source.read_bytes()
        for mutation in ('source', 'config', 'ownership', 'pause'):
            with self.subTest(mutation=mutation):
                restore()
                def drift(*args):
                    result = original(*args)
                    if mutation == 'source': source.write_bytes(raw + b'drift')
                    if mutation == 'config': self.file('readiness.json', {**self.config, 'reference': 'unreviewed'})
                    if mutation == 'ownership': self.item['reservation'] = {'issue_number': 999}; self.save()
                    if mutation == 'pause': controls.request(self.db, 'p', 'i', 'operator', 'pause', 'read-return-pause')
                    return result
                self.readers.execution_support.side_effect = drift
                try:
                    with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
                    self.assertEqual(caught.exception.code, 'iteration_paused' if mutation == 'pause' else 'readiness_inputs_changed')
                    if mutation == 'pause': self.assertTrue(controls.blocked(self.db, 'p', 'i'))
                finally: restore()

    def test_new_claim_requires_exact_configured_stage_profile_and_unused_capacity(self):
        EnforcedReadinessTests.setUp(self)
        slot = deepcopy(self.config['slots'][0])
        request = {k: slot[k] for k in ('stage', 'claim_id', 'profile', 'skills')}
        self.assertEqual(self.admit(selection=request), 'admitted')
        for key, value in (('stage', 'simplify'), ('claim_id', 'unselected'), ('profile', dict(request['profile'], name='other'))):
            changed = deepcopy(request); changed[key] = value
            with self.subTest(key=key), self.assertRaises(repositories.RepositoryError): self.admit(selection=changed)
        Path(request['profile']['home']).mkdir()
        with self.assertRaises(repositories.RepositoryError): self.admit(selection=request)

    def test_existing_assignment_run_launcher_and_current_holds_bound_effects(self):
        EnforcedReadinessTests.setUp(self)
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
        restore = self.snapshot()
        for mutation in ('revision', 'rejection'):
            with self.subTest(mutation=mutation):
                restore()
                try:
                    if mutation == 'revision':
                        self.item['requirement_changes'] = {'change': dict(kind='material', status='pending', affected=[1])}; self.save()
                    else:
                        row['review_holds'] = {'axis': {'verdict': 'reject'}}
                        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),)); self.db.commit()
                    with self.assertRaises(repositories.RepositoryError): self.admit(selection=terminal, operation='effect')
                finally: restore()

    def test_mutable_accounting_and_destinations_never_repin_immutable_policy(self):
        EnforcedReadinessTests.setUp(self)
        first = readiness_gate.policy(self.service, self.config, self.db)
        self.assertEqual(self.admit(), 'admitted')
        self.db.execute('UPDATE model_grants SET remaining=remaining-1'); self.db.commit()
        self.assertEqual(first, readiness_gate.policy(self.service, self.config, self.db))
        self.assertEqual(self.admit(), 'admitted')
        home = Path(self.config['slots'][0]['profile']['home'])
        home.write_text('mutable home observation')
        artifacts = Path(json.loads(Path(self.config['slots'][0]['launcher']).read_text())['artifacts_root'])
        (artifacts / 'new-result').write_text('mutable result')
        self.assertEqual(first, readiness_gate.policy(self.service, self.config, self.db))
        self.assertEqual(self.admit(operation='effect'), 'admitted')
        home.unlink()
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        with self.assertRaises(repositories.RepositoryError) as caught: self.admit()
        self.assertEqual(caught.exception.code, 'readiness_blocked')
        # Issued receipts are mutable only under an immutable, explicitly selected
        # verifier/path/scope policy. Exact consumer checks remain separate.
        paths = [str(self.root / name) for name in ('future-review.json', 'next-review.json')]
        verifier = self.root / 'receipt-verifier'
        verifier.write_bytes(b'explicit trusted verifier fixture'); verifier.chmod(0o700)
        self.config['runtime_receipts'] = {'scope': readiness_gate.scope_identity(self.item),
            'verifier': {'path': str(verifier), 'sha256': sha(verifier.read_bytes())},
            'receipts': {'review': {path: {'axis': 'both', 'repository_id': 7} for path in paths}}}
        self.service['dispatch_config'] = self.file('receipt-dispatch.json', {'continuation': {'authorities': {'review': paths}}})
        self.file('readiness.json', self.config); self.file('service.json', self.service)
        sealed = readiness_gate.policy(self.service, self.config, self.db)
        self.db.execute('UPDATE supervised_readiness SET policy=?', (sealed,)); self.db.commit()
        def consume(path, **kwargs):
            return readiness_gate.runtime_receipt(self.db, 'p', 'i', 'review', path, **kwargs)
        self.assertIsNone(consume(paths[0], missing_ok=True))
        def independently_verified(argv, **kwargs):
            self.assertEqual(argv, [str(verifier)])
            request = json.loads(kwargs['input'])
            self.assertEqual(request['scope'], self.config['runtime_receipts']['scope'])
            self.assertEqual(request['constraints'], {'axis': 'both', 'repository_id': 7})
            self.assertEqual(request['sha256'], sha(Path(request['path']).read_bytes()))
            return SimpleNamespace(returncode=0, stdout=json.dumps({'verified': request}).encode())
        with patch('factory_v1.readiness_receipts.subprocess.run', side_effect=independently_verified) as verify:
            for path in paths:
                Path(path).write_text(json.dumps({'operator_id': 'operator', 'exact_run': path})); Path(path).chmod(0o600)
                self.assertEqual(readiness_gate.policy(self.service, self.config, self.db), sealed)
                self.assertEqual(consume(path)['exact_run'], path)
                self.assertEqual(consume(path)['exact_run'], path)
            with self.assertRaises(repositories.RepositoryError):
                readiness_gate.runtime_receipt(self.db, 'p', 'i', 'merge', paths[0])
            self.assertEqual(verify.call_count, 4)  # no cached-verification consumption
            self.assertEqual(self.admit(operation='effect'), 'admitted')
            self.assertEqual(verify.call_count, 6)
            accepted = Path(paths[1]).read_bytes()
            authority = self.readers.github_authority.side_effect
            def late_receipt_change(*args):
                result = authority(*args)
                Path(paths[1]).write_text('{"operator_id":"operator","exact_run":"late-change"}')
                return result
            with patch.object(self.readers.github_authority, 'side_effect', late_receipt_change):
                with self.assertRaises(repositories.RepositoryError): self.admit(operation='effect')
            Path(paths[1]).write_bytes(accepted)
            Path(paths[1]).unlink()
            with self.assertRaises(repositories.RepositoryError): consume(paths[1], missing_ok=True)
            Path(paths[1]).write_bytes(accepted); Path(paths[1]).chmod(0o600)
            Path(paths[0]).write_text(json.dumps({'operator_id': 'operator', 'exact_run': 'replacement'}))
            with self.assertRaisesRegex(repositories.RepositoryError, 'immutable'): consume(paths[0])
            # Trusted verifier refusal cannot be replaced by path/private-file checks.
            verify.side_effect = None; verify.return_value = SimpleNamespace(returncode=0, stdout=b'{}')
            with self.assertRaises(repositories.RepositoryError): consume(paths[1])
        for field in ('scope', 'constraints', 'path', 'verifier'):
            changed = deepcopy(self.config)
            if field == 'scope': changed['runtime_receipts']['scope'] = '0' * 64
            elif field == 'constraints': changed['runtime_receipts']['receipts']['review'][paths[0]]['axis'] = 'none'
            elif field == 'path': changed['runtime_receipts']['receipts']['review'][str(self.root / 'unapproved.json')] = {'axis': 'both'}
            else: changed['runtime_receipts']['verifier']['sha256'] = '0' * 64
            with self.subTest(field=field):
                try: changed_policy = readiness_gate.policy(self.service, changed, self.db)
                except repositories.RepositoryError: pass
                else: self.assertNotEqual(changed_policy, sealed)
        verifier.write_bytes(b'drifted verifier')
        with self.assertRaises(repositories.RepositoryError): consume(paths[1])

    def test_real_model_admission_preserves_allowance_quota_and_final_no_forward(self):
        EnforcedReadinessTests.setUp(self)
        self.spending_records()
        restore = self.snapshot()
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
        restore()
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

    def test_policy_selection_requires_pause_and_never_resumes(self):
        EnforcedReadinessTests.setUp(self)
        with self.assertRaises(repositories.RepositoryError): readiness_gate.seal(self.db, Path(self.service_path))
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'select-policy')
        readiness_gate.seal(self.db, Path(self.service_path))
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))
        self.assertFalse(service.inspect_readiness(self.service, running=True)['iteration_runnable'])

    def test_registered_repair_exception_is_exact_and_preserves_other_holds(self):
        EnforcedReadinessTests.setUp(self)
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

    def test_delivered_retirement_resolution_preserves_unrelated_holds(self):
        EnforcedReadinessTests.setUp(self)
        slot = self.config['slots'][0]
        row = self.record(slot, 'complete')
        row['merge'] = dict(status='delivered', close_allowed=True, failure=None)
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),)); self.db.commit()
        self.item.update(reservation={'issue_number': 1}, restart_reconciliation=dict(status='blocked', observations={},
            blockers=[dict(boundary='tracker', code='reservation_conflict')]))
        self.save()
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        resolution = readiness_gate.retirement_resolution(self.db, self.service)
        self.assertIsNotNone(resolution)
        self.assertEqual(self.admit(operation='effect', resolution=resolution), 'admitted')
        with self.assertRaises(repositories.RepositoryError): self.admit(operation='effect')
        self.item['restart_reconciliation']['blockers'].append(dict(boundary='requirements', code='requirements_held')); self.save()
        with self.assertRaises(repositories.RepositoryError): self.admit(operation='effect', resolution=resolution)

    def test_unknown_operations_never_bypass_admission(self):
        EnforcedReadinessTests.setUp(self)
        for operation in ('repair', '', None, {}, []):
            with self.subTest(operation=operation), self.assertRaises(repositories.RepositoryError): self.admit(operation=operation)

    def test_private_host_client_selection_never_adopts_unsupported_route(self):
        EnforcedReadinessTests.setUp(self)
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
