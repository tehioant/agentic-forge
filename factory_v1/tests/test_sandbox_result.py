"""Result/prompt contract regressions; labeled unit evidence, not live inference."""
import ast
import json
import tempfile
import unittest
from pathlib import Path

from factory_v1 import sandbox
from factory_v1.repositories import RepositoryError


class ResultContractTests(unittest.TestCase):
    def test_worker_prompt_states_exact_command_and_string_result_contract(self):
        # Evaluate the actual prompt expression without starting the container-only worker.
        tree = ast.parse((Path(sandbox.__file__).with_name('sandbox_worker.py')).read_text())
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign) and
                    any(isinstance(target, ast.Name) and target.id == 'prompt' for target in node.targets))
        handoff = dict.fromkeys(('issue', 'adaptation', 'stage_rules', 'support_policy', 'skill_entry_points', 'stage'))
        handoff['stage'] = 'implementation'
        prompt = eval(compile(ast.Expression(node.value), '<worker-prompt>', 'eval'), {'json': json},
                      {'assignment': {'assignment_id': 'fixture'}, 'handoff': handoff, 'instructions': []})
        self.assertIn('complete native terminal command verbatim, including compound shell commands', prompt)
        self.assertIn('nonempty string containing actual output and exit status, not an object', prompt)
        self.assertIn('exact UTF-8 content with a terminal tool', prompt)

    def check_command(self, reported):
        command = 'python -m unittest discover -v && python -m compileall -q hello.py test_hello.py'
        with tempfile.TemporaryDirectory(prefix='f1-result-') as directory:
            root = Path(directory)
            scratch = root / 'scratch'
            scratch.mkdir()
            result = {'run_id': 'fixture', 'loads': [], 'tests': [{'command': reported, 'result': 'OK; exit 0'}]}
            conversation = {'messages': [
                {'role': 'assistant', 'tool_calls': [{'id': 'test', 'function': {
                    'name': 'terminal', 'arguments': json.dumps({'command': command})}}]},
                {'role': 'tool', 'tool_call_id': 'test', 'content': json.dumps({'output': 'Ran 2 tests; OK', 'exit_code': 0})}]}
            for name, value in [('result.json', result), ('loads.json', []),
                                ('conversation.json', conversation), ('probes.json', {})]:
                (scratch / name).write_text(json.dumps(value))
            (scratch / 'events.jsonl').write_text('')
            (root / 'model-evidence').mkdir()
            (root / 'model-evidence/response-1.json').write_text('{"label":"unit fixture, not inference"}')
            return sandbox.check_worker_evidence({'runtime': {'run_id': 'fixture'}}, root, [])

    def test_exact_compound_command_is_accepted(self):
        command = 'python -m unittest discover -v && python -m compileall -q hello.py test_hello.py'
        self.assertEqual(self.check_command(command)['tests'][0]['command'], command)

    def test_split_or_fabricated_commands_still_fail_closed(self):
        for command in ('python -m unittest discover -v', 'python -m compileall -q hello.py test_hello.py', 'echo OK'):
            with self.subTest(command=command), self.assertRaises(RepositoryError) as caught:
                self.check_command(command)
            self.assertEqual(caught.exception.code, 'invalid_result')
