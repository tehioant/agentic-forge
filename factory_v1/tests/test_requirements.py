"""Public fresh-process revisions with SIMULATED tracker and host executor, NOT native acceptance."""
import base64
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

from factory_v1 import requirements, tickets
from factory_v1.tests import test_assignments


class RequirementChangeTests(unittest.TestCase):
    def setUp(self):
        if hasattr(self, 'provided_assignment'):
            self.assignment = self.provided_assignment
        else:
            self.assignment = test_assignments.AssignmentTests()
            self.assignment.setUp()
            self.addCleanup(self.assignment.doCleanups)
        self.tracker = self.assignment.tracker
        self.fixture = self.assignment.fixture
        self.root = self.assignment.root
        self.original = self.tracker.inspect()
        self.direction = {'operator_id': '42', 'origin': self.original['origin'], 'reference': 'SIMULATED-operator-message',
                          'content': 'Retain note creation and reopening with an explicit durable-write requirement.'}
        self.change = {'change_id': 'durable-notes', 'kind': 'material', 'direction': self.direction,
                       'requirements': ['US-1'], 'issues': [10],
                       'old_revision': {'10': requirements.authority(self.original, 10)}}
        self.prepared = self.provided_stuck if hasattr(self, 'provided_stuck') else self.assignment.ok(self.assignment.command())
        self.drop_update = False
        self.bad_documents = False
        self.git_commits = {'a' * 40: {'sha': 'a' * 40, 'tree': {'sha': 'e' * 40}}}
        self.git_refs = {}
        self.git_blobs = {}
        self.git_tree = []
        self.publication_reads = []
        parent = self.fixture.server.RequestHandlerClass
        case = self

        class Handler(parent):
            def do_GET(self):
                prefix = '/repos/example/product'
                suffix = self.path.removeprefix(prefix)
                if suffix.startswith('/git/commits/'):
                    commit = case.git_commits.get(suffix.split('/')[-1])
                    if commit:
                        self.reply(200, commit)
                        return
                if suffix.startswith('/git/ref/heads/'):
                    branch = suffix.removeprefix('/git/ref/heads/')
                    self.reply(200 if branch in case.git_refs else 404,
                               {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': case.git_refs.get(branch)}})
                    return
                if '/contents/docs/factory/m1/revisions/' in self.path:
                    case.publication_reads.append(self.path)
                    path = suffix.split('?')[0].removeprefix('/contents/')
                    entry = next((e for e in case.git_tree if e['path'] == path), None)
                    if entry:
                        content = case.git_blobs[entry['sha']]
                        if case.bad_documents:
                            content = b'SIMULATED publication drift'
                        self.reply(200, {'path': path, 'type': 'file', 'encoding': 'base64', 'sha': entry['sha'],
                                         'content': base64.b64encode(content).decode()})
                        return
                super().do_GET()

            def do_POST(self):
                if '/git/' not in self.path:
                    super().do_POST()
                    return
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.fixture.calls.append(('POST', self.path, data))
                if self.path.endswith('/blobs'):
                    raw = base64.b64decode(data['content'])
                    sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                    case.git_blobs[sha] = raw
                    self.reply(201, {'sha': sha})
                elif self.path.endswith('/trees'):
                    case.git_tree = data['tree']
                    self.reply(201, {'sha': 'f' * 40})
                elif self.path.endswith('/commits'):
                    commit = {'sha': 'd' * 40, 'tree': {'sha': data['tree']},
                              'parents': [{'sha': p} for p in data['parents']], 'message': data['message']}
                    case.git_commits[commit['sha']] = commit
                    self.reply(201, commit)
                elif self.path.endswith('/refs'):
                    case.git_refs[data['ref'].removeprefix('refs/heads/')] = data['sha']
                    self.reply(201, {})
                else:
                    self.reply(403, {})

            def do_PATCH(self):
                number = int(self.path.split('/')[-1])
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.fixture.calls.append(('PATCH', self.path, data))
                case.tracker.issues[number].update(data)
                if case.drop_update:
                    case.drop_update = False
                    self.close_connection = True
                    return
                self.reply(200, case.tracker.issues[number])

        self.fixture.server.RequestHandlerClass = Handler
        self.helper = self.root / 'SIMULATED-synthesis-executor'
        self.helper.write_text('#!' + sys.executable + '\n' + '''
# Deterministic contract fixture. NO model calls or genuine skill work.
import hashlib, json, sys
from pathlib import Path
payload = json.load(sys.stdin)
def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
assert hashlib.sha256(Path(payload['skill']['path']).read_bytes()).hexdigest() == payload['skill']['sha256']
if payload['stage'] == 'to-spec':
    result = {name: doc['content'] for name, doc in payload['inputs']['documents'].items()}
    result['milestone.md'] = result['milestone.md'].replace('Persist notes locally', 'Persist notes durably with explicit durable-write semantics locally')
    result['provenance.md'] += '\\nSIMULATED revision direction: ' + payload['direction']['content'] + '\\n'
else:
    result = [{'number': int(number), 'title': issue['title'],
               'desired_behavior': 'SIMULATED agreed durable-note behavior for issue ' + number,
               'references': [{'spec': payload['inputs']['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
               'acceptance_criteria': ['SIMULATED durability acceptance with exact readback.'],
               'blockers': payload['blockers'][number]} for number, issue in payload['affected'].items()]
expected = {payload['skill']['path']: payload['skill']['sha256']}
expected.update({d['url']: d['sha256'] for d in payload['inputs']['documents'].values()})
expected.update(payload['load_sources'])
loads = [{'source': source, 'sha256': pin, 'tool_reference': 'SIMULATED fixture load; NOT native work'} for source, pin in expected.items()]
json.dump({'result': result, 'execution': {'run_id': 'SIMULATED-' + payload['stage'], 'loads': loads,
          'decomposition': 'SIMULATED contract result, NOT native installed skill acceptance', 'result_digest': digest(result)}}, sys.stdout)
''')
        self.helper.chmod(0o700)
        self.config = self.root / 'synthesis-capability.json'
        self.config.write_text(json.dumps({'operator_id': '42', 'reference': 'SIMULATED capability approval',
            'execution_reference': 'SIMULATED independently verified adapter; NOT live execution',
            'argv': [str(self.helper)], 'sha256': hashlib.sha256(self.helper.read_bytes()).hexdigest(), 'timeout': 10}))
        self.config.chmod(0o600)

    def control(self, command, request, config=None):
        file = self.root / ('revision-' + command + '.json')
        file.write_text(json.dumps(request))
        args = [command, '--project', 'product', '--iteration', 'm1', '--request', str(file), '--api-base', self.fixture.base]
        if config:
            args += ['--synthesis-config', str(config)]
        return self.fixture.cli(*args)

    def ok(self, result):
        return self.assignment.ok(result)

    def record(self):
        return self.ok(self.control('record-requirement-change', self.change))

    def resolve(self):
        item = self.record()
        record = item['requirement_changes']['durable-notes']
        return self.ok(self.control('resolve-requirement-change', {'change_id': 'durable-notes',
            'direction': self.direction, 'old_revision': record['old_revision']}))

    def synthesize(self):
        self.resolve()
        return self.ok(self.control('resynthesize-requirements', {'change_id': 'durable-notes',
            'skills': self.tracker.planning.skills}, self.config))

    def publish(self):
        self.synthesize()
        return self.ok(self.control('publish-requirement-change', {'change_id': 'durable-notes'}))

    def resume(self, item):
        return self.control('resume-requirement-change', {'change_id': 'durable-notes', 'direction': self.direction,
            'new_revision': item['requirement_changes']['durable-notes']['new_revision']})

    def test_material_hold_invalidates_old_assignment_and_dependents_but_releases_independent_claim(self):
        held = self.record()
        record = held['requirement_changes']['durable-notes']
        self.assertEqual(record['affected'], [10, 11])
        self.assertEqual(record['retired_assignments'], [self.prepared['assignment_id']])
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')
        self.assertEqual(self.tracker.members[11]['fields']['STATUS'], 'Blocked')
        frontier = self.ok(self.tracker.control('frontier'))['ticket_work']['frontier']
        self.assertEqual(frontier['eligible'], [12])
        self.assignment.refused(self.assignment.inspect(self.prepared), 'requirements_held')
        self.assignment.refused(self.assignment.command('assignment-result', self.assignment.result(self.prepared), self.prepared['assignment_id']), 'requirements_held')
        self.assertEqual(self.tracker.control('reserve-ticket', issue=10).returncode, 2)
        self.ok(self.tracker.control('reserve-ticket', issue=12))
        new = self.assignment.configuration(12)
        self.ok(self.assignment.command(request=new))
        self.assertEqual(self.record()['requirement_changes']['durable-notes']['status'], 'held')

    def test_selected_execution_versions_readbacks_and_explicit_resume_feed_fresh_inputs(self):
        independent_body = self.tracker.issues[12]['body']
        independent_issue = copy.deepcopy(self.tracker.issues[12])
        item = self.publish()
        record = item['requirement_changes']['durable-notes']
        self.assertEqual(record['status'], 'published')
        self.assertEqual(set(record['executions']), {'to-spec', 'to-tickets'})
        self.assertEqual(self.tracker.issues[12], independent_issue)
        self.assertTrue(self.publication_reads)
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')
        self.assertEqual(self.assignment.command().returncode, 2)
        resumed = self.ok(self.resume(item))
        self.assertEqual(resumed['requirement_changes']['durable-notes']['status'], 'resumed')
        self.assertEqual(resumed['ticket_work']['frontier']['eligible'], [10, 12])
        self.assertEqual(self.tracker.issues[12]['body'], independent_body)
        request = self.assignment.configuration()
        request['spec_commit'] = record['new_revision']['spec']['commit']
        request['claim_id'] = 'fresh-revised-implementation'
        fresh = self.ok(self.assignment.command(request=request))
        self.assertEqual(fresh['revision'], record['new_revision'])
        self.assertIn('durable-write', fresh['handoff']['specification']['documents']['milestone.md']['content'])
        self.assertEqual(fresh['handoff']['requirement_change']['resolution'], self.direction)
        self.assertIn('handoff:requirement-change', fresh['handoff']['input_loads'])
        from factory_v1.sandbox import inputs_for
        context = self.root / 'fresh-context'
        context.mkdir()
        loaded = inputs_for(fresh, context)
        self.assertIn('handoff:requirement-change', {v['source'] for v in loaded})
        self.assignment.refused(self.assignment.inspect(self.prepared), 'requirements_held')
        self.assertFalse(any(c[0] == 'PATCH' and '/git/refs/heads/main' in c[1] for c in self.fixture.calls))

    def test_minor_trace_revalidates_unchanged_evidence_without_status_mutations(self):
        self.change.update(kind='minor', direction={**self.direction, 'content': 'Clarification: the original note behavior is unchanged.'})
        self.fixture.calls.clear()
        item = self.record()
        record = item['requirement_changes']['durable-notes']
        self.assertEqual(record['status'], 'clarified')
        self.assertEqual(record['old_revision'], record['new_revision'])
        self.assertTrue(record['preserved_evidence'])
        self.assignment.ok(self.assignment.inspect(self.prepared))
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Ready')
        writes = [c for c in self.fixture.calls if c[0] != 'GET' and not (c[1] == '/graphql' and c[2]['query'].startswith('query '))]
        self.assertEqual(writes, [])
        self.tracker.issues[10]['body'] += '\nChanged acceptance is not just clarification.'
        self.change['change_id'] = 'another-clarification'
        self.assignment.refused(self.control('record-requirement-change', self.change), 'stale_requirements')

    def test_unresolved_manual_artifacts_and_wrong_operator_cannot_synthesize_or_resume(self):
        item = self.record()
        self.assignment.refused(self.control('resynthesize-requirements', {'change_id': 'durable-notes',
            'skills': self.tracker.planning.skills}, self.config), 'direction_required')
        self.assignment.refused(self.resume(item), 'publication_required')
        self.resolve()
        self.assignment.refused(self.control('resynthesize-requirements', {'change_id': 'durable-notes',
            'skills': self.tracker.planning.skills, 'documents': {}, 'tickets': []}, self.config), 'direction_required')
        wrong = {'change_id': 'durable-notes', 'direction': {**self.direction, 'operator_id': 'worker'},
                 'old_revision': item['requirement_changes']['durable-notes']['old_revision']}
        self.assignment.refused(self.control('resolve-requirement-change', wrong), 'approval_required')

    def test_lost_updated_ticket_response_reconciles_exact_effect_without_repeated_patch(self):
        self.synthesize()
        self.drop_update = True
        item = self.ok(self.control('publish-requirement-change', {'change_id': 'durable-notes'}))
        patches = [c for c in self.fixture.calls if c[:2] == ('PATCH', '/repos/example/product/issues/10')]
        self.assertEqual(len(patches), 1)
        self.ok(self.control('publish-requirement-change', {'change_id': 'durable-notes'}))
        self.assertEqual([c for c in self.fixture.calls if c[:2] == ('PATCH', '/repos/example/product/issues/10')], patches)
        self.ok(self.resume(item))

    def test_missing_changed_skill_and_executor_refuse_without_publication_or_fallback(self):
        self.resolve()
        self.helper.write_text(self.helper.read_text() + '\n# unexpected byte drift\n')
        self.assignment.refused(self.control('resynthesize-requirements', {'change_id': 'durable-notes',
            'skills': self.tracker.planning.skills}, self.config), 'execution_required')
        self.assertFalse(self.git_refs)
        self.assertEqual(self.tracker.inspect()['requirement_changes']['durable-notes']['status'], 'resolved')

    def test_missing_execution_receipts_persist_uncertain_attempt_without_automatic_retry(self):
        self.resolve()
        invocation = self.root / 'SIMULATED-invoked'
        self.helper.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nimport json\n' +
            'Path(' + repr(str(invocation)) + ').write_text("one fixture invocation")\n' +
            'print(json.dumps({"result": {}, "execution": {}}))\n')
        config = json.loads(self.config.read_text())
        config['sha256'] = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        self.config.write_text(json.dumps(config))
        request = {'change_id': 'durable-notes', 'skills': self.tracker.planning.skills}
        self.assignment.refused(self.control('resynthesize-requirements', request, self.config), 'execution_evidence_required')
        self.assertTrue(invocation.exists())
        invocation.unlink()
        self.assignment.refused(self.control('resynthesize-requirements', request, self.config), 'execution_uncertain')
        self.assertFalse(invocation.exists())
        self.assertFalse(self.git_refs)

    def test_published_revision_cannot_resume_an_unconfirmed_old_worker(self):
        item = self.publish()
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            old = json.loads(database.execute('SELECT payload FROM role_assignments WHERE assignment_id=?',
                (self.prepared['assignment_id'],)).fetchone()[0])
            old['runtime'] = {'status': 'unconfirmed', 'container_removed': False}
            database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                (json.dumps(old), old['assignment_id']))
        self.assignment.refused(self.resume(item), 'worker_active')
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')

    def test_partial_status_restoration_keeps_local_hold_until_explicit_resume_replay(self):
        item = self.publish()
        original = self.fixture.server.RequestHandlerClass
        counter = {'fields': 0}
        class Handler(original):
            def do_POST(inner):
                if inner.path == '/graphql':
                    import io
                    size = int(inner.headers['Content-Length'])
                    raw = inner.rfile.read(size)
                    data = json.loads(raw)
                    inner.rfile = io.BytesIO(raw)
                    if data['query'].startswith('mutation TicketField') and data['variables']['option'] == 'Ready':
                        counter['fields'] += 1
                        if counter['fields'] == 2:
                            inner.reply(403, {})
                            return
                super().do_POST()
        self.fixture.server.RequestHandlerClass = Handler
        self.assertEqual(self.resume(item).returncode, 2)
        pending = self.tracker.inspect()
        self.assertEqual(pending['requirement_changes']['durable-notes']['status'], 'resume-pending')
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Ready')
        self.assignment.refused(self.assignment.command(), 'requirements_held')
        self.assertEqual(self.ok(self.tracker.control('frontier'))['ticket_work']['frontier']['eligible'], [12])
        self.fixture.server.RequestHandlerClass = original
        self.assertEqual(self.ok(self.resume(item))['requirement_changes']['durable-notes']['status'], 'resumed')

    def drift_after_status_write(self, option, fault):
        import io
        parent = self.fixture.server.RequestHandlerClass
        case = self
        fired = []

        class Handler(parent):
            def do_POST(inner):
                raw = inner.rfile.read(int(inner.headers['Content-Length']))
                data = json.loads(raw)
                inner.rfile = io.BytesIO(raw)
                super().do_POST()
                if (inner.path == '/graphql' and data['query'].startswith('mutation TicketField') and
                        data['variables']['option'] == option and data['variables']['item'] == 'ITEM11'):
                    fired.append(True)
                    fault()

        self.fixture.server.RequestHandlerClass = Handler
        return fired

    def assert_post_restoration_drift_held(self, fault):
        item = self.publish()
        fired = self.drift_after_status_write('Ready', fault)
        response = self.resume(item)
        self.assertTrue(fired)
        self.assertEqual(response.returncode, 2, response.stdout + response.stderr)
        stored = self.tracker.inspect()
        self.assertEqual(stored['requirement_changes']['durable-notes']['status'], 'resume-pending')
        self.assertEqual(requirements.held_numbers(stored), {10, 11})
        request = self.assignment.configuration(11)
        request.update(spec_commit=item['requirement_changes']['durable-notes']['new_revision']['spec']['commit'],
                       claim_id='post-restoration-drift')
        self.assignment.refused(self.assignment.command(request=request), 'requirements_held')
        self.assertEqual(self.resume(item).returncode, 2)
        self.assertEqual(self.tracker.inspect()['requirement_changes']['durable-notes']['status'], 'resume-pending')

    def test_final_ready_write_membership_drift_retains_resume_pending_hold(self):
        self.assert_post_restoration_drift_held(lambda: self.tracker.members[11].update(id='REPLACED-ITEM11'))

    def test_final_ready_write_native_and_textual_drift_retains_resume_pending_hold(self):
        def drift():
            self.tracker.deps[11] = []
            self.tracker.issues[11]['body'] = self.tracker.issues[11]['body'].replace('- #10', 'None (can start immediately).')
        self.assert_post_restoration_drift_held(drift)

    def test_final_blocked_write_publication_drift_cannot_become_published(self):
        self.synthesize()
        self.tracker.members[11]['fields']['STATUS'] = 'Ready'
        fired = self.drift_after_status_write('Blocked', lambda: self.tracker.issues[10].update(title='Unexpected scope'))
        response = self.control('publish-requirement-change', {'change_id': 'durable-notes'})
        self.assertTrue(fired)
        self.assertEqual(response.returncode, 2, response.stdout + response.stderr)
        stored = self.tracker.inspect()
        self.assertEqual(stored['requirement_changes']['durable-notes']['status'], 'publication-pending')
        self.assertEqual(requirements.held_numbers(stored), {10, 11})

    def test_publication_drift_prevents_resume_and_retains_hold_across_restart(self):
        item = self.publish()
        self.bad_documents = True
        self.assignment.refused(self.resume(item), 'publication_mismatch')
        self.assertEqual(self.tracker.inspect()['requirement_changes']['durable-notes']['status'], 'published')
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Blocked')
        self.assignment.refused(self.assignment.command(), 'requirements_held')


class StuckRequirementRevisionTests(unittest.TestCase):
    def test_correlated_revise_direction_resynthesizes_and_releases_only_linked_hold(self):
        from factory_v1.tests import test_diagnosis
        case = test_diagnosis.DiagnosisLifecycleTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        stuck = case.stuck()
        case.assignment.ok(case.command('declare-stuck', case.identity))
        prepared = case.assignment.ok(case.command('prepare-diagnosis', case.debug_request()))
        case.case.mode('')
        axis = case.assignment.ok(case.case.launch(prepared))
        self.assertEqual(axis['runtime']['status'], 'complete', axis)
        event = case.assignment.ok(case.command('request-direction', case.identity, case.authorization(axis)))['attention']
        case.assignment.ok(case.respond(event, 'revise'))
        decision = case.assignment.ok(case.command('apply-direction', {**case.identity, 'event_id': event['event']['event_id']}))
        self.assertEqual(decision['status'], 'revision-required')
        revision = RequirementChangeTests()
        revision.provided_assignment = case.assignment
        revision.provided_stuck = stuck
        revision.setUp()
        revision.direction = {'operator_id': '42', 'origin': decision['applied_response']['origin'],
            'reference': decision['applied_response']['reference'], 'content': decision['direction']['direction']}
        revision.change.update(direction=revision.direction, stuck_assignment=stuck['assignment_id'])
        published = revision.publish()
        self.assertEqual(published['stuck_work'][stuck['assignment_id']]['status'], 'revision-required')
        resumed = revision.ok(revision.resume(published))
        self.assertEqual(resumed['stuck_work'][stuck['assignment_id']]['status'], 'released')
        self.assertEqual(resumed['requirement_changes']['durable-notes']['status'], 'resumed')
        self.assertEqual(resumed['ticket_work']['frontier']['eligible'], [10, 12])
