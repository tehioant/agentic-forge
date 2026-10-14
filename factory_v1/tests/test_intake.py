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

    def invoke(self, *arguments, state=None):
        return subprocess.run(
            [sys.executable, '-m', 'factory_v1', '--state', str(self.state if state is None else state),
             '--operator-id', '42', *arguments],
            text=True, capture_output=True, timeout=15,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        )

    def register(self, request=None):
        path = self.root / 'request.json'
        path.write_text(json.dumps(self.request if request is None else request))
        return self.invoke('register', '--request', str(path))

































# Legacy helpers above remain for untouched consumers; focused tests never call them.
import copy
import io
import sqlite3
from contextlib import closing, redirect_stdout
from unittest import mock
from factory_v1 import __main__ as intake
from factory_v1.errors import IntakeError


class IntakeComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'state.sqlite'
        self.request = {
            'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
            'idea': 'Explicit operator idea',
            'approval': {'operator_id': '42', 'reference': 'message-1'},
            'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123',
                       'parent_chat_id': '456', 'scope_id': '789'},
        }
        for target in ('socket.socket', 'subprocess.Popen'):
            patcher = mock.patch(target, side_effect=AssertionError('external creation denied'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def register_direct(self, request=None):
        request = copy.deepcopy(self.request if request is None else request)
        intake.validate_intake(request, '42')
        with closing(sqlite3.connect(self.state)) as db, db:
            return intake.register_iteration(db, request)

    def stored(self, project='product'):
        with closing(sqlite3.connect(self.state.as_uri() + '?mode=ro', uri=True)) as db:
            return intake.read_iteration(db, project, 'm1')

    def test_approved_intake_reopens_with_exact_provenance_and_no_execution(self):
        first = self.register_direct(dict(self.request, work_item_number=5))
        self.assertEqual(self.stored(), first)
        for field in ('repository', 'approval', 'origin'):
            self.assertEqual(first[field], self.request[field])
        self.assertEqual(first['stage'], 'intake_registered')
        self.assertFalse(first['execution_allowed'])
        correlation = first['correlation']
        self.assertEqual(correlation['work_item'], {'repository': 'example/product', 'issue_number': 5})
        self.assertEqual(correlation['project_id'], 'product')
        self.assertEqual(correlation['iteration_id'], 'm1')
        self.assertEqual(correlation['stage'], first['stage'])
        self.assertTrue(correlation['control_run_id'])
        self.assertIsNone(correlation['worker_run_id'])
        self.assertEqual(correlation['evidence_id'], first['evidence']['id'])
        self.assertEqual(first['evidence']['kind'], 'approved_intake')

    def test_invalid_approval_scope_origin_and_owner_are_refused_before_storage(self):
        cases = [dict(self.request, approval=value) for value in (None, {},
                 {'operator_id': '99', 'reference': 'x'}, {'operator_id': '42', 'reference': ''})]
        cases += [dict(self.request, repository=value) for value in
                  ('https://github.com/example/product', 'example', 'example/../other',
                   ' example/product', 'owner-/repo', 'owner--name/repo', '-owner/repo', 'x' * 40 + '/repo')]
        cases += [dict(self.request, origin=value) for value in
                  (None, dict(self.request['origin'], thread_id='999'),
                   dict(self.request['origin'], platform='unknown'),
                   {k: v for k, v in self.request['origin'].items() if k != 'parent_chat_id'})]
        cases += [dict(self.request, work_item_number=v) for v in (True, 0, -1, '5')]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(IntakeError) as raised:
                self.register_direct(request)
            self.assertEqual(raised.exception.code, 'invalid_intake')
            self.assertFalse(self.state.exists())

    def test_valid_owner_boundaries_keep_exact_case_and_bytes(self):
        for index, owner in enumerate(('x', 'Owner-Name', 'a-b-c', 'x' * 39)):
            request = dict(self.request, project_id=f'product-{index}', repository=owner + '/Product.Name_1')
            record = self.register_direct(request)
            self.assertEqual(self.stored(request['project_id']), record)
            self.assertEqual(record['repository'], request['repository'])

    def test_idempotent_registration_and_all_optional_scope_conflicts(self):
        first = self.register_direct()
        self.assertEqual(self.register_direct(), first)
        before = self.state.read_bytes()
        for changed in (dict(self.request, repository='example/other'),
                        dict(self.request, work_item_number=5),
                        dict(self.request, repository_intent={'mode': 'new', 'marker': 'approved'})):
            with self.assertRaises(IntakeError) as raised:
                self.register_direct(changed)
            self.assertEqual(raised.exception.code, 'intake_conflict')
            self.assertEqual(self.state.read_bytes(), before)
        for key, value in (('work_item_number', 5), ('repository_intent', {'mode': 'new', 'marker': 'approved'})):
            self.state = self.root / (key + '.sqlite')
            original = self.register_direct(dict(self.request, **{key: value}))
            with self.assertRaises(IntakeError) as raised:
                self.register_direct()
            self.assertEqual(raised.exception.code, 'intake_conflict')
            self.assertEqual(self.stored(), original)

    def test_serialized_products_have_one_active_iteration(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(lambda i: self.register_direct(dict(self.request,
                project_id=f'product-{i}', repository=f'example/product-{i}')), (1, 2)))
        self.assertEqual(sorted(r['status'] for r in records), ['active', 'paused'])
        for record in records:
            self.assertEqual(self.stored(record['project_id']), record)

    def test_missing_inspection_is_read_only(self):
        self.register_direct()
        before = self.state.read_bytes()
        self.assertIsNone(self.stored('unknown'))
        self.assertEqual(self.state.read_bytes(), before)

    def test_duplicate_and_parser_limit_json_are_not_admitted(self):
        with self.assertRaises(IntakeError):
            json.loads('{"approval":{},"approval":{}}', object_pairs_hook=intake.unique_fields)
        for raw in ('{invalid', '{"n":' + '9' * 5000 + '}', '[' * 1200 + '0' + ']' * 1200):
            try:
                value = json.loads(raw, object_pairs_hook=intake.unique_fields)
            except (ValueError, RecursionError):
                pass
            else:
                with self.assertRaises(IntakeError):
                    intake.validate_intake(value, '42')
        self.assertFalse(self.state.exists())

    def test_cli_input_and_persistence_refusals_in_process_without_journey(self):
        # Parser/error translation has no lower-level function; no successful CLI lifecycle.
        path = self.root / 'request.json'
        path.write_text(json.dumps(self.request))
        for state in ('', ':memory:'):
            for command in (['register', '--request', str(path)],
                            ['inspect', '--project', 'product', '--iteration', 'm1']):
                with mock.patch.object(sys, 'argv', ['factory', '--state', state, '--operator-id', '42', *command]), redirect_stdout(io.StringIO()), self.assertRaises(IntakeError) as raised:
                    intake.main()
                self.assertEqual(raised.exception.code, 'invalid_state')
        path.unlink()
        with mock.patch.object(sys, 'argv', ['factory', '--state', str(self.state), '--operator-id', '42', 'register', '--request', str(path)]), self.assertRaises(OSError):
            intake.main()
        with mock.patch.object(sys, 'argv', ['factory', '--state', str(self.state), '--operator-id', '42', 'inspect', '--project', 'unknown', '--iteration', 'unknown']), self.assertRaises(IntakeError) as raised:
            intake.main()
        self.assertEqual(raised.exception.code, 'not_found')
        self.assertFalse(self.state.exists())

    def test_cli_private_state_creation_seam_under_permissive_umask(self):
        # Only the file-opening wrapper owns this permission contract.
        path = self.root / 'request.json'
        path.write_text(json.dumps(self.request))
        old = os.umask(0)
        try:
            with mock.patch.object(sys, 'argv', ['factory', '--state', str(self.state), '--operator-id', '42', 'register', '--request', str(path)]), mock.patch.object(intake, 'register_iteration', return_value={}), redirect_stdout(io.StringIO()):
                intake.main()
        finally:
            os.umask(old)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_repository_guard_sensitivity(self):
        request = dict(self.request, repository='https://github.com/example/product')
        with self.assertRaises(IntakeError):
            intake.validate_intake(request, '42')
        with mock.patch.object(intake, 'valid_repository', return_value=True):
            intake.validate_intake(request, '42')
