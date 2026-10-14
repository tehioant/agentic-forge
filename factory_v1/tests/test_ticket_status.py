"""Status contract/frontier admission with mocked tracker I/O, without lifecycle setup."""
import copy

from factory_v1 import repositories
from factory_v1.tests import test_lean_tickets


class TicketStatusTests(test_lean_tickets.LeanControllerCase):
    def assert_status_refused(self, status, heading='## Status'):
        ready = 'ready (GitHub Projects is authoritative for current progress).'
        self.issues[12]['body'] = self.canonical_body.replace('## Status\n\n' + ready, heading + '\n\n' + status)
        self.assertNotEqual(self.issues[12]['body'], self.canonical_body)
        members = copy.deepcopy(self.members)
        snapshot = self.frontier()
        before = self.stored()
        with self.assertRaises(repositories.RepositoryError) as error:
            self.reserve(12)
        self.assertEqual(error.exception.code, 'ticket_ineligible')
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.members, members)
        self.assert_no_external_writes()
        self.assertEqual(snapshot['eligible'], [10])
        row = next(r for r in snapshot['items'] if r['number'] == 12)
        self.assertFalse(row['admissible'])
        self.assertTrue(row['held'] or not row['contract_valid'])
        reserved = self.reserve(10)
        self.assertEqual(reserved['reservation']['issue_number'], 10)
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertEqual(self.members[12], members[12])
        self.assertEqual(self.members[10]['fieldValues']['nodes'][0]['optionId'], 'Active')
        self.assertFalse(reserved['execution_allowed'])

    def test_deferred_under_trailing_space_status_heading_is_refused(self):
        self.assert_status_refused('deferred', '## Status ')

    def test_annotated_deferred_is_refused(self):
        self.assert_status_refused('deferred (waiting for operator)')

    def test_unknown_status_is_refused(self):
        self.assert_status_refused('unrecognized')

    def test_multiline_status_is_refused(self):
        self.assert_status_refused('ready\ndeferred')

    def test_unclosed_status_annotation_is_refused(self):
        self.assert_status_refused('ready (waiting for operator')

    def test_duplicate_status_section_is_refused(self):
        self.assert_status_refused('ready\n\n## Status \n\ndeferred')

    def test_canonical_generated_ready_annotation_remains_admissible(self):
        self.issues[12]['body'] = self.canonical_body
        self.assertIn('## Status\n\nready (GitHub Projects is authoritative for current progress).', self.issues[12]['body'])
        snapshot = self.frontier()
        self.assertEqual(snapshot['eligible'], [10, 12])
        self.assertTrue(all(r['contract_valid'] and not r['held'] for r in snapshot['items']))
        reserved = self.reserve(12)
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertEqual(self.members[12]['fieldValues']['nodes'][0]['optionId'], 'Active')
        self.assertEqual(self.issues[12]['body'], self.canonical_body)
        self.assertFalse(reserved['execution_allowed'])
