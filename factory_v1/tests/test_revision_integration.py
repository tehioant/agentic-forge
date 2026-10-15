"""Focused revision boundaries with simulated external reads/effects, not native acceptance."""
import base64
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import controls, planning, requirement_execution as synthesis
from factory_v1 import requirement_publication as publication, requirements, tickets
from factory_v1.publication import object_sha
from factory_v1.repositories import RepositoryError, save_iteration


ROOT = Path(__file__).resolve().parents[1]


def revision_inputs(commit='a' * 40, parent=3):
    docs = {'milestone.md': '## User Stories\n1. Persist notes locally.\n',
            'vision.md': 'Future work is outside this revision.', 'provenance.md': 'Operator agreed to retain notes.'}
    return {'handoff': {'commit': commit, 'issue': {'number': parent}}, 'documents': {
        name: {'content': content, 'url': f'https://github.com/example/product/blob/{commit}/docs/{name}',
               'sha256': hashlib.sha256(content.encode()).hexdigest()} for name, content in docs.items()}}


def lean_body(inputs):
    return ('## Change\n\nRetain the exact café note.\n\n## Done\n\n- Reopen café unchanged.\n\n'
            f"## Context\n\nParent #{inputs['handoff']['issue']['number']}; first milestone. Stories: US-1.\n\n"
            f"[Milestone]({inputs['documents']['milestone.md']['url']})\n\n"
            '## Constraint\n\nNo unrelated changes.\n\n## Blocked by\n\nNone.\n\n'
            '<!-- factory-ticket:revision:notes -->\n')


class RevisionBodyTests(unittest.TestCase):
    def test_lean_update_family_keeps_unrelated_bytes_and_admits_only_new_pins(self):
        old, new = revision_inputs(), revision_inputs('d' * 40, 4)
        ticket = {'title': 'Revised interruption', 'desired_behavior': 'Retain interruption evidence.',
                  'references': [{'requirement': 'US-1'}],
                  'acceptance_criteria': ['A paused revision cannot advance.']}
        for newline in ('\n', '\r\n'):
            for headings in ('## ', '## \t'):
                for footer in ('<!---->', '  <!--neighbor-->\t'):
                    body = (lean_body(old) + '\n' + footer).replace('## ', headings).replace('\n', newline)
                    issue = {'title': 'Retain notes', 'body': body}
                    with self.subTest(newline=newline, headings=headings, footer=footer):
                        revised = publication.updated_body(issue, ticket, new, old)
                        self.assertEqual(tickets.external_contract({**issue, 'title': ticket['title'], 'body': revised}, new),
                                         'contracted')
                        self.assertFalse(tickets.external_contract({**issue, 'body': revised}, old))
                        for name in ('Constraint', 'Blocked by'):
                            spans, revised_spans = tickets.section_spans(body), tickets.section_spans(revised)
                            assert spans is not None and revised_spans is not None
                            self.assertEqual(body[slice(*spans[name])], revised[slice(*revised_spans[name])])
                        self.assertTrue(revised.endswith(body[body.index('<!--'):]))
                        if newline == '\r\n':
                            self.assertNotIn('\n', revised.replace('\r\n', ''))


class RevisionIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = sqlite3.connect(self.root / 'state.sqlite')
        (self.root / 'state.sqlite').chmod(0o600)
        self.addCleanup(self.database.close)
        self.database.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.database.execute('CREATE TABLE role_assignments(assignment_id TEXT, payload TEXT)')
        self.old_inputs, self.new_inputs = revision_inputs(), revision_inputs('d' * 40, 4)
        self.direction = {'operator_id': '42', 'origin': {'conversation': 'revision-fixture'},
                          'reference': 'SIMULATED operator direction', 'content': 'Retain durable notes.'}
        descriptor = lambda name, directory: {'name': name, 'path': str(ROOT / directory / (name + '.md')),
            'sha256': hashlib.sha256((ROOT / directory / (name + '.md')).read_bytes()).hexdigest()}
        self.skills = [descriptor(name, 'planning_skills') for name in ('grill-me', 'grilling', 'to-spec')]
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
                     'approval': {'operator_id': '42', 'reference': 'SIMULATED approval'},
                     'origin': self.direction['origin'], 'repository': 'example/product',
                     'handoff': self.old_inputs['handoff'], 'planning': {'request': {}},
                     'ticket_work': {'assignment_id': 'old-tickets', 'input_digest': 'old-inputs',
                         'inputs': self.old_inputs, 'request': {'skill': descriptor('to-tickets', 'ticket_skills'),
                             'tracker': {'statuses': {s: s.title() for s in ('ready', 'blocked', 'deferred', 'active')}}},
                         'frontier': {'items': [
                             {'number': 10, 'blockers': [], 'status': 'ready', 'membership_id': 'M10'},
                             {'number': 11, 'blockers': [10], 'status': 'ready', 'membership_id': 'M11'}]}}}
        self.issues = {n: {'number': n, 'id': n + 100, 'node_id': f'I{n}', 'title': f'Retain notes {n}',
                           'body': lean_body(self.old_inputs), 'state': 'open', 'labels': []} for n in (10, 11)}
        self.statuses = {10: 'Ready', 11: 'Ready'}
        self.board = {'project_id': 'P1', 'status': {'id': 'STATUS',
                        'options': {s: s for s in ('Ready', 'Blocked', 'Deferred', 'Active')}},
                      'scope': {'options': {'m1': 'S1'}}}
        self.github = Mock()
        self.github.request.side_effect = AssertionError('Unexpected external request')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tickets.gh, 'read_issue',
            side_effect=lambda github, repo, number: copy.deepcopy(self.issues[number])))
        self.stack.enter_context(patch.object(tickets.gh, 'resolve_board', return_value=self.board))
        self.membership = self.stack.enter_context(patch.object(tickets.gh, 'membership', side_effect=self.member))
        self.set_field = self.stack.enter_context(patch.object(tickets.gh, 'set_field', side_effect=self.change_status))
        self.dependencies = self.stack.enter_context(patch.object(tickets.gh, 'dependencies',
            side_effect=lambda github, repo, number: [self.issues[10]] if number == 11 else []))
        self.pinned = self.stack.enter_context(patch.object(tickets, 'pinned_inputs', return_value=self.old_inputs))
        self.stack.enter_context(patch.object(tickets, 'frontier', side_effect=lambda db, item, gh: item))
        self.save(self.item)
        helper = self.root / 'inert-adapter'
        helper.write_text('# Never executed: invoke is the mocked external transport.\n')
        helper.chmod(0o700)
        self.config = self.root / 'capability.json'
        self.config.write_text(json.dumps({'operator_id': '42', 'reference': 'SIMULATED approval',
            'execution_reference': 'SIMULATED provenance, not native execution', 'argv': [str(helper)],
            'sha256': hashlib.sha256(helper.read_bytes()).hexdigest(), 'timeout': 1}))
        self.config.chmod(0o600)

    def save(self, item):
        if not self.database.execute('SELECT 1 FROM iterations').fetchone():
            self.database.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'm1', json.dumps(item)))
        else:
            save_iteration(self.database, item)
        self.database.commit()

    def read(self):
        return json.loads(self.database.execute('SELECT payload FROM iterations').fetchone()[0])

    def member(self, github, board, repository, issue):
        n = issue['number']
        return {'id': f'M{n}', 'status': self.statuses[n], 'scope': 'S1'}

    def change_status(self, github, project, member, field, option):
        self.statuses[int(member[1:])] = option

    def interrupt(self, action='pause', malformed=False):
        controls.request(self.database, 'product', 'm1', '42', action, 'SIMULATED exact boundary')
        if malformed:
            _, path = controls.paths(self.database, 'product', 'm1')
            assert path is not None
            record = json.loads(path.read_text())
            record.pop('request_id')
            path.write_text(json.dumps(record))

    def resume(self, malformed=False):
        if malformed:
            _, path = controls.paths(self.database, 'product', 'm1')
            assert path is not None
            record = json.loads(path.read_text())
            record['request_id'] = 'repaired-fixture'
            path.write_text(json.dumps(record))
        # Revision components mock the independent restart observation boundary.
        with patch('factory_v1.restart.reconcile', return_value={'status': 'ready'}):
            controls.resume(self.database, 'product', 'm1', '42', 'SIMULATED explicit resume', self.github)

    def refused(self, operation, code='iteration_paused'):
        with self.assertRaises(RepositoryError) as caught:
            # Match the controller transaction rollback for pre-checkpoint assignment edits.
            with self.database:
                operation()
        self.assertEqual(caught.exception.code, code)
        return self.read()

    def install_record(self, status='held'):
        record = {'request': {'change_id': 'durable-notes'}, 'kind': 'material', 'status': status,
                  'affected': [10, 11], 'requirements': ['US-1'], 'resolution': self.direction,
                  'issues': {str(n): requirements.issue_pin(i) for n, i in self.issues.items()},
                  'old_issues': copy.deepcopy(self.issues), 'old_revision': {
                      str(n): requirements.authority(self.item, n) for n in self.issues},
                  'memberships': {str(n): f'M{n}' for n in self.issues},
                  'previous_statuses': {'10': 'ready', '11': 'ready'}, 'blockers': {'10': [], '11': [10]},
                  'dependency_pins': {'10': [], '11': [{k: self.issues[10][k] for k in ('number', 'id', 'node_id')}]},
                  'blocks_verified': True, 'new_revision': {'spec': self.new_inputs['handoff'],
                      'ticket_assignment': 'durable-notes', 'ticket_inputs': 'revised-inputs'}}
        self.item['requirement_changes'] = {'durable-notes': record}
        self.statuses.update({10: 'Blocked', 11: 'Blocked'})
        self.save(self.item)
        return record

    def produced(self, work):
        if work['stage'] == 'to-spec':
            result = {'milestone.md': '\n'.join('## ' + heading + '\n' +
                      ('1. Persist notes durably.' if heading == 'User Stories' else 'Retain agreed behavior.')
                      for heading in planning.SECTIONS), 'vision.md': 'Future scope.',
                      'provenance.md': 'SIMULATED revision direction: retain durable notes.'}
        else:
            result = [{'number': n, 'title': f'Revised notes {n}', 'desired_behavior': 'Retain agreed durable-note behavior.',
                       'references': [{'spec': work['inputs']['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
                       'acceptance_criteria': ['Reopen with durability acceptance.'], 'blockers': [] if n == 10 else [10]}
                      for n in (10, 11)]
        pins = {work['skill']['path']: work['skill']['sha256'],
                **{d['url']: d['sha256'] for d in work['inputs']['documents'].values()}, **work.get('load_sources', {})}
        return {'result': result, 'execution': {'run_id': 'SIMULATED-' + work['stage'],
            'loads': [{'source': source, 'sha256': pin, 'tool_reference': 'SIMULATED load'} for source, pin in pins.items()],
            'decomposition': 'SIMULATED contract fixture, not native work', 'result_digest': tickets.digest(result)}}

    def execute(self):
        return synthesis.execute(self.database, 'product', 'm1', '42',
            {'change_id': 'durable-notes', 'skills': self.skills}, self.github, str(self.config))

    def prepare_publication(self):
        # Real synthesis controller/validation; only its external helper transport is replaced.
        self.install_record('resolved')
        with patch.object(synthesis, 'invoke', side_effect=lambda config, work, *, cancelled, admission: self.produced(work)):
            self.item = self.execute()
        record = self.item['requirement_changes']['durable-notes']
        record.update(status='publication-pending', new_inputs=self.new_inputs,
                      publication={'commit': 'd' * 40, 'path': 'docs/revision'})
        self.pinned.return_value = self.new_inputs
        self.save(self.item)
        return record

    def publish_updates(self):
        # Isolate affected-issue update receipts from unrelated document publication/completion.
        with patch.object(publication, 'reconcile'), \
             patch.object(publication, 'version_documents', side_effect=lambda db, item, record, gh: record['publication']), \
             patch.object(publication, 'spec_issue', return_value=4), \
             patch.object(planning, 'read_publication', return_value=self.new_inputs['handoff']):
            return publication.publish(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes'}, self.github)

    def prepare_resume(self):
        self.prepare_publication()
        def update(path, payload=None, method=None):
            assert payload is not None
            self.assertEqual(method, 'PATCH')
            self.issues[int(path.split('/')[-1])].update(payload)
        self.github.request.side_effect = update
        self.item = self.publish_updates()
        self.github.request.reset_mock()
        return self.item['requirement_changes']['durable-notes']

    def release(self):
        record = self.read()['requirement_changes']['durable-notes']
        return requirements.resume_change(self.database, 'product', 'm1', '42',
            {'change_id': 'durable-notes', 'direction': self.direction, 'new_revision': record['new_revision']}, self.github)

    def test_lean_revision_preserves_crlf_unrelated_sections_footer_and_lost_update(self):
        record = self.prepare_publication()
        original = lean_body(self.old_inputs).replace('## ', '## \t').replace('\n', '\r\n')
        self.issues[10]['body'] = original
        record['issues']['10'] = requirements.issue_pin(self.issues[10])
        self.save(self.item)
        def lost_update(path, payload=None, method=None):
            assert payload is not None
            self.issues[int(path.split('/')[-1])].update(payload)
            raise RepositoryError('transport_error', 'SIMULATED lost update response')
        self.github.request.side_effect = lost_update
        published = self.publish_updates()
        record = published['requirement_changes']['durable-notes']
        self.assertEqual(record['status'], 'published')
        body = self.issues[10]['body']
        self.assertEqual(tickets.external_contract(self.issues[10], record['new_inputs']), 'contracted')
        self.assertIn('agreed durable-note behavior', tickets.sections(body)['Change'])
        self.assertIn('durability acceptance', tickets.sections(body)['Done'])
        for name in ('Constraint', 'Blocked by'):
            before, after = tickets.section_spans(original), tickets.section_spans(body)
            assert before is not None and after is not None
            self.assertEqual(original[slice(*before[name])], body[slice(*after[name])])
        self.assertNotIn('\n', body.replace('\r\n', ''))
        self.assertTrue(body.endswith(original[original.index('<!--'):]))
        self.assertEqual(self.github.request.call_count, 2)
        self.publish_updates()
        self.assertEqual(self.github.request.call_count, 2, 'Readback recovery must not repeat either issue update')
        resumed = self.release()
        self.assertEqual(resumed['requirement_changes']['durable-notes']['status'], 'resumed')
        self.assertEqual(self.statuses, {10: 'Ready', 11: 'Ready'})

    def test_pause_after_final_record_read_prevents_revision_and_retirement(self):
        assignment = {'assignment_id': 'old-worker', 'revision': {'commit': 'a' * 40},
                      'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10}}
        self.database.execute('INSERT INTO role_assignments VALUES (?, ?)', ('old-worker', json.dumps(assignment)))
        self.database.commit()
        def final_read(github, repo, number):
            if number == 11:
                self.interrupt()
            return [self.issues[10]] if number == 11 else []
        self.dependencies.side_effect = final_read
        request = {'change_id': 'durable-notes', 'kind': 'material', 'direction': self.direction,
                   'requirements': ['US-1'], 'issues': [10],
                   'old_revision': {'10': requirements.authority(self.item, 10)}}
        after = self.refused(lambda: requirements.record_change(self.database, 'product', 'm1', '42', request, self.github))
        self.assertEqual([call.args[2] for call in self.dependencies.call_args_list], [10, 11])
        self.assertNotIn('requirement_changes', after)
        self.assertEqual(json.loads(self.database.execute('SELECT payload FROM role_assignments').fetchone()[0]), assignment)
        self.assertEqual(self.statuses, {10: 'Ready', 11: 'Ready'})
        self.set_field.assert_not_called()

    def test_pause_at_spec_search_prevents_new_revision_issue_intent(self):
        record = self.install_record('publication-pending')
        record['publication'] = {'commit': 'd' * 40}
        self.save(self.item)
        def search(*args):
            self.interrupt()
            return []
        with patch.object(tickets.gh, 'rest_pages', side_effect=search) as searched:
            after = self.refused(lambda: publication.spec_issue(self.database, self.item, record, self.github))
        searched.assert_called_once()
        self.assertEqual(after['requirement_changes']['durable-notes']['publication'], {'commit': 'd' * 40})
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        self.github.request.assert_not_called()

    def test_pause_at_commit_response_retains_effect_without_new_ref_intent(self):
        record = self.install_record('publication-pending')
        branch = 'factory/revision/m1/durable-notes'
        record['publication'] = {'branch': branch, 'path': 'docs/factory/m1/revisions/durable-notes',
                                 'tree': 'f' * 40, 'parent': 'a' * 40}
        self.save(self.item)
        refs = {}
        def git(path, payload=None, method=None):
            if path.endswith('/git/commits'):
                self.interrupt()
                return {'sha': 'd' * 40}
            if '/git/ref/heads/' in path:
                return {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': refs[branch]}} if refs else None
            if path.endswith('/git/refs'):
                assert payload is not None
                refs[branch] = payload['sha']
                return {}
            if path.endswith('/git/commits/' + 'd' * 40):
                return {'sha': 'd' * 40, 'tree': {'sha': 'f' * 40}, 'parents': [{'sha': 'a' * 40}]}
            self.fail('Unexpected Git transport read/write: ' + path)
        self.github.request.side_effect = git
        after = self.refused(lambda: publication.version_documents(self.database, self.item, record, self.github))
        journal = after['requirement_changes']['durable-notes']['publication']
        self.assertEqual(journal['commit'], 'd' * 40)
        self.assertTrue(journal['commit_intent'])
        self.assertNotIn('ref_intent', journal)
        self.assertEqual(refs, {})
        self.resume()
        self.item = self.read()
        publication.version_documents(self.database, self.item, self.item['requirement_changes']['durable-notes'], self.github)
        self.assertEqual(refs, {branch: 'd' * 40})
        self.assertEqual(sum(call.args[0].endswith('/git/commits') for call in self.github.request.call_args_list), 1)

    def test_pause_at_updated_issue_response_retains_pin_without_granting_authority(self):
        self.prepare_publication()
        def update(path, payload=None, method=None):
            assert payload is not None
            self.issues[int(path.split('/')[-1])].update(payload)
            self.interrupt()
        self.github.request.side_effect = update
        after = self.refused(self.publish_updates)
        record = after['requirement_changes']['durable-notes']
        self.assertEqual(record['updated_issues']['10'], requirements.issue_pin(self.issues[10]))
        self.assertNotIn('10', after.get('requirement_authority', {}))
        self.assertNotIn('11', record['updated_issues'])
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        self.resume()
        self.github.request.side_effect = lambda path, payload, method: self.issues[int(path.split('/')[-1])].update(payload)
        published = self.publish_updates()
        self.assertEqual(published['requirement_changes']['durable-notes']['status'], 'published')
        self.assertEqual(sum(call.args[0].endswith('/issues/10') for call in self.github.request.call_args_list), 1)

    def test_pause_during_resolution_final_membership_read_cannot_resolve_hold(self):
        record = self.install_record()
        record.pop('resolution')
        self.save(self.item)
        reads = 0
        def final_member(*args):
            nonlocal reads
            reads += 1
            member = self.member(*args)
            if reads == 4:
                self.interrupt()
            return member
        self.membership.side_effect = final_member
        operation = lambda: requirements.resolve_change(self.database, 'product', 'm1', '42',
            {'change_id': 'durable-notes', 'direction': self.direction, 'old_revision': record['old_revision']}, self.github)
        after = self.refused(operation)
        self.assertEqual(reads, 4)
        self.assertEqual(after['requirement_changes']['durable-notes']['status'], 'held')
        self.assertNotIn('resolution', after['requirement_changes']['durable-notes'])
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        self.resume()
        self.assertEqual(operation()['requirement_changes']['durable-notes']['status'], 'resolved')

    def refuse_synthesis_input(self, malformed=False):
        self.install_record('resolved')
        reads = 0
        def final_input(*args):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.interrupt(malformed=malformed)
            return self.old_inputs
        self.pinned.side_effect = final_input
        with patch.object(synthesis, 'invoke', side_effect=lambda config, work, *, cancelled, admission: self.produced(work)) as invoke:
            after = self.refused(self.execute, 'invalid_control' if malformed else 'iteration_paused')
            self.assertEqual(reads, 2)
            record = after['requirement_changes']['durable-notes']
            self.assertEqual(record['status'], 'resolved')
            self.assertNotIn('attempts', record)
            self.assertNotIn('executions', record)
            invoke.assert_not_called()
            self.resume(malformed=malformed)
            self.pinned.side_effect = None
            self.assertEqual(self.execute()['requirement_changes']['durable-notes']['status'], 'synthesized')
            self.assertEqual([call.args[1]['stage'] for call in invoke.call_args_list], ['to-spec', 'to-tickets'])

    def test_pause_after_synthesis_input_read_prevents_capability_attempt(self):
        self.refuse_synthesis_input()

    def test_pause_at_synthesis_return_retains_untrusted_receipt_not_admitted_result(self):
        self.install_record('resolved')
        def returned(config, work, *, cancelled, admission):
            produced = self.produced(work)
            self.interrupt()
            return produced
        with patch.object(synthesis, 'invoke', side_effect=returned) as invoke:
            after = self.refused(self.execute)
            record = after['requirement_changes']['durable-notes']
            self.assertEqual(record['status'], 'resolved')
            self.assertEqual(set(record['attempts']), {'to-spec'})
            self.assertNotIn('executions', record)
            receipt = record['execution_receipts']['to-spec']
            self.assertIn('SIMULATED revision direction', receipt['result']['provenance.md'])
            self.assertEqual(receipt['execution']['run_id'], 'SIMULATED-to-spec')
            self.resume()
            self.refused(self.execute, 'execution_uncertain')
            invoke.assert_called_once()

    def test_malformed_control_before_revision_blob_write_has_zero_mutations(self):
        record = self.install_record('publication-pending')
        record['documents'] = {'milestone.md': 'Retain notes.'}
        self.save(self.item)
        blobs, refs = {}, {}
        def git(path, payload=None, method=None):
            if path.endswith('/git/commits/' + 'a' * 40):
                if not controls.read(self.database, 'product', 'm1'):
                    self.interrupt(malformed=True)
                return {'sha': 'a' * 40, 'tree': {'sha': 'e' * 40}}
            if path.endswith('/git/blobs'):
                assert payload is not None
                raw = base64.b64decode(payload['content'])
                sha = object_sha('blob', raw)
                blobs[sha] = raw
                return {'sha': sha}
            if path.endswith('/git/trees'):
                return {'sha': 'f' * 40}
            if path.endswith('/git/commits'):
                return {'sha': 'd' * 40}
            if path.endswith('/git/refs'):
                assert payload is not None
                refs[payload['ref']] = payload['sha']
                return {}
            if '/git/ref/heads/' in path:
                ref = 'refs/heads/factory/revision/m1/durable-notes'
                return {'ref': ref, 'object': {'type': 'commit', 'sha': refs[ref]}} if refs else None
            if path.endswith('/git/commits/' + 'd' * 40):
                return {'sha': 'd' * 40, 'tree': {'sha': 'f' * 40}, 'parents': [{'sha': 'a' * 40}]}
            self.fail('Unexpected Git transport read/write: ' + path)
        self.github.request.side_effect = git
        after = self.refused(lambda: publication.version_documents(self.database, self.item, record, self.github), 'invalid_control')
        self.assertNotIn('publication', after['requirement_changes']['durable-notes'])
        self.assertEqual(blobs, {}, 'Late malformed controls must prevent the first blob effect')
        self.assertEqual(refs, {})
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        self.assertEqual(after['requirement_changes']['durable-notes']['status'], 'publication-pending')
        self.github.request.assert_called_once()
        self.set_field.assert_not_called()
        self.resume(malformed=True)
        self.item = self.read()
        journal = publication.version_documents(self.database, self.item, self.item['requirement_changes']['durable-notes'], self.github)
        self.assertEqual(journal['commit'], 'd' * 40)
        self.assertEqual(list(blobs.values()), [b'Retain notes.'])
        self.assertEqual(refs, {'refs/heads/factory/revision/m1/durable-notes': 'd' * 40})

    def test_malformed_control_before_held_status_restoration_preserves_blocks(self):
        self.prepare_resume()
        reads = 0
        def restoration_member(*args):
            nonlocal reads
            reads += 1
            member = self.member(*args)
            if reads == 3:  # Two final publication membership checks, then first restoration read.
                self.interrupt(malformed=True)
            return member
        self.membership.side_effect = restoration_member
        after = self.refused(self.release, 'invalid_control')
        self.assertEqual(after['requirement_changes']['durable-notes']['status'], 'resume-pending')
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        self.assertEqual(self.statuses, {10: 'Blocked', 11: 'Blocked'})
        self.set_field.assert_not_called()
        self.resume(malformed=True)
        self.assertEqual(self.release()['requirement_changes']['durable-notes']['status'], 'resumed')
        self.assertEqual(self.statuses, {10: 'Ready', 11: 'Ready'})

    def test_malformed_control_before_synthesis_attempt_never_invokes_adapter(self):
        self.refuse_synthesis_input(malformed=True)

    def test_stop_after_final_ready_write_cannot_release_requirement_hold(self):
        self.prepare_resume()
        def final_write(*args):
            self.change_status(*args)
            if self.statuses == {10: 'Ready', 11: 'Ready'}:
                self.interrupt('emergency-stop')
        self.set_field.side_effect = final_write
        after = self.refused(self.release)
        self.assertEqual(after['requirement_changes']['durable-notes']['status'], 'resume-pending')
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        self.assertEqual(self.statuses, {10: 'Ready', 11: 'Ready'}, 'Already applied effects stay truthful')
        writes = self.set_field.call_count
        self.resume()
        self.assertEqual(self.release()['requirement_changes']['durable-notes']['status'], 'resumed')
        self.assertEqual(self.set_field.call_count, writes, 'Ready effect reconciliation must not repeat writes')
        # Requirement-only release must not clear independent holds or underlying deferred progress.
        for previous, stuck, expected in [('blocked', False, 'Blocked'), ('deferred', False, 'Deferred'),
                                           ('ready', True, 'Blocked')]:
            with self.subTest(previous=previous, stuck=stuck):
                self.item = self.read()
                record = self.item['requirement_changes']['durable-notes']
                record.update(status='published', previous_statuses={'10': previous, '11': 'ready'})
                self.item['stuck_work'] = {'independent': {'status': 'held', 'affected': [10]}} if stuck else {}
                self.statuses.update({10: 'Blocked', 11: 'Blocked'})
                self.save(self.item)
                self.set_field.side_effect = self.change_status
                resumed = self.release()
                self.assertEqual(resumed['requirement_changes']['durable-notes']['status'], 'resumed')
                self.assertEqual(self.statuses, {10: expected, 11: 'Ready'})
                if stuck:
                    self.assertEqual(resumed['stuck_work']['independent']['status'], 'held')
