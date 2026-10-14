"""Control component fixtures honor the configured temporary directory."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.tests import test_controls


class FixturePortabilityTests(unittest.TestCase):
    def test_control_fixture_uses_configured_directory_and_removes_durable_sidecars(self):
        with tempfile.TemporaryDirectory() as configured_directory:
            with patch.object(tempfile, 'tempdir', configured_directory):
                case = test_controls.ControlFixture()
                try:
                    case.setUp()
                    self.assertEqual(case.root.parent, Path(configured_directory))
                    self.assertFalse(case.pause()['dispatch_allowed'])
                    self.assertEqual(case.inspect()['action'], 'pause')
                    self.assertTrue(case.state.exists())
                finally:
                    case.doCleanups()
                self.assertFalse(case.root.exists())
