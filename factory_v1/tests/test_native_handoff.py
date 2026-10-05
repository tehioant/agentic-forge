"""Issue #12 handoff regressions; local tests, not pinned-image acceptance."""
import ast
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from factory_v1 import assignments, simplification


class NativeHandoffTests(unittest.TestCase):
    def decode(self, content):
        value = json.loads(content)
        if isinstance(value, dict) and value.get('format') == 'factory-json-chunks-v1':
            self.assertEqual(value['decode'], "json.loads(''.join(chunks))")
            return json.loads(''.join(value['chunks']))
        return value

    def assert_readable(self, artifact):
        content = artifact['content']
        self.assertGreater(len(content.splitlines()), 1)
        self.assertLessEqual(max(map(len, content.splitlines())), 2000)
        self.assertEqual(artifact['sha256'], hashlib.sha256(content.encode()).hexdigest())

    def test_nested_real_output_and_artifact_strings_preserve_exact_values(self):
        # Exact retained controller output from /inputs/live-evidence.json. Keep the
        # regression portable after the immutable review inputs are unmounted.
        records = [{'command': 'PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -v',
                    'exit_code': 0, 'stdout': '', 'output_truncated': False,
                    'stderr': 'test_ordinary_name (test_hello.GreetTests.test_ordinary_name) ... ok\n'
                              'test_preserves_name_exactly (test_hello.GreetTests.test_preserves_name_exactly) ... ok\n'
                              'test_unicode_name (test_hello.GreetTests.test_unicode_name) ... ok\n\n'
                              '----------------------------------------------------------------------\n'
                              'Ran 3 tests in 0.000s\n\nOK\n'}]
        # Expand retained literal output into nested stress values; not live claims.
        long_output = records[0]['stderr'] * 16
        long_artifact = json.dumps({'controller_checks': {'records': records * 16}}, ensure_ascii=False)
        prior = {'assignment_id': '0b0308fb948a8b07815bc34d9286d0ccd2362e278d6c5c5a33d80384dcff4504',
                 'handoff_digest': 'd77bc59bbec777cb1c1d0fcd81356c9d5ab0d86d4f34737ae7f6172422d03c4b',
                 'runtime': {'run_id': 'regression', 'checks_sha256': 'a' * 64},
                 'handoff': {'candidate': 'b' * 40, 'baseline': 'c' * 40},
                 'submitted_result': {'tests': [{'command': records[0]['command'], 'result': long_output}],
                                      'artifacts': [{'name': 'stage-evidence', 'content': long_artifact}],
                                      'nested': {'escapes': ('"\\\n\tZoë😀' * 500)}}}
        receipt = {'records': records, 'nested': {'stdout': long_output}}
        with tempfile.TemporaryDirectory() as directory:
            artifacts = simplification.preceding(prior, Path(directory), {}, {}, receipt)
        evidence = next(item for item in artifacts if item['name'] == 'implementation-evidence')
        self.assert_readable(evidence)
        decoded = self.decode(evidence['content'])
        self.assertEqual(decoded['checks'], receipt)
        self.assertEqual(decoded['implementation_result'], prior['submitted_result'])
        self.assertEqual(decoded['candidate_sha256'], assignments.digest({}))
        self.assertFalse(decoded['native_execution_trusted'])
        # Controller's handoff reader must decode without changing result JSON contracts.
        self.assertEqual(simplification.handoff_evidence(evidence['content']), decoded)

    def test_multiline_diff_preserves_long_source_and_exact_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'inputs/baseline').mkdir(parents=True)
            (root / 'workspace').mkdir()
            old = 'baseline\n'
            new = ('print("Zoë😀\\n")' * 700) + '\n'
            (root / 'inputs/baseline/example.py').write_text(old)
            (root / 'workspace/example.py').write_text(new)
            before, after = {'example.py': {'sha256': 'a' * 64}}, {'example.py': {'sha256': 'b' * 64}}
            artifact = simplification.diff_artifact(root, before, after)
        self.assert_readable(artifact)
        entries = self.decode(artifact['content'])
        self.assertEqual(entries[0]['before'], before['example.py'])
        self.assertEqual(entries[0]['after'], after['example.py'])
        self.assertIn('+' + new, entries[0]['diff'])

    def test_short_json_stays_direct_and_long_values_round_trip(self):
        short = {'checks': {'stdout': 'Zoë\n'}, 'implementation_result': {'artifacts': []}}
        self.assertEqual(json.loads(simplification.handoff_json(short)), short)
        values = ['"\\\n\t😀' * 1500, 'x' * 2001, '\x00' * 2001]
        for value in values:
            with self.subTest(value_length=len(value)):
                original = {'nested': [{'content': value, 'result': value}], 'flags': [False, None, 12]}
                content = simplification.handoff_json(original)
                self.assertLessEqual(max(map(len, content.splitlines())), 2000)
                self.assertEqual(simplification.handoff_evidence(content), original)

    def test_chunk_transport_is_only_for_handoffs_not_result_contracts(self):
        content = simplification.handoff_json({'long': 'x' * 4000})
        transport = json.loads(content)
        self.assertEqual(simplification.object_json(content, 'invalid_result'), transport)
        from factory_v1.repositories import RepositoryError
        for chunks in (None, [12], {'bad': 'value'}):
            transport['chunks'] = chunks
            with self.subTest(chunks=chunks), self.assertRaises(RepositoryError) as caught:
                simplification.handoff_evidence(json.dumps(transport))
            self.assertEqual(caught.exception.code, 'implementation_unverified')

    def test_worker_refuses_truncated_lines_before_registering_load(self):
        # Execute the actual native-load loop, without probes, relay, inference or delegation.
        tree = ast.parse(Path(simplification.__file__).with_name('sandbox_worker.py').read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        loop = next(node for node in main.body if isinstance(node, ast.For) and
                    isinstance(node.target, ast.Name) and node.target.id == 'item')
        module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text('{"fixture": true}')
            envelope = {'inputs': [{'path': str(path), 'source': 'fixture',
                                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
            for flag in ('truncated_lines', 'truncated', 'error', 'not_found'):
                with self.subTest(flag=flag):
                    loads, instructions = [], []
                    def tool(name, args):
                        return json.dumps({'content': '1|partial', 'truncated': False, flag: True}), 'fixture:0'
                    namespace = dict(envelope=envelope, tool=tool, Path=Path, hashlib=hashlib,
                                     json=json, loads=loads, instructions=instructions)
                    with self.assertRaisesRegex(RuntimeError, 'instruction_load_incomplete'):
                        exec(compile(module, '<native-load-loop>', 'exec'), namespace)
                    self.assertEqual(loads, [])
                    self.assertEqual(instructions, [])


if __name__ == '__main__':
    unittest.main()
