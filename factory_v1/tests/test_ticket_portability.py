"""Ticket lifecycle fixtures honor tempfile/TMPDIR in either supported layout."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.tests import test_tickets


class TicketPortabilityTests(unittest.TestCase):
    def test_ticket_controls_use_configured_scratch_and_runner_temp_layouts(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('scratch', 'runner-temp'):
                configured = Path(root) / name
                configured.mkdir()
                with self.subTest(layout=name), patch.object(tempfile, 'tempdir', str(configured)):
                    case = test_tickets.TicketTests('test_synthesis_publication_frontier_and_one_reservation')
                    try:
                        case.setUp()
                        self.assertEqual(case.fixture.root.parent, configured)
                        case.test_synthesis_publication_frontier_and_one_reservation()
                    finally:
                        case.doCleanups()
                    self.assertEqual(list(configured.iterdir()), [])
