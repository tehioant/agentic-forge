"""Public launcher tests; Docker/model fixtures are NOT live isolation evidence."""
import json
import os
import subprocess
import unittest
from pathlib import Path

from factory_v1.tests import test_assignments

IMAGE = 'nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db'


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.assignment = test_assignments.AssignmentTests()
        self.assignment.setUp()
        self.addCleanup(self.assignment.doCleanups)
        self.root = self.assignment.root
        self.source = Path(self.assignment.request['workspace'])
        self.source.mkdir()
        for command in [['git', 'init', '-q'], ['git', 'config', 'user.email', 'fixture@example.invalid'],
                        ['git', 'config', 'user.name', 'Fixture']]:
            subprocess.run(command, cwd=self.source, check=True, capture_output=True)
        (self.source / 'hello.py').write_text('print("baseline")\n')
        subprocess.run(['git', 'add', '.'], cwd=self.source, check=True)
        subprocess.run(['git', 'commit', '-qm', 'test: baseline'], cwd=self.source, check=True)
        self.assignment.request['baseline'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip()
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir(mode=0o700)
        self.config = self.root / 'launcher.json'
        self.config.write_text(json.dumps({'image': IMAGE, 'uid': os.getuid() or 10000,
            'subscription_socket': str(self.root / 'subscription.sock'), 'artifacts_root': str(self.artifacts),
            'limits': {'seconds': 30, 'max_calls': 10, 'memory_mb': 512, 'cpus': 1, 'pids': 64, 'scratch_mb': 64}}))
        self.config.chmod(0o600)

    def launch(self, prepared, **kwargs):
        return self.assignment.command('launch-assignment', assignment=prepared['assignment_id'],
            extra=('--launcher-config', str(self.config), '--api-base', self.assignment.fixture.base), **kwargs)

    def test_untrusted_launcher_fields_are_refused_without_creating_attempt(self):
        prepared = self.assignment.ok(self.assignment.command())
        config = json.loads(self.config.read_text())
        config['command'] = ['sh', '-c', 'unrestricted']
        self.config.write_text(json.dumps(config))
        self.assignment.refused(self.launch(prepared), 'invalid_launcher')
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertNotIn('runtime', observed)
        self.assertEqual(list(self.artifacts.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
