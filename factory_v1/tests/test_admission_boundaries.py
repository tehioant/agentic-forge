"""Focused planning/ticket admission checks; network and skill loading are mocked."""
import base64
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import controls, planning, repositories, tickets


class AdmissionBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = sqlite3.connect(Path(directory.name) / 'state.sqlite')
        self.addCleanup(self.database.close)
        self.database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'status': 'active', 'stage': 'approved', 'correlation': {'stage': 'approved'},
                     'approval': {'operator_id': '42', 'reference': 'test-direction'},
                     'origin': {'platform': 'discord', 'chat_id': '1'},
                     'repository_onboarding': {'status': 'verified'}}
        self.database.execute('INSERT INTO iterations VALUES (?, ?, ?)',
                              ('product', 'm1', json.dumps(self.item)))
        self.database.commit()
        self.request = {'origin': self.item['origin'], 'expected_revision': 0, 'skills': []}
        self.github = Mock(base='https://api.github.com')

    def stored(self):
        return json.loads(self.database.execute('SELECT payload FROM iterations').fetchone()[0])

    def persist(self):
        repositories.save_iteration(self.database, self.item)
        self.database.commit()

    def pause(self):
        return controls.request(self.database, 'product', 'm1', '42', 'pause', 'test-pause')

    def assert_refused(self, operation, before):
        with self.assertRaises(repositories.RepositoryError) as caught:
            operation()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.database.rollback()
        self.assertEqual(self.stored(), before)
        self.assertTrue(controls.blocked(self.database, 'product', 'm1'))

    def test_pause_blocks_planning_until_explicit_resume(self):
        before = self.stored()
        self.pause()
        with patch.object(planning, 'resolve', return_value={}) as load:
            self.assert_refused(lambda: planning.plan(self.database, self.item, self.request), before)
            load.assert_not_called()
            controls.resume(self.database, 'product', 'm1', '42', 'explicit-test-resume')
            result = planning.plan(self.database, self.item, self.request)
        self.assertEqual(result['stage'], 'planning_pending')
        self.assertEqual(self.stored(), result)
        self.assertFalse(controls.blocked(self.database, 'product', 'm1'))

    def test_pause_during_planning_preparation_prevents_final_claim(self):
        before = self.stored()
        def load(_):
            self.pause()
            return {}
        with patch.object(planning, 'resolve', side_effect=load) as selected:
            self.assert_refused(lambda: planning.plan(self.database, self.item, self.request), before)
        selected.assert_called_once_with(self.request['skills'])
        self.assertNotIn('planning', self.stored())

    def test_explicit_iteration_resume_does_not_clear_revision_hold(self):
        self.item['requirement_authority'] = {'10': {'revision': 'held'}}
        self.persist()
        self.pause()
        controls.resume(self.database, 'product', 'm1', '42', 'explicit-test-resume')
        with patch.object(planning, 'resolve', return_value={}):
            with self.assertRaises(repositories.RepositoryError) as caught:
                planning.plan(self.database, self.item, self.request)
        self.assertEqual(caught.exception.code, 'requirements_held')
        self.database.rollback()
        self.assertEqual(self.stored()['requirement_authority'], self.item['requirement_authority'])
        self.assertNotIn('planning', self.stored())

    def test_pause_during_publication_read_prevents_planning_completion(self):
        self.item['planning'] = {'assignment_id': 'planning-test', 'status': 'pending',
                                 'request': self.request, 'docs_path': 'docs/test'}
        self.persist()
        before = self.stored()
        documents = {'milestone.md': ''.join('## ' + section + '\n\nTest substantive content.\n\n'
                                            for section in planning.SECTIONS),
                     'vision.md': 'Test future vision.', 'provenance.md': 'Test recorded direction.'}
        commit = 'a' * 40
        request = {'assignment_id': 'planning-test', 'origin': self.item['origin'],
                   'sufficient': True, 'rationale': 'Test settled frontier', 'reference': 'test-message',
                   'pending_questions': [], 'documents': documents, 'commit': commit, 'issue_number': 7}
        def read(path):
            if '/git/commits/' in path:
                return {'sha': commit}
            if '/contents/' in path:
                name = path.split('/')[-1].split('?')[0]
                raw = documents[name].encode()
                return {'type': 'file', 'path': 'docs/test/' + name, 'encoding': 'base64',
                        'content': base64.b64encode(raw).decode(),
                        'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
            self.assertEqual(path, '/repos/example/product/issues/7')
            self.pause()
            return {'number': 7, 'id': 700, 'html_url': 'https://github.com/example/product/issues/7',
                    'repository_url': 'https://api.github.com/repos/example/product', 'state': 'open',
                    'body': documents['milestone.md'], 'labels': [{'name': 'ready-for-agent'}]}
        self.github.request.side_effect = read
        with patch.object(planning, 'resolve', return_value={}), patch.object(planning, 'reconcile'):
            self.assert_refused(lambda: planning.complete(self.database, self.item, request, self.github), before)
        self.assertEqual(self.github.request.call_count, 5)
        self.assertNotIn('handoff', self.stored())
        self.assertTrue(all(len(call.args) == 1 for call in self.github.request.call_args_list))

    def ticket_work(self):
        source = Path(__file__).resolve().parents[1] / 'ticket_skills/to-tickets.md'
        descriptor = {'name': 'to-tickets', 'path': str(source), 'sha256': tickets.PIN}
        selected = tickets.skill(descriptor)
        url = 'https://github.com/example/product/blob/' + 'a' * 40 + '/milestone.md'
        inputs = {'handoff': {'issue': {'number': 7}},
                  'documents': {'milestone.md': {'url': url, 'sha256': 'a' * 64,
                                'content': '## User Stories\n1. As a reader, retain notes.\n'}}}
        request = {'skill': descriptor, 'tracker': {'triage_label': 'ready-for-agent'}}
        batch = [{'key': 'notes', 'title': 'Retain notes', 'desired_behavior': 'Persist notes.',
                  'references': [{'spec': url, 'requirement': 'US-1'}],
                  'acceptance_criteria': ['Stored notes can be read.'], 'blockers': [],
                  'status': 'ready', 'iteration': 'm1', 'triage_label': 'ready-for-agent'}]
        work = {'assignment_id': 'ticket-test', 'request': request, 'skill': selected,
                'inputs': inputs, 'input_digest': tickets.digest(inputs), 'status': 'synthesis_pending'}
        execution = {'run_id': 'test-run', 'decomposition': 'Test bounded slice.',
                     'result_digest': tickets.digest(batch),
                     'loads': [{'source': source_name, 'sha256': pin, 'tool_reference': 'test-load'}
                               for source_name, pin in {str(source): tickets.PIN, url: 'a' * 64}.items()]}
        return work, batch, execution

    def test_pause_during_pinned_read_prevents_synthesis_claim(self):
        work, _, _ = self.ticket_work()
        before = self.stored()
        def read(*_):
            self.pause()
            return work['inputs']
        with patch.object(tickets, 'pinned_inputs', side_effect=read) as inputs:
            self.assert_refused(lambda: tickets.synthesize(self.database, self.item, work['request'], self.github), before)
        inputs.assert_called_once()
        self.assertNotIn('ticket_work', self.stored())
        self.github.request.assert_not_called()

    def test_pause_during_pinned_read_prevents_synthesis_result_admission(self):
        work, batch, execution = self.ticket_work()
        self.item['ticket_work'] = copy.deepcopy(work)
        self.persist()
        before = self.stored()
        request = {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'],
                   'adaptation': tickets.ADAPTATION, 'execution': execution, 'tickets': batch}
        def read(*_):
            self.pause()
            return work['inputs']
        with patch.object(tickets, 'pinned_inputs', side_effect=read), patch.object(tickets, 'publish') as publish:
            self.assert_refused(lambda: tickets.complete_synthesis(self.database, self.item, request, self.github), before)
        publish.assert_not_called()
        self.github.request.assert_not_called()

    def test_pause_at_final_issue_list_read_prevents_publication_intent(self):
        work, batch, execution = self.ticket_work()
        work.update(tickets=batch, execution=execution, status='publication_pending')
        self.item['ticket_work'] = work
        self.item['stage'] = self.item['correlation']['stage'] = 'ticket_publication_pending'
        self.persist()
        board = {'project_id': 'project-test'}
        def listed(*_):
            self.pause()
            return []
        with patch.object(tickets, 'pinned_inputs', return_value=work['inputs']), \
                patch.object(tickets, 'verified_board', return_value=board), \
                patch.object(tickets.gh, 'rest_pages', side_effect=listed) as read:
            with self.assertRaises(repositories.RepositoryError) as caught:
                tickets.publish(self.database, self.item, self.github)
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.database.rollback()
        stored = self.stored()
        self.assertEqual(stored['ticket_work']['status'], 'publication_pending')
        self.assertEqual(stored['ticket_work']['board'], board)
        self.assertNotIn('intents', stored['ticket_work'])
        self.assertNotIn('blocker', stored['ticket_work'])
        self.assertEqual(stored['stage'], 'ticket_publication_pending')
        self.assertTrue(controls.blocked(self.database, 'product', 'm1'))
        read.assert_called_once()
        self.github.request.assert_not_called()
