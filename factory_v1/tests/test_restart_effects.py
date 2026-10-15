"""Single persisted-effect restart components; tracker/GitHub input is simulated.

No launch, synthesis, review chain or remote mutation is performed. FrontierFixture
constructs rows and replaces individual tracker read/write seams, not a journey.
"""
import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import assignments, merge, publication, repositories, restart, tickets
from factory_v1.repositories import RepositoryError
from factory_v1.tests.test_tickets import FrontierFixture


class TrackerRestartTests(unittest.TestCase):
    def setUp(self):
        self.f = FrontierFixture(self)
        self.enterContext(patch.object(repositories, 'reconcile', return_value=self.f.item))
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            self.enterContext(patch(target, side_effect=AssertionError('External execution forbidden')))

    def read(self):
        return restart.tracker(self.f.database, self.f.stored(), restart.Readback(self.f.github, 'example/product'))

    def test_exact_board_frontier_and_dependencies_are_read_without_reservation(self):
        result = self.read()
        self.assertEqual(result['eligible'], [10, 12])
        self.assertFalse(next(r for r in result['items'] if r['number'] == 11)['admissible'])
        self.assertNotIn('reservation', self.f.stored())
        self.f.set_field.assert_not_called()
        self.f.add_item.assert_not_called()
        self.f.add_dependency.assert_not_called()

    def test_native_textual_dependency_disagreement_blocks_restart(self):
        self.f.deps[11] = []
        with self.assertRaises(RepositoryError):
            self.read()
        self.f.set_field.assert_not_called()

    def test_board_duplicate_and_unknown_progress_matrix(self):
        for mutation in ('duplicate', 'unknown'):
            with self.subTest(mutation=mutation):
                if mutation == 'duplicate':
                    original = self.f.project_items()
                    with patch.object(tickets.gh, 'project_items', return_value=original + original[:1]), self.assertRaises(RepositoryError):
                        self.read()
                else:
                    self.f.members[10]['fields']['STATUS'] = 'UNKNOWN'
                    with self.assertRaises(RepositoryError):
                        self.read()
        self.f.set_field.assert_not_called()

    def test_lost_issue_response_adopts_only_fully_read_back_publication(self):
        self.f.publication()
        ticket = self.f.work['tickets'][0]
        body = tickets.body(self.f.item, ticket, {})
        self.f.work['intents'] = {'note': {'issue': {'title': ticket['title'], 'body': body}}}
        self.f.issues[10] = self.f.issue(10, body)
        self.f.members[10] = {'id': 'ITEM10', 'fields': {'STATUS': 'Ready', 'SCOPE': 'M1'}}
        self.f.persist()
        item = self.f.stored()
        restart.tracker(self.f.database, item, restart.Readback(self.f.github, 'example/product'))
        self.assertTrue(item['ticket_work']['publication_complete'])
        self.assertEqual(item['ticket_work']['published']['note'], {'number': 10, 'id': 1010, 'node_id': 'I10'})
        self.f.github.request.assert_not_called()
        self.f.set_field.assert_not_called()
        self.f.add_item.assert_not_called()
        self.f.add_dependency.assert_not_called()
        self.assertFalse(self.f.stored()['ticket_work'].get('publication_complete', False))

    def test_absent_issue_effect_never_creates_again(self):
        self.f.publication()
        self.f.work['intents'] = {'note': {'issue': {'title': 'Retain a note'}}}
        self.f.persist()
        with self.assertRaises(RepositoryError):
            self.read()
        self.f.github.request.assert_not_called()
        self.f.add_item.assert_not_called()

    def test_lost_status_response_is_read_back_without_second_write(self):
        self.f.item['reservation'] = {'issue_number': 10, 'run_id': 'retained-run', 'status': 'pending',
                                      'assignment_id': self.f.work['assignment_id'], 'input_digest': self.f.work['input_digest']}
        self.f.members[10]['fields']['STATUS'] = 'Active'
        self.f.persist()
        item = self.f.stored()
        restart.tracker(self.f.database, item, restart.Readback(self.f.github, 'example/product'))
        self.assertEqual(item['reservation']['status'], 'reserved')
        self.assertEqual(item['reservation']['run_id'], 'retained-run')
        self.f.set_field.assert_not_called()
        self.f.members[10]['fields']['STATUS'] = 'Ready'
        with self.assertRaises(RepositoryError):
            self.read()
        self.f.set_field.assert_not_called()


class CandidateRestartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.handoff = {'repository': 'owner/repo', 'repository_id': 123, 'baseline': 'a' * 40,
                        'candidate': None, 'stage': 'implementation', 'profile': {'name': 'writer'},
                        'spec_commit': 'c' * 40, 'issue': {'number': 10, 'id': 100, 'node_id': 'I10'}}
        self.assignment = {'assignment_id': 'd' * 64, 'claim_id': 'claim', 'handoff': self.handoff,
                           'handoff_digest': assignments.digest(self.handoff), 'revision': {}, 'ticket_scope': {},
                           'runtime': {'run_id': 'run', 'artifacts': str(self.root), 'status': 'complete', 'container_removed': True}}
        self.config = {'repository': 'owner/repo', 'repository_id': 123, 'api_base': 'https://api.github.com',
                       'branch': 'factory/issue-10', 'base': 'main', 'candidate': 'b' * 40, 'expected_head': 'a' * 40,
                       'issue': 10, 'binding': publication.binding(self.assignment)}
        self.plan = {'candidate': 'b' * 40, 'tree': 'c' * 40, 'base_tree': 'e' * 40, 'message': 'candidate'}
        self.assignment['publication'] = {'status': 'pending', 'approval': self.config,
                                          'candidate': 'b' * 40, 'pr_number': None}
        self.pr = {'number': 20, 'state': 'open', 'merged': False, 'draft': True,
                   'title': 'Factory candidate for #10',
                   'body': 'Refs #10\n\nCandidate publication only; not delivery, closure or merge.\n\n<!-- factory-v1-publication:' + self.assignment['assignment_id'] + ' -->',
                   'head': {'ref': self.config['branch'], 'sha': 'b' * 40, 'repo': {'id': 123, 'full_name': 'owner/repo'}},
                   'base': {'ref': 'main', 'sha': 'a' * 40, 'repo': {'id': 123, 'full_name': 'owner/repo'}}}
        self.values = {
            '/git/ref/heads/factory/issue-10': {'ref': 'refs/heads/factory/issue-10', 'object': {'type': 'commit', 'sha': 'b' * 40}},
            '/git/commits/' + 'b' * 40: {'sha': 'b' * 40, 'tree': {'sha': 'c' * 40}, 'parents': [{'sha': 'a' * 40}], 'message': 'candidate\n'},
            publication.pr_query(self.config): [self.pr], '/pulls/20': self.pr,
            '/issues/20': {'number': 20, 'body': self.pr['body'], 'state': 'open', 'pull_request': {},
                           'repository_url': 'https://api.github.com/repos/owner/repo'}}
        self.api = Mock(base='https://api.github.com')
        self.api.read.side_effect = lambda suffix: copy.deepcopy(self.values[suffix])
        self.enterContext(patch.object(publication, 'assemble', return_value=(self.plan, {})))
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            self.enterContext(patch(target, side_effect=AssertionError('External execution forbidden')))

    def test_lost_pr_response_adopts_exact_existing_effect_without_writes(self):
        result = restart.candidate(self.assignment, self.api)
        assert result is not None
        self.assertEqual(result['pr_number'], 20)
        self.assertEqual(self.assignment['publication']['status'], 'published')
        self.api.write.assert_not_called()
        self.assertNotIn('submitted_result', self.assignment)

    def test_absent_duplicate_or_wrong_ref_never_replays_publication(self):
        for suffix, replacement in ((publication.pr_query(self.config), []),
                                    (publication.pr_query(self.config), [self.pr, self.pr]),
                                    ('/git/ref/heads/factory/issue-10', None)):
            with self.subTest(suffix=suffix, replacement=replacement):
                before = copy.deepcopy(self.assignment)
                original = self.values[suffix]
                self.values[suffix] = replacement
                with self.assertRaises(RepositoryError):
                    restart.candidate(self.assignment, self.api)
                self.assertEqual(self.assignment, before)
                self.values[suffix] = original
        self.api.write.assert_not_called()

    def test_pr_identity_body_repository_and_candidate_drift_matrix(self):
        for field in ('body', 'head-repo', 'head-sha', 'closed'):
            with self.subTest(field=field):
                pr = copy.deepcopy(self.pr)
                if field == 'body': pr['body'] += 'external edit'
                elif field == 'head-repo': pr['head']['repo']['id'] = 999
                elif field == 'head-sha': pr['head']['sha'] = 'f' * 40
                else: pr['state'] = 'closed'
                with patch.object(self.api, 'read', side_effect=lambda suffix: [pr] if suffix == publication.pr_query(self.config) else copy.deepcopy(self.values[suffix])), self.assertRaises(RepositoryError):
                    restart.candidate(self.assignment, self.api)
        self.api.write.assert_not_called()

    def test_known_published_pr_can_be_ready_without_granting_merge(self):
        self.assignment['publication'].update(status='published', pr_number=20)
        self.pr['draft'] = False
        restart.candidate(self.assignment, self.api)
        self.assertNotIn('merge', self.assignment)
        self.api.write.assert_not_called()

    def test_unsafe_or_wrong_journal_targets_refuse_before_remote_reads(self):
        for change in ({'branch': '../../other'}, {'branch': 'main'}, {'issue': 11}, {'candidate': 'B' * 40}):
            with self.subTest(change=change):
                row = copy.deepcopy(self.assignment)
                row['publication']['approval'].update(change)
                self.api.read.reset_mock()
                with self.assertRaises(RepositoryError):
                    restart.candidate(row, self.api)
                self.api.read.assert_not_called()

    def merged(self):
        self.assignment['publication'].update(status='published', pr_number=20)
        authority = {**self.config, 'candidate_assignment': self.assignment['assignment_id'],
                     'pr_number': 20, 'head': 'b' * 40, 'baseline': 'a' * 40,
                     'required_checks': [{'context': 'quality', 'app_id': 1}]}
        self.assignment['merge'] = {'status': 'pending', 'authority': authority, 'integrated_evidence': None,
                                    'close_allowed': False}
        self.pr.update(state='closed', merged=True, merge_commit_sha='f' * 40)
        self.values.update({'': {'id': 123, 'full_name': 'owner/repo', 'default_branch': 'main'},
                            '/git/commits/' + 'f' * 40: {'sha': 'f' * 40, 'tree': {'sha': 'c' * 40},
                                                       'parents': [{'sha': 'a' * 40}, {'sha': 'b' * 40}]},
                            '/git/ref/heads/main': {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': 'f' * 40}},
                            '/compare/' + 'f' * 40 + '...' + 'f' * 40: {'status': 'identical', 'base_commit': {'sha': 'f' * 40}, 'merge_base_commit': {'sha': 'f' * 40}},
                            '/issues/10': {'id': 100, 'node_id': 'I10', 'number': 10, 'body': 'Pinned work', 'state': 'open',
                                           'repository_url': 'https://api.github.com/repos/owner/repo'},
                            '/commits/' + 'f' * 40 + '/check-runs?per_page=100&filter=latest':
                                {'total_count': 1, 'check_runs': [{'id': 1, 'name': 'quality', 'head_sha': 'f' * 40,
                                 'status': 'completed', 'conclusion': 'success', 'app': {'id': 1}}]}})
        self.enterContext(patch.object(merge, 'mapped_candidate', return_value=self.plan))
        branch = copy.deepcopy(self.values['/commits/' + 'f' * 40 + '/check-runs?per_page=100&filter=latest'])
        branch['check_runs'][0]['head_sha'] = 'b' * 40
        self.values['/commits/' + 'b' * 40 + '/check-runs?per_page=100&filter=latest'] = branch
        self.enterContext(patch.object(merge, 'protection', return_value={'SIMULATED effective policy': True}))

    def test_lost_merge_response_adopts_exact_commit_and_ci_without_merge_or_closure(self):
        self.merged()
        result = restart.integration(self.assignment, self.api)
        assert result is not None
        self.assertEqual(result['integration']['integrated'], 'f' * 40)
        self.assertEqual(self.assignment['merge']['status'], 'merged')
        self.assertFalse(self.assignment['merge']['close_allowed'])
        self.api.write.assert_not_called()
        self.api.issue_state.assert_not_called()

    def test_merge_effect_absent_before_write_is_never_repeated(self):
        self.merged()
        self.pr['merged'] = False
        with self.assertRaises(RepositoryError):
            restart.integration(self.assignment, self.api)
        self.assertEqual(self.assignment['merge']['status'], 'pending')
        self.api.write.assert_not_called()

    def test_failed_integrated_checks_retain_proven_effect_but_not_delivery(self):
        self.merged()
        checks = self.values['/commits/' + 'f' * 40 + '/check-runs?per_page=100&filter=latest']
        checks['check_runs'][0]['conclusion'] = 'failure'
        with self.assertRaises(RepositoryError):
            restart.integration(self.assignment, self.api)
        self.assertEqual(self.assignment['merge']['integrated_evidence']['integrated'], 'f' * 40)
        self.assertFalse(self.assignment['merge']['close_allowed'])
        self.assertNotEqual(self.assignment['merge']['status'], 'delivered')
        self.api.write.assert_not_called()

    def public_effect(self, merged=False):
        """One CLI call from simulated persisted intent, not prior lifecycle calls."""
        from factory_v1.__main__ import main
        if merged:
            self.merged()  # Pure dictionaries + read mocks only.
        source = self.root / 'source'
        (source / '.git').mkdir(parents=True)
        (source / '.git/HEAD').write_text('a' * 40 + '\n')
        h = self.assignment['handoff']
        h['workspace'] = str(source)
        h['profile']['home'] = str(self.root / 'profile')
        h['issue']['body_sha256'] = hashlib.sha256(b'Pinned work').hexdigest()
        self.assignment['handoff_digest'] = assignments.digest(h)
        self.assignment['ticket_scope'] = {'project': 'p', 'iteration': 'i', 'repository': 'owner/repo',
                                            'repository_id': 123, 'issue_number': 10, 'issue_id': 100}
        self.assignment['revision'] = {'spec': {}, 'ticket_assignment': None, 'ticket_inputs': None}
        self.assignment['runtime'].update(container='retained-worker', controller_pid=111, controller_start='exited-owner')
        self.config['binding'] = publication.binding(self.assignment)
        self.config['operator_id'] = 'operator'
        if merged:
            self.assignment['merge']['authority']['operator_id'] = 'operator'
        item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'owner/repo', 'status': 'active',
                'execution_allowed': False, 'approval': {'operator_id': 'operator', 'reference': 'SIMULATED approval'}}
        state = self.root / 'state.sqlite'
        with closing(sqlite3.connect(state)) as db, db:
            db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
            db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(item)))
            db.execute('INSERT INTO role_assignments VALUES (?,?)', (self.assignment['assignment_id'], json.dumps(self.assignment)))
        repo = {'id': 123, 'full_name': 'owner/repo', 'name': 'repo', 'owner': {'login': 'owner'},
                'private': True, 'default_branch': 'main'}
        self.values[''] = repo
        self.values['/git/commits/' + 'a' * 40] = {'sha': 'a' * 40}
        github = Mock(base='https://api.github.com')
        github.request.side_effect = lambda path, *_: copy.deepcopy(self.values[path.removeprefix('/repos/owner/repo')])
        argv = ['factory', '--state', str(state), '--operator-id', 'operator', 'reconcile-restart',
                '--project', 'p', '--iteration', 'i', '--api-base', github.base]
        output = io.StringIO()
        with patch('sys.argv', argv), patch.object(repositories, 'GitHub', return_value=github), \
             patch.object(restart.Docker, 'inspect', return_value=None), patch.object(restart, 'process_identity', return_value=None), \
             patch('factory_v1.requirements.revalidate_inputs', return_value={}), \
             patch.object(tickets.gh, 'read_issue', return_value={'number': 10, 'id': 100, 'node_id': 'I10', 'body': 'Pinned work'}), redirect_stdout(output):
            main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'ready', result['blockers'])
        self.assertTrue(all(all(value is None for value in call.args[1:]) for call in github.request.call_args_list))
        with closing(sqlite3.connect(state)) as db:
            saved = json.loads(db.execute('SELECT payload FROM role_assignments').fetchone()[0])
        return saved

    def test_public_restart_recovers_lost_pr_response_without_remote_writes(self):
        saved = self.public_effect()
        self.assertEqual(saved['publication']['status'], 'published')
        self.assertEqual(saved['publication']['pr_number'], 20)
        self.assertNotIn('submitted_result', saved)

    def test_public_restart_recovers_lost_merge_response_without_merge_or_closure(self):
        saved = self.public_effect(merged=True)
        self.assertEqual(saved['merge']['status'], 'merged')
        self.assertEqual(saved['merge']['integrated_evidence']['integrated'], 'f' * 40)
        self.assertFalse(saved['merge']['close_allowed'])
