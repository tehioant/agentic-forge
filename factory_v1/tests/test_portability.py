"""Lifecycle fixtures must honor tempfile's configured temporary directory."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.tests import test_intake, test_repositories


class FixturePortabilityTests(unittest.TestCase):
    def test_lifecycle_fixtures_use_the_configured_temporary_directory(self):
        with tempfile.TemporaryDirectory() as configured_directory:
            with patch.object(tempfile, 'tempdir', configured_directory):
                fixtures = (
                    (test_intake.IntakeLifecycleTests,
                     'test_approved_iteration_survives_a_fresh_controller_process'),
                    (test_repositories.RepositoryLifecycleTests,
                     'test_selected_existing_repository_is_read_back_without_creation'),
                )
                for case_type, behavior in fixtures:
                    with self.subTest(fixture=case_type.__name__):
                        case = case_type(behavior)
                        try:
                            case.setUp()
                            self.assertEqual(case.root.parent, Path(configured_directory))
                            # Exercise the public CLI with all state in the configured directory.
                            getattr(case, behavior)()
                        finally:
                            case.doCleanups()
                        self.assertFalse(case.root.exists())
