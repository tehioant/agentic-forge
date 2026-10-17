import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_registration_preserves_approved_provenance(self):
        repositories = ('example/product', 'x/Product.Name_1', 'Owner-Name/Product.Name_1',
                        'a-b-c/Product.Name_1', 'x' * 39 + '/Product.Name_1')
        for index, repository in enumerate(repositories):
            with self.subTest(repository=repository):
                self.state = self.root / f'approved-{index}.sqlite'
                request = dict(self.request, repository=repository, work_item_number=5)
                first = self.register_direct(request)
                self.assertEqual(self.stored(), first)
                for field in ('repository', 'approval', 'origin'):
                    self.assertEqual(first[field], request[field])
                self.assertEqual(first['stage'], 'intake_registered')
                self.assertFalse(first['execution_allowed'])
                correlation = first['correlation']
                self.assertEqual(correlation['work_item'], {'repository': repository, 'issue_number': 5})
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
            if request['repository'] == 'https://github.com/example/product':
                with mock.patch.object(intake, 'valid_repository', return_value=True):
                    intake.validate_intake(request, '42')


    def test_intake_replay_and_inspection_are_read_only(self):
        first = self.register_direct()
        self.assertEqual(self.register_direct(), first)
        before = self.state.read_bytes()
        self.assertIsNone(self.stored('unknown'))
        self.assertEqual(self.state.read_bytes(), before)
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
        from factory_v1 import controls, repositories
        with closing(sqlite3.connect(self.state)):
            pass
        for command in ('pause', 'emergency-stop', 'resume', 'inspect-control'):
            operation = 'inspect' if command == 'inspect-control' else 'resume' if command == 'resume' else 'request'
            args = ['factory', '--state', str(self.state), '--operator-id', '42', command,
                    '--project', 'product', '--iteration', 'm1']
            if command != 'inspect-control':
                args += ['--reference', 'operator-message']
            if command == 'resume':
                args += ['--api-base', 'https://api.github.com']
            with self.subTest(command=command), mock.patch.object(sys, 'argv', args), \
                    mock.patch.object(controls, operation, autospec=True, return_value={'command': command}) as run, \
                    mock.patch.object(repositories, 'GitHub', autospec=True) as github, redirect_stdout(io.StringIO()) as out:
                intake.main()
                self.assertEqual(json.loads(out.getvalue()), {'command': command})
                positional = run.call_args.args
                self.assertEqual(positional[1:4], ('product', 'm1', '42'))
                if command in ('pause', 'emergency-stop'):
                    self.assertEqual(positional[4:], (command, 'operator-message'))
                elif command == 'resume':
                    self.assertEqual(positional[4:], ('operator-message', github.return_value))
                    github.assert_called_once_with('https://api.github.com', 'credential-blind-controller', 10)
                else:
                    self.assertEqual(len(positional), 4)
                    github.assert_not_called()

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
