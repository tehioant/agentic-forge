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

    def test_exact_nested_and_multiline_roundtrip_matrix(self):
        records = [{'command': 'PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -v', 'exit_code': 0, 'stdout': '', 'output_truncated': False, 'stderr': 'test_ordinary_name (test_hello.GreetTests.test_ordinary_name) ... ok\ntest_preserves_name_exactly (test_hello.GreetTests.test_preserves_name_exactly) ... ok\ntest_unicode_name (test_hello.GreetTests.test_unicode_name) ... ok\n\n----------------------------------------------------------------------\nRan 3 tests in 0.000s\n\nOK\n'}]
        long_output = records[0]['stderr'] * 16
        long_artifact = json.dumps({'controller_checks': {'records': records * 16}}, ensure_ascii=False)
        prior = {'assignment_id': '0b0308fb948a8b07815bc34d9286d0ccd2362e278d6c5c5a33d80384dcff4504', 'handoff_digest': 'd77bc59bbec777cb1c1d0fcd81356c9d5ab0d86d4f34737ae7f6172422d03c4b', 'runtime': {'run_id': 'regression', 'checks_sha256': 'a' * 64}, 'handoff': {'candidate': 'b' * 40, 'baseline': 'c' * 40}, 'submitted_result': {'tests': [{'command': records[0]['command'], 'result': long_output}], 'artifacts': [{'name': 'stage-evidence', 'content': long_artifact}], 'nested': {'escapes': '"\\\n\tZoë😀' * 500}}}
        receipt = {'records': records, 'nested': {'stdout': long_output}}
        with tempfile.TemporaryDirectory() as directory:
            artifacts = simplification.preceding(prior, Path(directory), {}, {}, receipt)
        evidence = next((item for item in artifacts if item['name'] == 'implementation-evidence'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'inputs/baseline').mkdir(parents=True)
            (root / 'workspace').mkdir()
            old = 'baseline\n'
            new = 'print("Zoë😀\\n")' * 700 + '\n'
            (root / 'inputs/baseline/example.py').write_text(old)
            (root / 'workspace/example.py').write_text(new)
            before, after = ({'example.py': {'sha256': 'a' * 64}}, {'example.py': {'sha256': 'b' * 64}})
            artifact = simplification.diff_artifact(root, before, after)
        for label, value, expected in (('implementation', evidence, None), ('diff', artifact, None), ('short', None, {'checks': {'stdout': 'Zoë\n'}, 'implementation_result': {'artifacts': []}}), *[(name, None, {'nested': [{'content': value, 'result': value}], 'flags': [False, None, 12]}) for name, value in [('escapes', '"\\\n\t😀' * 1500), ('long', 'x' * 2001), ('nul', '\x00' * 2001)]]):
            with self.subTest(label=label):
                content = value['content'] if value else simplification.handoff_json(expected)
                self.assertLessEqual(max(map(len, content.splitlines())), 2000)
                decoded = self.decode(content)
                if label != 'diff':
                    self.assertEqual(simplification.handoff_evidence(content), decoded)
                if value:
                    self.assert_readable(value)
                if label == 'implementation':
                    self.assertEqual(decoded['checks'], receipt)
                    self.assertEqual(decoded['implementation_result'], prior['submitted_result'])
                    self.assertEqual(decoded['candidate_sha256'], assignments.digest({}))
                    self.assertFalse(decoded['native_execution_trusted'])
                elif label == 'diff':
                    self.assertEqual(decoded[0]['before'], before['example.py'])
                    self.assertEqual(decoded[0]['after'], after['example.py'])
                    self.assertIn('+' + new, decoded[0]['diff'])
                else:
                    self.assertEqual(decoded, expected)
                    if label == 'short':
                        self.assertEqual(json.loads(content), expected)



    def test_transport_not_result_authority_matrix(self):
        from factory_v1.repositories import RepositoryError
        content = simplification.handoff_json({'long': 'x' * 4000})
        transport = json.loads(content)
        self.assertEqual(simplification.object_json(content, 'invalid_result'), transport)
        tree = ast.parse(Path(simplification.__file__).with_name('sandbox_worker.py').read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        loop = next(node for node in main.body if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'item')
        module = ast.fix_missing_locations(ast.Module(body=[loop], type_ignores=[]))
        compiled = compile(module, '<native-load-loop>', 'exec')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text('{"fixture": true}')
            envelope = {'inputs': [{'path': str(path), 'source': 'fixture', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
            cases = [('chunks', defect) for defect in (None, [12], {'bad': 'value'})]
            cases += [('load', flag) for flag in ('truncated_lines', 'truncated', 'error', 'not_found')]
            for kind, defect in cases:
                with self.subTest(kind=kind, defect=defect):
                    loads, instructions = [], []
                    if kind == 'chunks':
                        malformed = dict(transport, chunks=defect)
                        operation, arguments, expected = simplification.handoff_evidence, (json.dumps(malformed),), RepositoryError
                    else:
                        def tool(name, args):
                            return json.dumps({'content': '1|partial', 'truncated': False, defect: True}), 'fixture:0'
                        namespace = dict(envelope=envelope, tool=tool, Path=Path, hashlib=hashlib, json=json, loads=loads, instructions=instructions)
                        operation, arguments, expected = exec, (compiled, namespace), RuntimeError
                    with self.assertRaises(expected) as caught:
                        operation(*arguments)
                    if kind == 'chunks':
                        self.assertEqual(caught.exception.code, 'implementation_unverified')
                    else:
                        self.assertRegex(str(caught.exception), 'instruction_load_incomplete')
                        self.assertEqual(loads, [])
                        self.assertEqual(instructions, [])



if __name__ == '__main__':
    unittest.main()
