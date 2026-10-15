"""Read-only local source-identity probe, separate from portable canonical tests.

Usage: mise exec -- python evidence/issue25/native-portability/verify_installed.py /path/to/hermes-agent
No native modules are imported. Drift or missing source is a failure, never a skip.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from factory_v1.tests import test_planning_wakeup as tests


def main():
    installed = Path(sys.argv[1]).resolve(strict=True)
    snapshots = ROOT / 'factory_v1/tests/native_api_snapshots'
    metadata = json.loads((snapshots / 'metadata.json').read_text())
    bodies = {}
    for name, pin in metadata.items():
        source = (installed / pin['path']).read_bytes()
        tree = ast.parse(source)
        if name == 'original_user_message':
            builder = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_turn_context')
            nodes = [n for n in ast.walk(builder) if isinstance(n, ast.Assign) and
                     any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        else:
            parent = tree
            if '.' in pin['qualname']:
                parent = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == pin['qualname'].split('.')[0])
            nodes = [n for n in parent.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        assert len(nodes) == 1, (name, 'ambiguous or missing installed API')
        node = nodes[0]
        body = b''.join(source.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])
        assert hashlib.sha256(source).hexdigest() == pin['source_sha256'], (name, 'installed file drift')
        assert hashlib.sha256(body).hexdigest() == pin['sha256'], (name, 'installed API drift')
        assert body == (snapshots / (name + '.txt')).read_bytes(), (name, 'snapshot differs from installed API')
        bodies[name] = body
        print(json.dumps({'api': pin['qualname'], 'path': pin['path'], 'sha256': pin['sha256'], 'equal': True}))

    def installed_contract(name, namespace):
        exec(compile(textwrap.dedent(bodies[name].decode()), str(installed / metadata[name]['path']), 'exec'), namespace)
        return namespace.get(name)

    names = ['test_pinned_native_host_spawn_body_and_bounded_join',
             'test_pinned_native_injection_router_rechecks_grant_and_same_origin',
             'test_pinned_native_inject_body_preserves_origin_and_boolean_semantics',
             'test_pinned_gateway_input_slice_reaches_real_strict_hook']
    suite = unittest.TestSuite(tests.PlanningWakeupTests(name) for name in names)
    with patch.object(tests, 'native_contract', installed_contract):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
