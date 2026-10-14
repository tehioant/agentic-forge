"""Ticket admission components honor tempfile/TMPDIR in supported layouts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.tests import test_lean_tickets


class TicketPortabilityTests(unittest.TestCase):
    def test_ticket_components_use_configured_scratch_and_runner_temp_layouts(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('scratch', 'runner-temp'):
                configured = Path(root) / name
                configured.mkdir()
                with self.subTest(layout=name), patch.object(tempfile, 'tempdir', str(configured)):
                    case = test_lean_tickets.LeanControllerCase()
                    try:
                        case.setUp()
                        self.assertEqual(case.root.parent, configured)
                        reserved = case.reserve(12)
                        self.assertEqual(reserved['reservation']['status'], 'reserved')
                        self.assertEqual(case.stored()['reservation']['issue_number'], 12)
                        self.assertFalse(reserved['execution_allowed'])
                    finally:
                        case.doCleanups()
                    self.assertEqual(list(configured.iterdir()), [])
