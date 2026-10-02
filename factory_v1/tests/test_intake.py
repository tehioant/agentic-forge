import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class IntakeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'state.sqlite'
        self.request = {
            'project_id': 'approved-product',
            'iteration_id': 'milestone-1',
            'repository': 'example/approved-product',
            'idea': 'A product explicitly requested by its operator',
            'approval': {'operator_id': '42', 'reference': 'operator-message-101'},
            'origin': {
                'platform': 'discord', 'chat_id': '123', 'thread_id': '123',
                'parent_chat_id': '456', 'scope_id': '789',
            },
        }

    def invoke(self, *arguments):
        return subprocess.run(
            [sys.executable, '-m', 'factory_v1', '--state', str(self.state),
             '--operator-id', '42', *arguments],
            text=True, capture_output=True, timeout=15,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        )

    def register(self, request=None):
        path = self.root / 'request.json'
        path.write_text(json.dumps(self.request if request is None else request))
        return self.invoke('register', '--request', str(path))

    def test_approved_iteration_survives_a_fresh_controller_process(self):
        registered = self.register()
        self.assertEqual(registered.returncode, 0, registered.stderr)
        first = json.loads(registered.stdout)
        self.assertEqual(first['status'], 'active')
        inspected = self.invoke('inspect', '--project', 'approved-product',
                                '--iteration', 'milestone-1')
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout), first)
        self.assertEqual(first['repository'], 'example/approved-product')
        self.assertEqual(first['approval'], self.request['approval'])
        self.assertEqual(first['origin'], self.request['origin'])
        self.assertEqual(first['stage'], 'intake_registered')


    def test_unapproved_or_ambiguous_intake_is_refused_without_creating_state(self):
        import copy
        unsafe = []
        item = copy.deepcopy(self.request)
        item.pop('approval')
        unsafe.append(item)
        item = copy.deepcopy(self.request)
        item['approval']['operator_id'] = '99'
        unsafe.append(item)
        for repository in ['https://github.com/example/product', 'example', 'example/../other', ' example/product']:
            item = copy.deepcopy(self.request)
            item['repository'] = repository
            unsafe.append(item)
        item = copy.deepcopy(self.request)
        item['origin']['thread_id'] = '999'
        unsafe.append(item)
        item = copy.deepcopy(self.request)
        item['origin'].pop('parent_chat_id')
        unsafe.append(item)
        item = copy.deepcopy(self.request)
        item['origin']['platform'] = 'unknown'
        unsafe.append(item)
        item = copy.deepcopy(self.request)
        item['approval']['reference'] = ''
        unsafe.append(item)
        for item in unsafe:
            with self.subTest(request=item):
                refused = self.register(item)
                self.assertEqual(refused.returncode, 2)
                self.assertEqual(json.loads(refused.stderr)['error'], 'invalid_intake')
                self.assertFalse(self.state.exists())


    def test_repeated_registration_is_idempotent_and_conflicting_replay_is_refused(self):
        first = self.register()
        repeated = self.register()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout), json.loads(first.stdout))
        changed = dict(self.request, repository='example/another-product')
        conflict = self.register(changed)
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(json.loads(conflict.stderr)['error'], 'intake_conflict')
        inspected = self.invoke('inspect', '--project', 'approved-product', '--iteration', 'milestone-1')
        self.assertEqual(json.loads(inspected.stdout), json.loads(first.stdout))


    def test_concurrent_approved_products_keep_one_active_and_one_paused(self):
        from concurrent.futures import ThreadPoolExecutor
        paths = []
        for index in [1, 2]:
            item = dict(self.request, project_id=f'product-{index}', repository=f'example/product-{index}')
            path = self.root / f'intake-{index}.json'
            path.write_text(json.dumps(item))
            paths.append(path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda path: self.invoke('register', '--request', str(path)), paths))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        states = [json.loads(result.stdout) for result in results]
        self.assertEqual(sorted(state['status'] for state in states), ['active', 'paused'])
        for state in states:
            inspected = self.invoke('inspect', '--project', state['project_id'], '--iteration', 'milestone-1')
            self.assertEqual(json.loads(inspected.stdout), state)


    def test_public_correlations_survive_restart_without_claiming_a_worker_run(self):
        request = dict(self.request, work_item_number=5)
        registered = self.register(request)
        self.assertEqual(registered.returncode, 0, registered.stderr)
        item = json.loads(registered.stdout)
        correlation = item['correlation']
        self.assertEqual(correlation['project_id'], item['project_id'])
        self.assertEqual(correlation['iteration_id'], item['iteration_id'])
        self.assertEqual(correlation['stage'], item['stage'])
        self.assertEqual(correlation['work_item'], {'repository': request['repository'], 'issue_number': 5})
        self.assertTrue(correlation['control_run_id'])
        self.assertTrue(correlation['evidence_id'])
        self.assertIsNone(correlation['worker_run_id'])
        self.assertFalse(item['execution_allowed'])
        self.assertEqual(item['evidence']['kind'], 'approved_intake')
        self.assertEqual(item['evidence']['id'], correlation['evidence_id'])
        inspected = self.invoke('inspect', '--project', item['project_id'], '--iteration', item['iteration_id'])
        self.assertEqual(json.loads(inspected.stdout), item)


    def test_missing_iteration_inspection_is_read_only_and_reports_not_found(self):
        result = self.invoke('inspect', '--project', 'unknown', '--iteration', 'unknown')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'not_found')
        self.assertFalse(self.state.exists())
        self.assertEqual(self.register().returncode, 0)
        before = self.state.read_bytes()
        result = self.invoke('inspect', '--project', 'unknown', '--iteration', 'unknown')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'not_found')
        self.assertEqual(self.state.read_bytes(), before)


    def test_malformed_or_missing_intake_has_a_structured_error_and_no_state(self):
        path = self.root / 'malformed.json'
        for content in ['{invalid', '{"approval":{},"approval":{}}']:
            with self.subTest(content=content):
                path.write_text(content)
                result = self.invoke('register', '--request', str(path))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_intake')
                self.assertFalse(self.state.exists())
        path.unlink()
        result = self.invoke('register', '--request', str(path))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'io_error')
        self.assertFalse(self.state.exists())


    @unittest.skipUnless(os.name == 'posix', 'POSIX permission contract')
    def test_new_state_is_private_even_when_the_caller_umask_is_permissive(self):
        import stat
        previous = os.umask(0)
        try:
            result = self.register()
        finally:
            os.umask(previous)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)


    def test_adding_a_work_item_to_a_registered_intake_is_a_conflict_not_a_crash(self):
        first = self.register()
        self.assertEqual(first.returncode, 0, first.stderr)
        result = self.register(dict(self.request, work_item_number=5))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'intake_conflict')


    def test_removing_a_work_item_from_a_registered_intake_is_a_conflict(self):
        first = self.register(dict(self.request, work_item_number=5))
        self.assertEqual(first.returncode, 0, first.stderr)
        result = self.register()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'intake_conflict')


if __name__ == '__main__':
    unittest.main()
