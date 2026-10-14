"""Small mocked components only: no workers, HTTP servers or controller journeys."""
import base64
import copy
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from factory_v1 import assignments, attention, delivery, iteration, merge, planning, skills
from factory_v1.__main__ import main, register_iteration
from factory_v1.errors import IntakeError
from factory_v1.repositories import RepositoryError, save_iteration


ORIGIN = {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '122', 'scope_id': '121'}
SHA = 'a' * 40


def work_row(number=1):
    return {'number': number, 'id': number * 10, 'node_id': 'I_' + str(number), 'membership_id': 'M_' + str(number),
            'status': 'done', 'issue_state': 'closed', 'state_reason': 'completed', 'blockers': [],
            'contract_valid': True, 'held': False, 'admissible': False}


def context():
    return {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product', 'idea': 'notes',
            'approval': {'operator_id': '42', 'reference': 'approved-intake'}, 'origin': copy.deepcopy(ORIGIN),
            'status': 'active', 'stage': 'delivery', 'execution_allowed': False,
            'correlation': {'project_id': 'product', 'iteration_id': 'm1', 'stage': 'delivery',
                            'control_run_id': 'controller', 'evidence_id': 'intake'},
            'repository_onboarding': {'status': 'verified', 'metadata': {'id': 7}},
            'handoff': {'repository': 'example/product', 'commit': SHA, 'issue': {'number': 7, 'id': 70}, 'documents': {}},
            'ticket_work': {'publication_complete': True, 'published': {'T1': {'number': 1}},
                            'inputs': {'documents': {'milestone.md': {'url': 'https://example.test/spec', 'sha256': 'b' * 64}}}}}


class IterationComponents(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state.sqlite'
        self.db = sqlite3.connect(self.state)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
        self.item = context()
        self.db.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'm1', json.dumps(self.item)))
        self.db.commit()
        self.github = Mock(base='http://127.0.0.1:9876')
        self.observed = {'items': [work_row()], 'foreign_active': [], 'active': [], 'eligible': [], 'authority': 'github'}
        self.saved = {'config': {'api_base': self.github.base}, 'latest': {'status': 'healthy'}}
        self.health = {'status': 'healthy', 'main': SHA, 'deployment': {'id': 9, 'status': {'state': 'success'}}, 'checks': [], 'workflows': []}
        self.delivered = {'issue': {'number': 1, 'id': 10, 'url': 'https://github.com/example/product/issues/1'},
                          'pr': {'number': 2, 'url': 'https://github.com/example/product/pull/2'},
                          'assignment_id': 'c' * 64, 'integration': {'integrated': SHA}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for target, value in [('iteration.frontier', self.observed), ('delivery.monitor_state', self.saved),
                              ('delivery.validate_persisted', None), ('delivery.observe', self.health),
                              ('iteration.delivered_item', self.delivered), ('merge.load_config', {'issue': 1}),
                              ('tickets.gh.read_issue', {'body': '## Status\n\ndone\n', 'state': 'closed'})]:
            self.mocks[target] = self.stack.enter_context(patch('factory_v1.' + target, return_value=value))

    def stored(self, iteration_id='m1'):
        return json.loads(self.db.execute('SELECT payload FROM iterations WHERE iteration_id=?', (iteration_id,)).fetchone()[0])

    def store(self):
        save_iteration(self.db, self.item)
        self.db.commit()

    def complete(self, request=None):
        return iteration.complete(self.db, 'product', 'm1', '42', request or {'merge_configs': ['private-merge.json']}, self.github)

    def test_report_wait_and_outbox_are_atomic_restart_idempotent_and_silent(self):
        self.item['bug_work'] = {'future': {'discovery': {'scope': 'deferred', 'symptoms': 'Optional formatting'}, 'status': 'synthesis_pending'}}
        self.store()
        report = self.complete()
        stored = self.stored()
        self.assertEqual(stored['status'], 'waiting-for-operator')
        self.assertFalse(stored['execution_allowed'])
        self.assertNotIn('planning', stored)
        self.assertNotIn('next_iteration', stored)
        self.assertEqual(report['report']['verification'], 'simulation')
        self.assertEqual(report['report']['bugs'][0]['discovery']['scope'], 'deferred')
        records = attention.records(self.db, 'product', 'm1', stored)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['destination'], ORIGIN)
        self.assertEqual(records[0]['state'], 'pending')
        self.assertIn(SHA, records[0]['event']['message'])
        self.assertEqual(self.complete(), report)
        self.assertEqual(attention.records(self.db, 'product', 'm1', stored), records)
        self.github.request.assert_not_called()
        with patch('factory_v1.attention.save', side_effect=RuntimeError('simulated crash')):
            self.db.execute('DELETE FROM notifications')
            self.item = context()
            self.store()
            with self.db, self.assertRaises(RuntimeError):
                self.complete()
        self.assertNotIn('completion', self.stored())
        self.assertEqual(attention.records(self.db, 'product', 'm1', self.item), [])

    def test_tracker_states_and_missing_original_scope_refuse_without_outbox(self):
        for field, value in [('status', 'blocked'), ('status', 'ready'), ('status', 'deferred'),
                             ('issue_state', 'open'), ('held', True), ('contract_valid', False), ('state_reason', 'not_planned')]:
            with self.subTest(field=field, value=value):
                observed = copy.deepcopy(self.observed)
                observed['items'][0][field] = value
                self.mocks['iteration.frontier'].return_value = observed
                with self.assertRaises(RepositoryError):
                    self.complete()
                self.assertNotIn('completion', self.stored())
        for observed in (dict(self.observed, items=[]), dict(self.observed, foreign_active=[8])):
            self.mocks['iteration.frontier'].return_value = observed
            with self.assertRaises(RepositoryError):
                self.complete()
        self.assertEqual(attention.records(self.db, 'product', 'm1', self.item), [])

    def test_required_set_cannot_be_selected_by_caller_and_deferred_bug_stays_nonexec(self):
        self.observed['items'].append(work_row(2))
        with self.assertRaises(RepositoryError):
            self.complete()
        deferred = work_row(2)
        deferred.update(status='deferred', held=True, issue_state='open')
        self.observed['items'][1] = deferred
        self.mocks['tickets.gh.read_issue'].side_effect = lambda github, repo, n: {'body': '## Bug scope\n\ndeferred\n', 'state': 'closed' if n == 1 else 'open'}
        report = self.complete()
        self.assertEqual(report['report']['deferred_issues'], [2])
        self.assertEqual([w['issue']['number'] for w in report['report']['delivered']], [1])
        self.assertEqual(self.stored()['ticket_work']['published'], {'T1': {'number': 1}})

    def test_unknown_failed_health_incident_and_pending_decisions_refuse(self):
        for status in ('unknown', 'failed'):
            self.mocks['delivery.observe'].return_value = dict(self.health, status=status)
            with self.assertRaises(RepositoryError):
                self.complete()
        self.mocks['delivery.observe'].return_value = self.health
        self.item['main_incident'] = {'status': 'active'}
        self.store()
        with self.assertRaises(RepositoryError):
            self.complete()
        self.item.pop('main_incident')
        self.store()
        with patch('factory_v1.iteration.pending_decisions', return_value=[{'decision_id': 'unanswered', 'issue_number': 1}]):
            with self.assertRaises(RepositoryError):
                self.complete()
        self.assertNotIn('completion', self.stored())

    def test_revalidation_after_health_and_transaction_guard_refuse_races(self):
        self.mocks['delivery.observe'].side_effect = [self.health, dict(self.health, status='unknown')]
        with self.assertRaises(RepositoryError):
            self.complete()
        def pause_after_io(*args):
            changed = self.stored()
            changed['status'] = 'paused'
            save_iteration(self.db, changed)
            self.db.commit()
            return self.health
        count = 0
        def observe(config):
            nonlocal count
            count += 1
            return pause_after_io() if count == 2 else self.health
        self.mocks['delivery.observe'].side_effect = observe
        with self.assertRaisesRegex(RepositoryError, 'changed across external'):
            self.complete()
        self.db.rollback()
        self.assertEqual(self.stored()['status'], 'paused')
        self.assertNotIn('completion', self.stored())

    def test_plain_plan_and_intake_cannot_bypass_wait(self):
        self.complete()
        item = self.stored()
        with self.db, self.assertRaises(RepositoryError):
            planning.plan(self.db, item, {'origin': ORIGIN, 'expected_revision': 0, 'skills': []})
        with self.db, self.assertRaises(RepositoryError):
            planning.complete(self.db, item, {}, self.github)
        with self.db, self.assertRaises(IntakeError):
            register_iteration(self.db, dict(context(), iteration_id='m2'))

    def test_every_required_item_gets_two_fresh_delivery_checks(self):
        self.observed['items'].append(work_row(2))
        self.mocks['merge.load_config'].side_effect = lambda path, operator: {'issue': 1 if path == 'one.json' else 2}
        def delivered(database, item, row, *args):
            value = copy.deepcopy(self.delivered)
            value['issue']['number'] = row['number']
            return value
        self.mocks['iteration.delivered_item'].side_effect = delivered
        report = self.complete({'merge_configs': ['one.json', 'two.json']})
        self.assertEqual([r['issue']['number'] for r in report['report']['delivered']], [1, 2])
        self.assertEqual([c.args[2]['number'] for c in self.mocks['iteration.delivered_item'].call_args_list], [1, 2, 1, 2])

    def test_unreconciled_worker_or_required_hold_prevents_completion(self):
        assignment = {'ticket_scope': {'project': 'product', 'iteration': 'm1'},
                      'runtime': {'status': 'running', 'container_removed': False}}
        with patch.object(assignments, 'rows', return_value=[assignment]), self.assertRaises(RepositoryError):
            self.complete()
        for source in ('requirements', 'diagnosis', 'bugs'):
            with patch('factory_v1.' + source + '.held_numbers', return_value={1}), self.assertRaises(RepositoryError):
                self.complete()
        self.assertNotIn('completion', self.stored())

    def test_reservation_is_archived_not_left_as_an_executable_claim(self):
        self.item['reservation'] = {'issue_number': 1, 'status': 'reserved'}
        self.store()
        self.complete()
        stored = self.stored()
        self.assertNotIn('reservation', stored)
        self.assertEqual(stored['reservation_history'], [self.item['reservation']])

    def test_public_cli_routes_completion_and_structured_malformed_refusals(self):
        path = self.root / 'request.json'
        path.write_text('{"merge_configs":["private-merge.json"]}')
        args = ['factory_v1', '--state', str(self.state), '--operator-id', '42', 'complete-iteration',
                '--project', 'product', '--iteration', 'm1', '--request', str(path), '--api-base', self.github.base]
        output = io.StringIO()
        with patch.object(sys, 'argv', args), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())['report']['iteration_id'], 'm1')
        path.write_text('{"merge_configs":[],"merge_configs":[]}')
        with patch.object(sys, 'argv', args), self.assertRaises(IntakeError):
            main()


class DeliveredEvidenceComponents(unittest.TestCase):
    def setUp(self):
        self.item = context()
        self.row = work_row()
        self.config = {'issue': 1, 'base': 'main', 'repository': 'example/product', 'repository_id': 7,
                       'api_base': 'https://api.github.com', 'bearer': 'fixture', 'pr_number': 2,
                       'standards_assignment': 's', 'spec_assignment': 'p'}
        self.issue = {'number': 1, 'id': 10, 'node_id': 'I_1', 'state': 'closed', 'state_reason': 'completed', 'body': 'accepted requirements'}
        self.evidence = {'candidate': 'b' * 40, 'integrated': SHA, 'tree': 'c' * 40, 'parents': ['d' * 40, 'b' * 40], 'main': SHA}
        self.prior = {'assignment_id': 'c' * 64, 'revision': {'spec': self.item['handoff']},
                      'handoff': {'issue': {'body_sha256': hashlib.sha256(self.issue['body'].encode()).hexdigest()}},
                      'merge': {'status': 'delivered', 'failure': None, 'close_allowed': True,
                                'authority': {k: v for k, v in self.config.items() if k != 'bearer'},
                                'integrated_evidence': self.evidence, 'delivery_checks': ['exact-integrated-check']}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for name, value in [('load_config', self.config), ('chain', (self.prior, {}, Path('/fixture'))),
                            ('MergeGitHub', Mock()), ('issue', self.issue), ('require_publication', None),
                            ('mapped_candidate', {}), ('target', {}), ('merged_evidence', self.evidence),
                            ('protection', {}), ('checks', ['exact-integrated-check'])]:
            self.mocks[name] = self.stack.enter_context(patch.object(merge, name, return_value=value))

    def check(self):
        return iteration.delivered_item(None, self.item, self.row, '/fixture.json', '42', SimpleNamespace(base='https://api.github.com'), SHA)

    def test_external_evidence_and_original_chain_are_revalidated(self):
        result = self.check()
        self.assertEqual(result['integration']['integrated'], SHA)
        self.assertEqual(result['reviews'], ['s', 'p'])
        self.mocks['chain'].assert_called_once()
        self.mocks['require_publication'].assert_called_once()
        self.mocks['checks'].assert_called_once_with(self.mocks['MergeGitHub'].return_value, self.config, SHA)

    def test_closed_issue_and_delivered_boolean_never_replace_original_proof(self):
        for boundary in ('chain', 'mapped_candidate', 'target', 'merged_evidence', 'protection', 'checks', 'require_publication'):
            with self.subTest(boundary=boundary):
                self.mocks[boundary].side_effect = RepositoryError('missing_evidence', 'fixture refusal')
                with self.assertRaises(RepositoryError):
                    self.check()
                self.mocks[boundary].side_effect = None
        for key, value in [('status', 'merged'), ('status', 'revalidation-required'), ('close_allowed', False), ('failure', {'code': 'removed'})]:
            old = self.prior['merge'][key]
            self.prior['merge'][key] = value
            with self.subTest(key=key), self.assertRaises(RepositoryError):
                self.check()
            self.prior['merge'][key] = old

    def test_reopened_different_requirements_wrong_scope_and_integration_refuse(self):
        for field, value in [('state', 'open'), ('body', 'changed'), ('id', 999), ('state_reason', 'not_planned')]:
            old = self.issue[field]
            self.issue[field] = value
            with self.subTest(field=field), self.assertRaises(RepositoryError):
                self.check()
            self.issue[field] = old
        self.mocks['merged_evidence'].return_value = dict(self.evidence, main='e' * 40)
        with self.assertRaises(RepositoryError):
            self.check()
        self.mocks['merged_evidence'].return_value = self.evidence
        self.mocks['checks'].return_value = ['new-check']
        with self.assertRaises(RepositoryError):
            self.check()


class ContinuationComponents(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
        self.db.execute('CREATE TABLE notifications (project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project, iteration, event_id))')
        self.item = context()
        report = {k: self.item[k] for k in ('project_id', 'iteration_id', 'repository')}
        report_id = assignments.digest(report)
        self.item.update(status='waiting-for-operator', completion={'report_id': report_id,
                         'event_id': 'completion-' + report_id, 'report': report})
        self.db.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'm1', json.dumps(self.item)))
        self.db.commit()
        self.request = {'iteration_id': 'm2', 'event_id': self.item['completion']['event_id'], 'operator_id': '42', 'origin': ORIGIN,
                        'reference': 'later-user-message', 'message_id': '102', 'input': 'Add search to this product', 'skills': []}
        # Independent immutable fixture snapshots, not actual host skill discovery.
        snapshots = Path(__file__).parent / 'planning_skill_snapshots'
        for name in ('grill-me', 'grilling', 'to-spec'):
            target = self.root / 'skills' / name / 'SKILL.md'
            target.parent.mkdir(parents=True)
            target.write_bytes((snapshots / (name + '.md')).read_bytes())
            self.request['skills'].append({'name': name, 'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(skills, 'INSTALLED_ROOT', self.root / 'skills'))
        self.record = {'state': 'delivered', 'receipt': {'reference': 'earlier-readback', 'message_id': '101'}}
        self.get_record = self.stack.enter_context(patch.object(attention, 'get_record', return_value=self.record))

    def start(self, request=None):
        with self.db:
            return iteration.next_iteration(self.db, 'product', 'm1', '42', request or self.request)

    def test_explicit_same_product_input_starts_only_pinned_pending_interview(self):
        result = self.start()
        self.assertEqual(result['project_id'], self.item['project_id'])
        self.assertEqual(result['repository'], self.item['repository'])
        self.assertEqual(result['origin'], ORIGIN)
        self.assertEqual(result['planning']['continuation']['previous_spec'], self.item['handoff'])
        self.assertEqual(result['planning']['continuation']['dependencies']['grill-me'], ['grilling'])
        self.assertEqual(result['stage'], 'planning_pending')
        self.assertNotIn('handoff', result)
        self.assertNotIn('ticket_work', result)
        self.assertFalse(result['execution_allowed'])
        self.assertEqual(self.start(), result)
        with self.db, self.assertRaises(RepositoryError):
            planning.plan(self.db, result, result['planning']['request'])

    def test_wrong_operator_origin_event_empty_input_and_unverified_delivery_refuse(self):
        for changes in ({'operator_id': '99'}, {'origin': dict(ORIGIN, thread_id='999')}, {'event_id': 'other'},
                        {'input': ''}, {'reference': ''}, {'iteration_id': 'm1'}, {'repository': 'other/product'},
                        {'message_id': '100'}, {'message_id': '101'}, {'message_id': '00102'}, {'message_id': '１０２'}):
            with self.subTest(changes=changes), self.assertRaises(RepositoryError):
                self.start(dict(self.request, **changes))
        for state in ('pending', 'accepted', 'uncertain'):
            self.record['state'] = state
            with self.subTest(state=state), self.assertRaises(RepositoryError):
                self.start()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM iterations').fetchone()[0], 1)

    def test_skill_source_alternate_repins_missing_dependencies_and_drift_refuse(self):
        for defect in ('missing', 'repin', 'alternate', 'drift'):
            request = copy.deepcopy(self.request)
            source = Path(request['skills'][1]['path'])
            original = source.read_bytes()
            if defect == 'missing': request['skills'].pop(1)
            if defect == 'repin': request['skills'][1]['sha256'] = '0' * 64
            if defect == 'alternate':
                alternate = self.root / 'alternate.md'
                alternate.write_bytes(original)
                request['skills'][1]['path'] = str(alternate)
            if defect == 'drift': source.write_bytes(original + b'\nUnreviewed change')
            with self.subTest(defect=defect), self.assertRaises(RepositoryError):
                self.start(request)
            source.write_bytes(original)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM iterations').fetchone()[0], 1)

    def evidence(self, item, sufficient=True):
        selected = item['planning']['skills']
        continuation = item['planning']['continuation']
        names = ['grill-me', 'grilling'] + (['to-spec'] if sufficient else [])
        loads = {selected[n]['path']: selected[n]['sha256'] for n in names}
        loads.update(continuation['input_loads'])
        loads.update({'planning:input': assignments.digest(continuation['input']),
                      'planning:adaptation': hashlib.sha256(planning.NEXT_ADAPTATION.encode()).hexdigest()})
        docs = {name: '# Updated ' + name for name in ('milestone.md', 'vision.md', 'provenance.md')}
        request = {'sufficient': sufficient, 'documents': docs, 'pending_questions': []}
        request['execution'] = {'assignment_id': item['planning']['assignment_id'], 'run_id': 'conversation-run', 'origin': ORIGIN,
            'adaptation': planning.NEXT_ADAPTATION,
            'loads': [{'source': s, 'sha256': h, 'tool_reference': 'read-' + str(n)} for n, (s, h) in enumerate(loads.items())],
            'rounds': [{'reference': 'agent-round', 'complete': True, 'questions': [{'number': 1, 'question': 'Which search?',
                         'recommendation': 'Local text', 'answer': 'Local text', 'operator_id': '42', 'reference': 'user-answer'}]}],
            'work': [{'skill': n, 'tool_reference': 'work-' + n, 'result': 'Observable ' + n + ' work'} for n in names],
            'result_digest': assignments.digest(docs)}
        return request

    def test_correlated_load_work_answers_and_round_gate_are_required(self):
        item = self.start()
        request = self.evidence(item)
        self.assertEqual(planning.continuation_evidence(item['planning'], request, item['planning']['skills']), request['execution'])
        for defect in ('assignment', 'origin', 'load', 'hash', 'work', 'answer', 'actor', 'gate', 'result', 'adaptation'):
            bad = copy.deepcopy(request)
            evidence = bad['execution']
            if defect == 'assignment': evidence['assignment_id'] = 'stale'
            if defect == 'origin': evidence['origin'] = {}
            if defect == 'load': evidence['loads'].pop()
            if defect == 'hash': evidence['loads'][0]['sha256'] = '0' * 64
            if defect == 'work': evidence['work'] = []
            if defect == 'answer': evidence['rounds'][0]['questions'][0]['answer'] = None
            if defect == 'actor': evidence['rounds'][0]['questions'][0]['operator_id'] = 'worker'
            if defect == 'gate': evidence['rounds'][0]['complete'] = False
            if defect == 'result': evidence['result_digest'] = '0' * 64
            if defect == 'adaptation': evidence['adaptation'] = 'invented'
            with self.subTest(defect=defect), self.assertRaises(RepositoryError):
                planning.continuation_evidence(item['planning'], bad, item['planning']['skills'])

    def test_pending_interview_never_reads_publication_and_complete_updates_spec_pointers(self):
        item = self.start()
        request = self.evidence(item, sufficient=False)
        request.pop('documents')
        request.update(assignment_id=item['planning']['assignment_id'], origin=ORIGIN,
                       rationale='Need the actual answer', reference='agent-pending', pending_questions=['Which search?'])
        request['execution']['result_digest'] = assignments.digest(request['pending_questions'])
        question = request['execution']['rounds'][0]['questions'][0]
        question.update(answer=None, operator_id=None, reference=None)
        request['execution']['rounds'][0]['complete'] = False
        github = Mock(base='http://127.0.0.1:9876')
        with self.db:
            pending = planning.complete(self.db, item, request, github)
        self.assertEqual(pending['planning']['pending_questions'], ['Which search?'])
        self.assertNotIn('handoff', pending)
        github.request.assert_not_called()
        request = self.evidence(item)
        request.update(assignment_id=item['planning']['assignment_id'], origin=ORIGIN,
                       rationale='Answered current frontier', reference='agent-sufficient', commit='e' * 40, issue_number=8)
        request['documents']['milestone.md'] = '\n'.join('## ' + section + '\nSubstantive agreed text.\n' for section in planning.SECTIONS)
        request['execution']['result_digest'] = assignments.digest(request['documents'])
        def read(path):
            if '/git/commits/' in path:
                return {'sha': request['commit']}
            if '/contents/' in path:
                name = path.split('/')[-1].split('?')[0]
                raw = request['documents'][name].encode()
                return {'type': 'file', 'path': item['planning']['docs_path'] + '/' + name, 'encoding': 'base64',
                        'content': base64.b64encode(raw).decode(),
                        'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
            return {'number': 8, 'id': 80, 'state': 'open', 'body': request['documents']['milestone.md'],
                    'html_url': 'https://github.com/example/product/issues/8',
                    'repository_url': 'https://api.github.com/repos/example/product', 'labels': [{'name': 'ready-for-agent'}]}
        github.request.side_effect = read
        with patch.object(planning, 'reconcile'), self.db:
            completed = planning.complete(self.db, pending, request, github)
        self.assertEqual(completed['stage'], 'planning_complete')
        self.assertEqual(completed['handoff']['commit'], 'e' * 40)
        self.assertEqual(completed['handoff']['issue']['number'], 8)
        self.assertEqual(completed['planning']['execution'], request['execution'])
        previous = json.loads(self.db.execute("SELECT payload FROM iterations WHERE iteration_id='m1'").fetchone()[0])
        self.assertEqual(previous['handoff'], self.item['handoff'])
        self.assertNotIn('ticket_work', completed)
        self.assertFalse(completed['execution_allowed'])

    def test_stale_spec_pointer_catalog_only_and_conflicting_next_input_refuse(self):
        item = self.start()
        request = self.evidence(item)
        request.update(assignment_id=item['planning']['assignment_id'], origin=ORIGIN,
                       rationale='Ready', reference='agent', commit=SHA, issue_number=7)
        request['documents']['milestone.md'] = '\n'.join('## ' + section + '\nReal text.\n' for section in planning.SECTIONS)
        request['execution']['result_digest'] = assignments.digest(request['documents'])
        with self.db, self.assertRaises(RepositoryError):
            planning.complete(self.db, item, request, Mock())
        request.pop('execution')
        with self.db, self.assertRaises(RepositoryError):
            planning.complete(self.db, item, request, Mock())
        with self.assertRaises(RepositoryError):
            self.start(dict(self.request, input='Conflicting new batch'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM iterations').fetchone()[0], 2)

    def test_completed_iteration_claim_retirement_is_not_issue_closure(self):
        assignment = {'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 1, 'issue_id': 10},
                      'runtime': {'status': 'complete', 'container_removed': True}}
        self.assertFalse(assignments.completed_iteration_claim(self.db, assignment))
        report = {'delivered': [{'issue': {'number': 1, 'id': 10}}]}
        self.item['completion'] = {'report_id': assignments.digest(report), 'report': report}
        save_iteration(self.db, self.item)
        self.db.commit()
        self.assertTrue(assignments.completed_iteration_claim(self.db, assignment))
        assignment['runtime']['container_removed'] = False
        self.assertFalse(assignments.completed_iteration_claim(self.db, assignment))


if __name__ == '__main__':
    unittest.main()
