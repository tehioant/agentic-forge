"""MOCK ONLY: Status admission regressions through the public CLI/HTTP harness."""
import copy
import json
import unittest

from factory_v1.tests import test_ticket_native_scope


class TicketStatusTests(unittest.TestCase):
    setUp = test_ticket_native_scope.NativeScopeTests.setUp

    def assert_status_refused(self, status, heading='## Status'):
        self.case.publish()
        ready = 'ready (GitHub Projects is authoritative for current progress).'
        self.case.issues[12]['body'] = self.case.issues[12]['body'].replace(
            '## Status\n\n' + ready, heading + '\n\n' + status)
        members = copy.deepcopy(self.case.members)
        reservation = self.case.inspect().get('reservation')
        result = self.case.control('reserve-ticket', issue=12)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'ticket_ineligible')
        self.assertEqual(self.case.inspect().get('reservation'), reservation)
        self.assertEqual(self.case.members, members)
        snapshot = self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']
        self.assertEqual(snapshot['eligible'], [10])
        row = next(r for r in snapshot['items'] if r['number'] == 12)
        self.assertFalse(row['admissible'])
        self.assertTrue(row['held'] or not row['contract_valid'])
        reserved = self.case.ok(self.case.control('reserve-ticket', issue=10))
        self.assertEqual(reserved['reservation']['issue_number'], 10)
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertEqual(self.case.members[12], members[12])
        self.assertEqual(self.case.members[10]['fields']['STATUS'], 'In Progress')
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
        item = self.case.publish()
        self.assertIn('## Status\n\nready (GitHub Projects is authoritative for current progress).',
                      self.case.issues[12]['body'])
        self.assertEqual(item['ticket_work']['frontier']['eligible'], [10, 12])
        self.assertTrue(all(r['contract_valid'] and not r['held']
                            for r in item['ticket_work']['frontier']['items']))
        reserved = self.case.ok(self.case.control('reserve-ticket', issue=12))
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertEqual(self.case.members[12]['fields']['STATUS'], 'In Progress')
        self.assertEqual(self.case.issues[12]['milestone']['id'], 501)
        self.assertFalse(reserved['execution_allowed'])
