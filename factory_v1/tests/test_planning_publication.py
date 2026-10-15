"""Direct initial publisher/native guards; all GitHub responses are labeled fixtures."""
import base64
import copy
import json
import unittest
from unittest.mock import patch

from factory_v1 import planning, planning_publication, publication, repositories
from factory_v1.conversation_binding import ConversationBinding
from factory_v1.tests import test_conversation_binding


class PlanningPublicationTests(unittest.TestCase):
    def setUp(self):
        test_conversation_binding.ConversationBindingTests.setUp(self)
        self.item.pop('handoff'); self.item.pop('ticket_work')
        self.native = {'assignment_id': 'origin-assignment', 'session_id': 'native-origin', 'task_id': 'task',
            'turn_id': 'turn', 'run_id': 'fixture-native-run', 'origin': self.item['origin'], 'operator_id': '42',
            'binding_reference': self.config['reference'], 'sources': self.selected, 'adaptation': planning.INSTRUCTIONS,
            'observations': [{'reference': 'native-turn:turn', 'role': 'user', 'content': 'Use Python; keep sharing pending.'}],
            'loads': ['grill-me', 'grilling', 'to-spec'], 'sufficient': True, 'rationale': 'Finite local notes only.'}
        self.item['planning']['native_execution'] = copy.deepcopy(self.native)
        self.fixture.persist()
        self.args = {'assignment_id': 'origin-assignment', 'run_id': 'fixture-native-run',
            'documents': {'milestone.md': ''.join('## ' + s + '\n\nFinite local notes.\n\n' for s in planning.SECTIONS),
                          'vision.md': 'Sharing remains a future decision.',
                          'provenance.md': 'native-turn:turn\nUse Python; keep sharing pending.\n'},
            'answers': [{'reference': 'native-turn:turn', 'answer': 'Use Python; keep sharing pending.'}],
            'work': [{'skill': name, 'result': 'Fixture native model work claim, not host attestation.'}
                     for name in self.native['loads']], 'reference': 'fixture-tool-output'}
        self.auth = {'operator_id': '42', 'reference': 'fixture-host-approval', 'repository': 'example/product',
            'repository_id': 123, 'private': True, 'api_base': 'https://api.github.com', 'bearer': 'fixture-only',
            'assignment_id': 'origin-assignment', 'session_id': 'native-origin', 'parent': 'b' * 40}
        self.auth_path = self.fixture.root / 'publication.json'
        self.auth_path.write_text(json.dumps(self.auth)); self.auth_path.chmod(0o600)
        self.binding.publication_config = str(self.auth_path)
        self.repo = {'id': 123, 'full_name': 'example/product', 'name': 'product', 'owner': {'login': 'example'}, 'private': True}
        self.records = self.initial_records()
        self.writes = []
        self.github = self.fixture.github
        self.github.request.side_effect = self.request
        self.github_patch = patch.object(repositories, 'GitHub', return_value=self.github)
        self.github_patch.start(); self.addCleanup(self.github_patch.stop)
        self.fail_after = None
        self.fail_before = None
        self.drift = None

    def initial_records(self):
        return {'': self.repo, '/git/commits/' + 'b' * 40: {'sha': 'b' * 40, 'tree': {'sha': 'c' * 40}},
                '/git/trees/' + 'c' * 40 + '?recursive=1': {'sha': 'c' * 40, 'truncated': False, 'tree': []}}

    def request(self, path, body=None, method=None):
        prefix = '/repos/example/product'
        self.assertTrue(path.startswith(prefix))
        suffix = path[len(prefix):]
        if body is None:
            result = self.records.get(suffix)
            if suffix.startswith('/issues?state=all'):
                result = [r for p, r in self.records.items() if p.startswith('/issues/')]
            if self.drift:
                result = self.drift(suffix, copy.deepcopy(result))
            return copy.deepcopy(result)
        self.assertEqual(method, 'POST')
        journal = self.fixture.stored()['planning']['host_publication']
        self.assertTrue(any(e['path'] == suffix and e['payload'] == body for e in journal['effects'].values()))
        self.writes.append((suffix, copy.deepcopy(body)))
        if self.fail_before == suffix:
            raise repositories.RepositoryError('fixture-response-lost', 'Fixture unknown write outcome.')
        if suffix == '/git/blobs':
            sha = publication.object_sha('blob', base64.b64decode(body['content']))
            result = {'sha': sha, **body}; self.records['/git/blobs/' + sha] = result
        elif suffix == '/git/trees':
            result = {'sha': 'd' * 40}
            self.records['/git/trees/' + 'd' * 40 + '?recursive=1'] = {**result, 'truncated': False,
                'tree': [{**r, 'size': 100} for r in body['tree']]}
            for row in body['tree']:
                blob = self.records['/git/blobs/' + row['sha']]
                self.records['/contents/' + row['path'] + '?ref=' + 'e' * 40] = {
                    'type': 'file', 'path': row['path'], 'encoding': 'base64', 'sha': row['sha'], 'content': blob['content']}
        elif suffix == '/git/commits':
            result = {'sha': 'e' * 40, 'tree': {'sha': body['tree']}, 'parents': [{'sha': s} for s in body['parents']]}
            self.records['/git/commits/' + 'e' * 40] = result
        elif suffix == '/git/refs':
            result = {'ref': body['ref'], 'object': {'type': 'commit', 'sha': body['sha']}}
            self.records['/git/ref/' + body['ref'][5:]] = result
        elif suffix == '/issues':
            result = {'number': 7, 'id': 1007, 'title': body['title'], 'body': body['body'], 'state': 'open',
                'labels': [{'name': n} for n in body['labels']], 'html_url': 'https://github.com/example/product/issues/7',
                'repository_url': 'https://api.github.com/repos/example/product'}
            self.records['/issues/7'] = result
        else:
            raise AssertionError(('unapproved fixture write', suffix, body))
        if self.fail_after == suffix:
            raise repositories.RepositoryError('fixture-response-lost', 'Fixture lost response after effect.')
        return copy.deepcopy(result)

    def publish(self):
        return json.loads(self.binding.publish(self.args, session_id='native-origin'))

    def execution(self):
        return {'session_id': 'native-origin', 'native': copy.deepcopy(self.native),
                'answers': copy.deepcopy(self.args['answers']), 'model_work': copy.deepcopy(self.args['work']),
                'reference': self.args['reference']}

    def seed_verified_publication(self):
        """Independent completion input; no publisher execution constructs it."""
        path = self.item['planning']['docs_path']
        commit = 'e' * 40
        references = {}
        for name, content in self.args['documents'].items():
            raw = content.encode()
            blob = publication.object_sha('blob', raw)
            document_path = path + '/' + name
            self.records['/contents/' + document_path + '?ref=' + commit] = {
                'type': 'file', 'path': document_path, 'encoding': 'base64', 'sha': blob,
                'content': base64.b64encode(raw).decode()}
            references[name] = {'path': document_path, 'blob': blob,
                'url': 'https://github.com/example/product/blob/' + commit + '/' + document_path}
        self.records['/git/commits/' + commit] = {'sha': commit}
        self.records['/issues/7'] = {'number': 7, 'id': 1007, 'state': 'open',
            'title': 'Factory initial specification origin-assignment', 'body': self.args['documents']['milestone.md'],
            'labels': [{'name': 'ready-for-agent'}], 'html_url': 'https://github.com/example/product/issues/7',
            'repository_url': 'https://api.github.com/repos/example/product'}
        handoff = {'repository': 'example/product', 'commit': commit,
            'issue': {'number': 7, 'id': 1007, 'url': 'https://github.com/example/product/issues/7'},
            'documents': references, 'api_base': self.github.base}
        record = {'scope': {k: v for k, v in self.auth.items() if k != 'bearer'},
            'execution': self.execution(), 'documents': copy.deepcopy(self.args['documents']),
            'branch': 'factory/planning/m1/origin-assignment', 'path': path, 'effects': {},
            'issue': {'number': 7, 'id': 1007}, 'commit': commit, 'handoff': handoff, 'status': 'verified'}
        self.item['planning']['host_publication'] = record
        self.fixture.persist()
        return copy.deepcopy(record)

    def publish_only(self):
        # Completion is a separate component; do not feed real publisher output into it.
        completed = {'planning': {'status': 'complete'}, 'stage': 'planning_complete', 'handoff': {'commit': 'e' * 40}}
        with patch.object(planning, 'complete', return_value=completed):
            self.publish()
        return self.fixture.stored()['planning']['host_publication']

    def test_real_publisher_functions_complete_durable_exact_mock_readbacks(self):
        self.publish_only()
        record = self.fixture.stored()['planning']['host_publication']
        independent = self.seed_verified_publication()
        with patch.object(planning_publication, 'publish', return_value=independent):
            output = self.publish()
        self.assertEqual(output['status'], 'complete')
        stored = self.fixture.stored()
        self.assertEqual(record['status'], 'verified')
        self.assertEqual(stored['handoff']['commit'], 'e' * 40)
        self.assertEqual(record['documents'], self.args['documents'])
        self.assertEqual(record['execution']['native'], self.native)
        self.assertEqual(len(self.writes), 7)
        self.assertEqual({p for p, _ in self.writes}, {'/git/blobs', '/git/trees', '/git/commits', '/git/refs', '/issues'})
        self.assertEqual(record['branch'], 'factory/planning/m1/origin-assignment')
        self.assertFalse(any('main' in json.dumps(body) or 'force' in body for _, body in self.writes))
        from factory_v1.service_dispatch import select
        self.assertEqual(select(stored, [], {}), ('synthesize', None))

    def test_repeated_native_hook_survives_tool_loop_and_new_adapter_instance(self):
        self.item['planning'].pop('native_execution'); self.fixture.persist()
        kwargs = {'task_id': 'task', 'turn_id': 'turn', 'user_message': 'Use Python.', 'sender_id': '42',
                  'conversation_history': [{'role': 'assistant', 'content': 'Which language?'}]}
        self.binding.context('native-origin', **kwargs)
        self.binding.sufficient(self.judgment, session_id='native-origin')
        before = self.fixture.stored()['planning']['native_execution']
        self.binding.context('native-origin', **kwargs)
        restarted = ConversationBinding(self.ctx, self.config, self.github)
        restarted.context('native-origin', **kwargs)
        self.assertEqual(self.fixture.stored()['planning']['native_execution'], before)
        self.assertEqual(before['loads'], ['grill-me', 'grilling', 'to-spec'])
        self.assertTrue(before['sufficient'])
        self.assertEqual(len(self.ctx.dispatch_tool.call_args_list), 3)

    def test_load_only_forged_answers_wrong_run_and_arbitrary_targets_refused(self):
        for mutate in (lambda a: a.update(run_id='forged'), lambda a: a['answers'][0].update(answer='Invented'),
                       lambda a: a.update(repository='other/repo'), lambda a: a.update(branch='main'),
                       lambda a: a.update(documents={**a['documents'], 'provenance.md': 'Erased answers'})):
            original = copy.deepcopy(self.args)
            mutate(self.args)
            with self.assertRaises(repositories.RepositoryError): self.publish()
            self.args = original
        self.item['planning']['native_execution']['loads'] = ['to-spec']; self.fixture.persist()
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertEqual(self.writes, [])

    def test_native_bound_legacy_public_completion_cannot_bypass_publisher(self):
        with self.assertRaises(repositories.RepositoryError) as error:
            planning.complete(self.fixture.database, self.item, {'sufficient': True}, self.github)
        self.assertEqual(error.exception.code, 'execution_required')
        self.assertEqual(self.writes, [])

    def test_uncertain_commit_never_repeats_write(self):
        self.fail_after = '/git/commits'
        with self.assertRaises(repositories.RepositoryError): self.publish()
        before = copy.deepcopy(self.writes)
        self.fail_after = None
        with self.assertRaises(repositories.RepositoryError) as error: self.publish()
        self.assertEqual(error.exception.code, 'publication_uncertain')
        self.assertEqual(self.writes, before)
        self.assertNotEqual(self.fixture.stored()['planning']['status'], 'complete')

    def test_lost_ref_and_issue_responses_reconcile_exact_effect_without_duplicate(self):
        for suffix in ('/git/refs', '/issues'):
            with self.subTest(suffix=suffix):
                # Independent minimal fixture for each single boundary.
                self.fail_after = suffix
                with self.assertRaises(repositories.RepositoryError): self.publish_only()
                self.fail_after = None
                output = self.publish_only()
                self.assertEqual(output['status'], 'verified')
                self.assertEqual(self.fixture.stored()['planning']['host_publication']['status'], 'verified')
                self.assertEqual(sum(p == suffix for p, _ in self.writes), 1)
                if suffix == '/git/refs':
                    # Next subcase reuses no admitted publication or native completion.
                    self.item['planning'].pop('host_publication', None)
                    self.fixture.persist(); self.records = self.initial_records(); self.writes = []

    def test_repository_privacy_or_authority_drift_before_write_refuses(self):
        self.repo['private'] = False
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertEqual(self.writes, [])
        self.repo['private'] = True
        self.auth['assignment_id'] = 'other'
        self.auth_path.write_text(json.dumps(self.auth))
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertEqual(self.writes, [])

    def test_exact_document_or_issue_readback_drift_never_completes(self):
        self.drift = lambda path, result: ({**result, 'body': 'wrong'} if path == '/issues/7' else result)
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertNotEqual(self.fixture.stored()['planning']['status'], 'complete')
        self.assertNotIn('handoff', self.fixture.stored())

    def test_tree_cannot_smuggle_changes_outside_fixed_docs(self):
        def drift(path, result):
            if path == '/git/trees/' + 'd' * 40 + '?recursive=1':
                result['tree'].append({'path': 'outside.py', 'mode': '100644', 'type': 'blob', 'sha': 'f' * 40})
            return result
        self.drift = drift
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertFalse(any(p == '/git/commits' for p, _ in self.writes))

    def test_pause_at_last_prewrite_read_retains_intent_but_sends_nothing(self):
        def drift(path, result):
            journal = self.fixture.stored()['planning'].get('host_publication', {})
            if path == '' and journal.get('effects'):
                self.drift = None
                self.fixture.pause()
            return result
        self.drift = drift
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertEqual(self.writes, [])
        self.assertTrue(self.fixture.stored()['planning']['host_publication']['effects'])
        self.assertNotEqual(self.fixture.stored()['planning']['status'], 'complete')

    def test_host_authority_revoked_during_final_completion_read_is_not_admitted(self):
        record = self.seed_verified_publication()
        def drift(path, result):
            if path == '/issues/7' and self.fixture.stored()['planning']['host_publication'].get('status') == 'verified':
                changed = {**self.auth, 'reference': 'changed-authority'}
                self.auth_path.write_text(json.dumps(changed))
            return result
        self.drift = drift
        with patch.object(planning_publication, 'publish', return_value=record):
            with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertEqual(self.fixture.stored()['planning']['host_publication']['status'], 'verified')
        self.assertNotEqual(self.fixture.stored()['planning']['status'], 'complete')
        self.assertNotIn('handoff', self.fixture.stored())

    def test_authorization_permissions_and_missing_host_context_refused(self):
        self.auth_path.chmod(0o644)
        with self.assertRaises(repositories.RepositoryError): planning_publication.configuration(self.auth_path, '42')
        self.item['planning'].pop('native_execution'); self.fixture.persist()
        self.binding.context('native-origin')
        self.binding.sufficient(self.judgment, session_id='native-origin')
        with self.assertRaises(repositories.RepositoryError): self.publish()
        self.assertEqual(self.writes, [])
