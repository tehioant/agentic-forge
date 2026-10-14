"""Planning assignment and exact publication validation, with explicit durable state."""
import base64
import copy
import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory_v1 import planning, repositories
from factory_v1.tests import test_tickets


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self)
        self.database, self.root = self.fixture.database, self.fixture.root
        self.item = self.fixture.item
        self.item.pop('ticket_work'); self.item.pop('handoff')
        self.item['stage'] = self.item['correlation']['stage'] = 'approved'
        self.fixture.persist()
        self.origin = copy.deepcopy(self.item['origin'])
        self.skills = []
        for name in ('grill-me', 'grilling', 'to-spec'):
            path = Path(__file__).resolve().parents[1] / 'planning_skills' / (name + '.md')
            self.skills.append({'name': name, 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.plan_request = {'origin': self.origin, 'expected_revision': 0, 'skills': self.skills}
        self.github = self.fixture.github
        self.repo = {'id': 123, 'full_name': 'example/product', 'name': 'product', 'owner': {'login': 'example'}, 'private': True}
        self.published = {'/repos/example/product': self.repo}
        self.github.request.side_effect = lambda path: copy.deepcopy(self.published[path])

    def inspect(self):
        return self.fixture.stored()

    def command(self, command, request):
        """Retained helper signature, backed by a direct real function."""
        try:
            function = planning.plan if command == 'plan' else planning.complete
            args = (self.database, self.inspect(), request)
            result = function(*args, *(() if command == 'plan' else (self.github,)))
            return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr='')
        except repositories.RepositoryError as error:
            self.database.rollback()
            return SimpleNamespace(returncode=2, stdout='', stderr=json.dumps({'error': error.code, 'message': str(error)}))

    def start(self):
        result = planning.plan(self.database, self.inspect(), self.plan_request)
        self.database.commit()
        return result

    def completion(self):
        item = self.start()
        milestone = ''.join('## ' + section + '\n\n' + content + '\n\n' for section, content in zip(planning.SECTIONS, (
            'Readers lose local notes.', 'Persist exact French notes.', '1. As a reader, retain notes.',
            'Rust selected by operator in message-2.', 'Test exact readback and unsafe-path refusal.',
            'Team sharing.', 'Finite milestone: exact persisted bytes.')))
        return {'assignment_id': item['planning']['assignment_id'], 'origin': self.origin, 'sufficient': True,
                'rationale': 'All choices settled.', 'reference': 'agent-turn-3', 'pending_questions': [],
                'documents': {'milestone.md': milestone, 'vision.md': 'Local notes first; sharing later.',
                              'provenance.md': 'Q1: language? Recommendation Python. Operator message-2: Rust, French CLI.'},
                'commit': 'a' * 40, 'issue_number': 7}

    def publication(self, request):
        self.published = {'/repos/example/product': self.repo, '/repos/example/product/git/commits/' + request['commit']: {'sha': request['commit']}}
        prefix = 'docs/factory/m1/' + request['assignment_id'] + '/'
        for name, content in request['documents'].items():
            raw = content.encode(); path = prefix + name
            self.published['/repos/example/product/contents/' + path + '?ref=' + request['commit']] = {
                'path': path, 'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(raw).decode(),
                'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
        self.issue_path = '/repos/example/product/issues/7'
        self.published[self.issue_path] = {'number': 7, 'id': 700, 'state': 'open', 'body': request['documents']['milestone.md'],
            'html_url': 'https://github.com/example/product/issues/7', 'repository_url': 'https://api.github.com/repos/example/product',
            'labels': [{'name': 'ready-for-agent'}]}

    def complete(self, request):
        return planning.complete(self.database, self.inspect(), request, self.github)

    def assert_refused(self, operation, before, code=None):
        with self.assertRaises(repositories.RepositoryError) as caught:
            operation()
        if code:
            self.assertEqual(caught.exception.code, code)
        self.database.rollback(); self.assertEqual(self.inspect(), before)

    def test_assignment_uses_actual_selected_skills_in_same_conversation(self):
        item = self.start(); work = item['planning']
        self.assertEqual(work['origin'], self.origin); self.assertEqual(work['status'], 'pending')
        self.assertEqual(list(work['skills']), ['grill-me', 'grilling', 'to-spec'])
        self.assertIn('Ask the whole frontier', work['skills']['grilling']['instructions'])
        self.assertIn('## Problem Statement', work['skills']['to-spec']['instructions'])
        self.assertIn('same originating conversation', work['instructions']); self.assertIn('no additional routine', work['instructions'])
        self.assertFalse(item['execution_allowed']); self.assertIsNone(item['correlation']['worker_run_id'])
        self.assertEqual(self.start(), item); self.assertEqual(self.inspect(), item)

    def test_missing_answers_remain_pending_without_publication_access(self):
        item = self.start()
        request = {'assignment_id': item['planning']['assignment_id'], 'origin': self.origin, 'sufficient': False,
                   'rationale': 'Language unresolved.', 'reference': 'agent-turn-1', 'pending_questions': ['Rust or Python?']}
        result = self.complete(request)
        self.assertEqual(result['planning']['pending_questions'], request['pending_questions'])
        self.assertNotIn('handoff', result); self.assertFalse(result['execution_allowed']); self.github.request.assert_not_called()
        self.assertEqual(self.complete(request), result); self.assertEqual(self.inspect(), result)

    def test_readback_hands_off_only_github_references_and_is_restart_idempotent(self):
        request = self.completion(); self.publication(request)
        result = self.complete(request)
        self.assertEqual(result['stage'], 'planning_complete'); self.assertEqual(result['handoff']['issue']['number'], 7)
        self.assertEqual(result['handoff']['commit'], request['commit']); self.assertNotIn('documents', result['planning'])
        self.assertNotIn('to-tickets', result['handoff']); self.assertFalse(result['execution_allowed'])
        self.assertEqual(self.complete(request), result); self.assertEqual(self.inspect(), result)
        self.assertTrue(all(len(c.args) == 1 for c in self.github.request.call_args_list))

    def test_missing_ambiguous_wrong_name_and_changed_skill_pins(self):
        before = self.inspect()
        for defect in ('missing', 'duplicate', 'hash', 'name', 'future', 'path'):
            request = copy.deepcopy(self.plan_request)
            if defect == 'missing': request['skills'].pop()
            if defect == 'duplicate': request['skills'].append(request['skills'][0])
            if defect == 'hash': request['skills'][0]['sha256'] = '0' * 64
            if defect == 'name': request['skills'][0]['path'] = request['skills'][1]['path']
            if defect == 'future': request['skills'].append({'name': 'to-tickets', 'path': '/never-read', 'sha256': '0' * 64})
            if defect == 'path': request['skills'][0]['path'] = str(self.root / 'absent')
            with self.subTest(defect=defect):
                self.assert_refused(lambda: planning.plan(self.database, self.item, request), before, 'skill_blocked')

    def test_docs_issue_and_commit_mismatches_never_complete(self):
        request = self.completion(); before = self.inspect()
        for defect in ('bytes', 'blob', 'path', 'symlink', 'base64', 'commit', 'body', 'number', 'url', 'repo', 'pr', 'closed', 'label', 'issue_id'):
            self.publication(request)
            doc = next(v for k, v in self.published.items() if '/contents/' in k); issue = self.published[self.issue_path]
            if defect == 'bytes': doc['content'] = base64.b64encode(b'stale').decode()
            if defect == 'blob': doc['sha'] = 'b' * 40
            if defect == 'path': doc['path'] = 'wrong.md'
            if defect == 'symlink': doc['type'] = 'symlink'
            if defect == 'base64': doc['content'] = '%%%'
            if defect == 'commit': self.published['/repos/example/product/git/commits/' + request['commit']]['sha'] = 'b' * 40
            if defect == 'body': issue['body'] += 'changed'
            if defect == 'number': issue['number'] = True
            if defect == 'url': issue['html_url'] = 'https://github.com/other/product/issues/7'
            if defect == 'repo': issue['repository_url'] = 'https://api.github.com/repos/other/product'
            if defect == 'pr': issue['pull_request'] = {}
            if defect == 'closed': issue['state'] = 'closed'
            if defect == 'label': issue['labels'] = []
            if defect == 'issue_id': issue['id'] = 0
            with self.subTest(defect=defect): self.assert_refused(lambda: self.complete(request), before, 'publication_mismatch')

    def test_new_assignment_invalidates_handoff_ticket_stage_and_reservation(self):
        request = self.completion(); self.publication(request); result = self.complete(request)
        result.update(ticket_work={'status': 'published'}, reservation={'status': 'reserved'})
        repositories.save_iteration(self.database, result); self.database.commit()
        replacement = planning.plan(self.database, result, {**self.plan_request, 'expected_revision': 1})
        self.assertEqual(replacement['planning']['revision'], 2); self.assertEqual(replacement['planning_history'][0]['status'], 'complete')
        self.assertNotIn('handoff', replacement); self.assertNotIn('ticket_work', replacement)
        self.assertEqual(replacement['ticket_history'], [{'status': 'published'}]); self.assertEqual(replacement['reservation']['status'], 'invalidated')
        self.assert_refused(lambda: self.complete(request), replacement, 'planning_conflict')

    def test_locked_reload_ignores_stale_onboarding_snapshot_for_assignment_and_completion(self):
        stale = self.inspect(); request = self.completion(); self.publication(request)
        result = planning.complete(self.database, stale, request, self.github)
        self.assertEqual(self.inspect(), result); self.assertEqual(result['planning']['status'], 'complete')
        replacement = planning.plan(self.database, stale, {**self.plan_request, 'expected_revision': 1})
        self.assertEqual(self.inspect(), replacement); self.assertEqual(replacement['planning']['revision'], 2)
        self.assertEqual(replacement['planning_history'][0]['status'], 'complete'); self.assertNotIn('handoff', replacement)

    def test_identity_endpoint_and_completed_issue_continuity(self):
        request = self.completion(); self.publication(request); before = self.inspect()
        self.repo['id'] = 999
        self.assert_refused(lambda: self.complete(request), before, 'repository_mismatch')
        self.repo['id'] = 123; result = self.complete(request)
        for field, value in (('id', 701), ('body', 'changed')):
            self.published[self.issue_path][field] = value
            self.assert_refused(lambda: self.complete(request), result, 'publication_mismatch')
            self.published[self.issue_path][field] = 700 if field == 'id' else request['documents']['milestone.md']
        self.github.base += '/other'; self.github.request.reset_mock()
        self.assert_refused(lambda: self.complete(request), result, 'capability_conflict'); self.github.request.assert_not_called()

    def test_malformed_origin_stale_revision_parent_and_completed_replacement_refused(self):
        request = self.completion(); before = self.inspect()
        for changes in ({'origin': {}}, {'assignment_id': 'old'}, {'sufficient': 'yes'}, {'pending_questions': ['open']},
                        {'commit': 'main'}, {'issue_number': True}, {'documents': []}, {'memory': 'other'}):
            with self.subTest(changes=changes): self.assert_refused(lambda: self.complete({**request, **changes}), before)
        self.item = self.inspect(); self.item['work_item_number'] = 7; self.fixture.item = self.item; self.fixture.persist()
        self.assert_refused(lambda: self.complete(request), self.item, 'invalid_planning')
        self.item.pop('work_item_number'); self.fixture.persist(); self.publication(request); complete = self.complete(request)
        changed = copy.deepcopy(request); changed['documents']['vision.md'] += 'silently new'
        self.assert_refused(lambda: self.complete(changed), complete, 'planning_conflict')
        pending = {k: request[k] for k in ('assignment_id', 'origin', 'sufficient', 'rationale', 'reference', 'pending_questions')}
        pending.update(sufficient=False, pending_questions=['new choice'])
        self.assert_refused(lambda: self.complete(pending), complete, 'planning_conflict')

    def test_assignment_requires_onboarding_and_exact_revision(self):
        self.item['repository_onboarding']['status'] = 'pending'; self.fixture.persist()
        self.assert_refused(lambda: planning.plan(self.database, self.item, self.plan_request), self.item, 'onboarding_required')
        self.item['repository_onboarding']['status'] = 'verified'; self.fixture.persist(); before = self.start()
        path = self.root / 'grill-me.md'; path.write_bytes(Path(self.skills[0]['path']).read_bytes())
        request = copy.deepcopy(self.plan_request); request['skills'][0]['path'] = str(path)
        self.assert_refused(lambda: planning.plan(self.database, self.item, request), before, 'planning_conflict')

    def test_changed_skill_after_assignment_blocks_completion_without_reads(self):
        path = self.root / 'grilling.md'; path.write_bytes(Path(self.skills[1]['path']).read_bytes()); self.skills[1]['path'] = str(path)
        request = self.completion(); before = self.inspect(); path.write_text('Changed instructions')
        self.assert_refused(lambda: self.complete(request), before, 'skill_blocked'); self.github.request.assert_not_called()

    def test_wrong_operator_is_refused_at_current_state_seam(self):
        from factory_v1 import assignments
        before = self.inspect()
        self.assert_refused(lambda: assignments.current(self.database, 'product', 'm1', '99'), before, 'approval_required')
