"""Direct bug validators and durable body/edge/publication components, without workers."""
import copy
import json
import unittest
from unittest.mock import patch

from factory_v1 import assignments, bugs, repositories, requirements, restart, tickets
from factory_v1.tests import test_tickets


class BugTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self)
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github
        self.case = self.fixture
        self.work_record = self.make_work()
        self.item['bug_work'] = {'accent': self.work_record}
        self.fixture.persist()

    def discovery(self, scope='current', discovery_id='discovered-19', symptoms='Reopened notes lose accents'):
        return {'discovery_id': discovery_id, 'skill': self.fixture.start_request['skill'], 'discovery': {
            'symptoms': symptoms, 'reproduction': ['Create café.', 'Reopen the note.'], 'evidence': ['fixture-output'],
            'expected': 'Reopening retains café.', 'actual': 'Reopening displays caf.', 'environment': 'Offline fixture.',
            'scope': scope, 'scope_reason': 'Observed requirement implication.', 'affected': [10] if scope == 'current' else []}}

    def make_work(self, scope='current'):
        discovery = self.discovery(scope)['discovery']
        candidates = [{'issue': copy.deepcopy(i), 'scope': 'M1', 'membership': self.fixture.membership(None, None, None, i),
                       'dependencies': [{k: self.fixture.issues[n][k] for k in ('number', 'id', 'node_id')} for n in self.fixture.deps[i['number']]]}
                      for i in self.fixture.issues.values()]
        work = {'assignment_id': 'accent-fixture', 'status': 'synthesis_pending', 'discovery': discovery,
                'skill': self.fixture.work['skill'], 'request': self.discovery(scope), 'inputs': self.fixture.inputs,
                'board': self.fixture.board, 'candidates': candidates, 'input_digest': 'a' * 64,
                'adaptation': bugs.ADAPTATION, 'load_sources': {'bug-adaptation:accent-fixture': tickets.digest(bugs.ADAPTATION)},
                'scoped_inputs': {str(n): self.fixture.inputs for n in discovery['affected']}}
        work['bug'] = self.completion_for(work)['bug']; work['execution'] = self.fixture.evidence(work['bug'], work)
        return work

    def completion_for(self, work, status=None):
        scope = work['discovery']['scope']
        result = {'title': 'Retain accents when reopening', 'desired_behavior': 'Readers reopen the exact French note.',
                  'references': [{'spec': work['inputs']['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
                  'acceptance_criteria': ['Reopen café unchanged.'], 'blockers': [], 'status': status or ('deferred' if scope == 'deferred' else 'ready'),
                  'scope': scope, 'affected': work['discovery']['affected'], 'triage_label': 'agent-triage',
                  'comparisons': [{'number': o['issue']['number'], 'symptoms_match': False, 'scope_match': False,
                                  'rationale': 'Feature ticket, not the observed defect.'} for o in work['candidates']]}
        return {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'], 'adaptation': bugs.ADAPTATION,
                'bug': result, 'execution': self.fixture.evidence(result, work)}

    def completion(self, discovery=None, status=None):
        return self.completion_for(self.work_record, status)

    def work(self, item, assignment=None):
        return next(w for w in item['bug_work'].values() if assignment is None or w['assignment_id'] == assignment)

    def signed(self, request):
        request['execution']['result_digest'] = tickets.digest(request['bug'])
        return request

    def complete(self, request):
        return bugs.complete(self.database, self.fixture.stored(), request, self.github)

    def retry(self, assignment):
        return bugs.publish(self.database, self.fixture.stored(), {'assignment_id': assignment}, self.github)

    def test_bug_discovery_and_result_contract_1(self):
        good = self.discovery()['discovery']
        normalized = {**good, 'symptoms': ' REOPENED  notes lose accents\n', 'affected': [12]}
        cases = [(changes, False) for changes in
                 ({'affected': []}, {'affected': [True]}, {'affected': [10, 10]}, {'scope': 'future'},
                  {'symptoms': ''}, {'expected': ''}, {'actual': ''}, {'scope_reason': ''},
                  {'reproduction': [], 'evidence': []}, {'environment': ''}, {'scope': 'deferred'}, {'scope': 'recovery'})]
        cases += [({}, True), ({'environment': None, 'reproduction': []}, True), (normalized, True)]
        cases += [(self.discovery(scope)['discovery'], True) for scope in ('deferred', 'recovery')]
        for changes, valid in cases:
            with self.subTest(changes=changes, valid=valid):
                observation = {**good, **changes}
                if valid:
                    bugs.validate_discovery(observation)
                else:
                    with self.assertRaises(repositories.RepositoryError) as caught:
                        bugs.validate_discovery(observation)
                    self.assertEqual(caught.exception.code, 'invalid_bug')
        self.assertEqual(bugs.fingerprint(good, self.item), bugs.fingerprint(normalized, self.item))
        self.assertNotEqual(bugs.fingerprint(good, self.item), bugs.fingerprint({**normalized, 'scope': 'deferred'}, self.item))

    def test_bug_discovery_and_result_contract_2(self):
        work = self.work_record
        good = work['bug']
        cases = [(changes, None, True) for changes in
                 ({'title': ''}, {'acceptance_criteria': []}, {'affected': [12]}, {'blockers': [10]},
                  {'blockers': [11]}, {'blockers': [99]}, {'scope': 'deferred'}, {'status': 'done'},
                  {'triage_label': 'unconfigured'}, {'comparisons': []},
                  {'references': [{'spec': self.fixture.inputs['documents']['vision.md']['url'], 'requirement': 'Team sharing'}]})]
        for field in ('symptoms_match', 'scope_match'):
            comparisons = copy.deepcopy(good['comparisons']); comparisons[0][field] = 'yes'
            cases.append(({'comparisons': comparisons}, None, True))
        cases.insert(0, ({}, None, False))
        comparisons = copy.deepcopy(good['comparisons']); comparisons[0]['symptoms_match'] = True
        cases.append(({'comparisons': copy.deepcopy(comparisons)}, None, False))
        comparisons[0]['scope_match'] = True
        cases.append(({'comparisons': copy.deepcopy(comparisons)}, 10, False))
        comparisons[1].update(symptoms_match=True, scope_match=True)
        cases.append(({'comparisons': comparisons}, 'publication_uncertain', True))
        for changes, expected, refused in cases:
            with self.subTest(changes=changes):
                if refused:
                    with self.assertRaises(repositories.RepositoryError) as caught:
                        bugs.validate_result(work, {**good, **changes}, self.item)
                    if expected:
                        self.assertEqual(caught.exception.code, expected)
                else:
                    self.assertEqual(bugs.validate_result(work, {**good, **changes}, self.item), expected)
        work['candidates'][0]['issue']['body'] += '\n## Bug scope\ncurrent\n## Observable symptoms\n REOPENED notes lose accents \n## Expected behavior\nReopening retains café.\n## Actual behavior\nReopening displays caf.\n'
        self.assertEqual(bugs.validate_result(work, good, self.item), 10)



    def test_synthesis_replay_new_ids_and_added_scope_refusal_are_durable(self):
        self.item.pop('bug_work'); self.fixture.persist()
        def context(_d, _i, _g, affected, check_board=True):
            return self.fixture.inputs, self.fixture.board, {str(n): self.fixture.inputs for n in affected}
        with patch.object(bugs, 'context', side_effect=context), patch.object(bugs, 'observations', return_value=self.work_record['candidates']):
            result = bugs.synthesize(self.database, self.item, self.discovery(), self.github); old = self.work(result)
            self.assertEqual(old['skill']['dependencies'], []); self.assertIn('bug-adaptation:' + old['assignment_id'], old['load_sources'])
            replay = bugs.synthesize(self.database, result, self.discovery(discovery_id='new-id'), self.github)
            self.assertEqual(self.work(replay), old)
            for changes in ({'affected': [10, 12]}, {'evidence': ['changed']}):
                bad = self.discovery(discovery_id='expanded' if 'affected' in changes else 'discovered-19'); bad['discovery'].update(changes)
                with self.assertRaises(repositories.RepositoryError) as caught: bugs.synthesize(self.database, replay, bad, self.github)
                self.assertEqual(caught.exception.code, 'bug_conflict'); self.database.rollback(); self.assertEqual(self.fixture.stored(), replay)

    def test_completion_revalidates_adaptation_load_before_publication(self):
        request = self.completion(); before = self.fixture.stored()
        for defect in ('digest', 'adaptation', 'execution', 'load'):
            bad = copy.deepcopy(request)
            if defect == 'digest': bad['input_digest'] = '0' * 64
            if defect == 'adaptation': bad['adaptation'] = 'other'
            if defect == 'execution': bad['execution']['loads'] = []
            if defect == 'load': bad['execution']['loads'][-1]['sha256'] = '0' * 64
            with self.subTest(defect=defect), patch.object(bugs, 'work_context', return_value=(self.fixture.inputs, self.fixture.board)), patch.object(bugs, 'publish_locked') as publish:
                with self.assertRaises(repositories.RepositoryError): self.complete(bad)
                self.database.rollback(); self.assertEqual(self.fixture.stored(), before); publish.assert_not_called()

    def test_render_retains_contract_evidence_environment_and_priority(self):
        for scope in ('current', 'deferred', 'recovery'):
            work = self.make_work(scope); work['discovery']['environment'] = None
            rendered = bugs.render(self.item, work); parts = tickets.sections(rendered)
            self.assertEqual(parts['Bug scope'], scope); self.assertEqual(parts['Environment'], 'Unavailable.')
            self.assertEqual(tickets.external_contract({'title': work['bug']['title'], 'body': rendered}, work['inputs']), work['bug']['status'])
            self.assertEqual(parts['Priority'], {'current': 'required iteration work', 'deferred': 'next grilling only', 'recovery': 'immediate recovery'}[scope])

    def test_bug_affected_body_effect(self):
        original = self.fixture.issues[10]['body']
        bodies = [original.replace('## Blocked by\n', heading) for heading in ('## Blocked by \t\n', '##  Blocked by\n')]
        bodies += [original.replace('\nNone (can start immediately).\n\n', '\n \t\n  None (can start immediately). \t\n\n'), original.replace('\n', '\r\n')]
        rows = [('acknowledged', body) for body in bodies] + [('lost', original), ('stale', original)]
        for outcome, body in rows:
            with self.subTest(outcome=outcome, body=body):
                self.fixture.issues[10]['body'] = body
                self.fixture.deps[10] = []
                self.github.request.reset_mock(); self.github.request.side_effect = self.fixture.request
                self.fixture.add_dependency.reset_mock()
                work = self.make_work(); self.item['bug_work'] = {'accent': work}
                start, end = tickets.section_spans(body)['Blocked by']
                newline = '\r\n' if body[start - 2:start] == '\r\n' else '\n'
                expected = body[:start] + newline + '- #13' + newline * 2 + body[end:]
                issue = self.fixture.issue(13, 'fixture bug'); self.fixture.issues[13] = issue
                if outcome == 'stale':
                    work['affected_intents'] = {'10': {'before': body, 'body': body}}
                self.fixture.persist(); before = self.fixture.stored()
                before_other = copy.deepcopy((self.fixture.issues[11], self.fixture.issues[12]))
                if outcome == 'lost':
                    def patch_body(path, payload, method=None):
                        self.assertIn('10', self.fixture.stored()['bug_work']['accent']['affected_intents'])
                        self.fixture.request(path, payload, method)
                        raise repositories.RepositoryError('github_unavailable', 'lost acknowledgement')
                    self.github.request.side_effect = patch_body
                if outcome in ('stale', 'lost'):
                    with self.assertRaises(repositories.RepositoryError) as caught:
                        bugs.affected_edges(self.database, self.item, work, self.github, issue)
                    if outcome == 'stale':
                        self.assertEqual(caught.exception.code, 'publication_mismatch')
                        self.github.request.assert_not_called(); self.fixture.add_dependency.assert_not_called()
                        self.assertEqual(self.fixture.stored(), before)
                        self.fixture.issues.pop(13)
                        continue
                    self.github.request.reset_mock(); self.github.request.side_effect = self.fixture.request
                bugs.affected_edges(self.database, self.item, work, self.github, issue)
                if outcome == 'lost': self.github.request.assert_not_called()
                self.assertEqual(self.fixture.issues[10]['body'], expected)
                self.assertEqual(self.fixture.deps[10], [13])
                self.assertEqual((self.fixture.issues[11], self.fixture.issues[12]), before_other)
                self.github.request.reset_mock(); self.fixture.add_dependency.reset_mock()
                bugs.affected_edges(self.database, self.item, work, self.github, issue)
                self.github.request.assert_not_called(); self.fixture.add_dependency.assert_not_called()
                self.assertEqual(self.fixture.stored()['bug_work']['accent']['affected_intents']['10']['body'], expected)
                self.fixture.issues.pop(13)



    def test_bug_rescope_parks_only_clean_correlated_ownership(self):
        original = copy.deepcopy(self.fixture.issues[10])
        base_item = copy.deepcopy(self.item)
        for fault in ('none', 'running', 'unknown-cleanup', 'unconfirmed', 'pin', 'handoff', 'pause'):
            with self.subTest(fault=fault):
                self.item = self.fixture.item = copy.deepcopy(base_item)
                self.fixture.issues[10] = copy.deepcopy(original); self.fixture.deps[10] = []
                self.database.execute('DELETE FROM role_assignments')
                work = self.item['bug_work']['accent']
                issue = self.fixture.issue(13, 'fixture bug'); self.fixture.issues[13] = issue
                work['identity'] = {k: issue[k] for k in ('number', 'id', 'node_id')}
                scope = {'project': 'product', 'iteration': 'm1', 'repository': 'example/product',
                         'repository_id': 123, 'issue_number': 10, 'issue_id': original['id']}
                handoff = {'stage': 'implementation', 'repository': 'example/product', 'repository_id': 123,
                           'issue': requirements.issue_pin(original), 'profile': {'name': 'old-profile', 'home': '/old-profile'}}
                old = {'assignment_id': 'old-implementation', 'claim_id': 'old-claim', 'ticket_scope': scope,
                       'handoff': handoff, 'handoff_digest': assignments.digest(handoff),
                       'revision': requirements.authority(self.item, 10), 'runtime': {'status': 'complete', 'container_removed': True, 'controller_pid': 999999999,
                           'controller_start': 'fixture-absent', 'container': 'fixture-old', 'run_id': 'fixture-run', 'artifacts': str(self.fixture.root)}}
                if fault == 'running': old['runtime']['status'] = 'running'
                if fault == 'unknown-cleanup': old['runtime']['container_removed'] = None
                if fault == 'unconfirmed': old['runtime']['error'] = 'stop_unconfirmed'
                if fault == 'pin':
                    handoff['issue']['body_sha256'] = 'f' * 64; old['handoff_digest'] = assignments.digest(handoff)
                if fault == 'handoff': old['handoff_digest'] = 'e' * 64
                self.item['reservation'] = {'issue_number': 10, 'status': 'reserved', 'assignment_id': 'ticket-fixture', 'input_digest': self.fixture.work['input_digest']}
                self.database.execute('INSERT INTO role_assignments VALUES (?,?)', (old['assignment_id'], json.dumps(old)))
                self.fixture.persist(); self.github.request.reset_mock(); self.fixture.add_dependency.reset_mock()
                if fault == 'pause': self.fixture.pause()
                if fault != 'none':
                    before = self.fixture.stored()
                    with self.assertRaises(repositories.RepositoryError): bugs.affected_edges(self.database, self.item, work, self.github, issue)
                    self.database.rollback(); self.assertEqual(self.fixture.stored(), before)
                    self.assertNotIn('bug_disposition', assignments.rows(self.database)[0])
                    self.github.request.assert_not_called(); self.fixture.add_dependency.assert_not_called()
                else:
                    bugs.affected_edges(self.database, self.item, work, self.github, issue)
                    parked = assignments.rows(self.database)[0]
                    self.assertTrue(bugs.released_claim(self.database, parked))
                    self.assertEqual(parked['handoff'], old['handoff']); self.assertEqual(parked['runtime'], old['runtime'])
                    self.assertNotIn('reservation', self.fixture.stored())
                    self.assertEqual(assignments.ticket_claims(self.database, {**scope, 'issue_number': 13, 'issue_id': issue['id']}), [])
                    self.assertEqual(restart.ownership(self.database, self.fixture.stored())['assignments'], [])
                    with self.assertRaises(repositories.RepositoryError): assignments.inspect(self.database, 'product', 'm1', '42', parked['assignment_id'])
                    with self.assertRaises(repositories.RepositoryError): assignments.require_result_active(parked)
                    for key, value in (('container_removed', False), ('status', 'running')):
                        bad = copy.deepcopy(parked); bad['runtime'][key] = value
                        self.assertFalse(bugs.released_claim(self.database, bad))
                    work.update(status='published', publication_complete=True)
                    tickets.checkpoint(self.database, self.item)
                    with patch.object(restart, 'worker', return_value={'container_absent': True}), patch.object(restart, 'workspace', return_value={}), \
                            patch.object(restart, 'tracker', return_value=self.fixture.work['frontier']):
                        report = restart.reconcile(self.database, 'product', 'm1', '42', self.github)
                    self.assertEqual(report['status'], 'ready', report)
                    self.assertIn('old-implementation:bug-rescope', report['observations'])
                    fresh_request = {'stage': 'implementation', 'claim_id': 'fresh-claim',
                                     'profile': {'name': 'fresh-profile', 'home': '/fresh-profile'}}
                    assignments.claims(self.database, fresh_request, scope, 'fresh-assignment')
                    with self.assertRaises(repositories.RepositoryError):
                        assignments.claims(self.database, {**fresh_request, 'claim_id': 'old-claim'}, scope, 'fresh-assignment')
                    with self.assertRaises(repositories.RepositoryError):
                        assignments.claims(self.database, {**fresh_request, 'profile': handoff['profile']}, scope, 'fresh-assignment')
                    later_issue = copy.deepcopy(self.fixture.issues[10])
                    later_issue['body'] = later_issue['body'].replace('- #13', '- #13\n- #14')
                    later = copy.deepcopy(work)
                    later['assignment_id'] = 'later-bug'
                    later['candidates'][0]['issue'] = copy.deepcopy(self.fixture.issues[10])
                    later['affected_intents'] = {'10': {'before': self.fixture.issues[10]['body'], 'body': later_issue['body']}}
                    continued = self.fixture.stored(); continued['bug_work']['later'] = later
                    self.assertTrue(bugs.rescope_body_matches(continued, parked, later_issue))
                    later_issue['body'] += 'uncontrolled change'
                    self.assertFalse(bugs.rescope_body_matches(continued, parked, later_issue))
                    observed = 0
                    def late_tracker(*args):
                        nonlocal observed
                        observed += 1
                        if observed == 2: self.fixture.issues[10]['body'] += 'late unrelated edit'
                        return self.fixture.work['frontier']
                    with patch.object(restart, 'worker', return_value={'container_absent': True}), patch.object(restart, 'workspace', return_value={}), \
                            patch.object(restart, 'tracker', side_effect=late_tracker):
                        refused = restart.reconcile(self.database, 'product', 'm1', '42', self.github)
                    self.assertEqual(refused['status'], 'blocked')
                    self.assertIn({'boundary': 'old-implementation:final-requirements', 'code': 'scope_mismatch'}, refused['blockers'])
                if fault == 'pause':
                    with patch.object(restart, 'reconcile', return_value={'status': 'ready'}):
                        from factory_v1 import controls
                        controls.resume(self.database, 'product', 'm1', '42', 'fixture resume', self.github)
                self.fixture.issues.pop(13)

    def test_edge_intent_durable_and_unknown_absent_edge_not_retried(self):
        work = self.work_record; self.fixture.issues[13] = self.fixture.issue(13, 'fixture bug')
        def lost(*args):
            self.assertEqual(self.fixture.stored()['bug_work']['accent']['edge_intents'], [[13, 1010]])
            self.fixture.edge(*args); raise repositories.RepositoryError('github_unavailable', 'lost edge acknowledgement')
        self.fixture.add_dependency.side_effect = lost
        with self.assertRaises(repositories.RepositoryError): bugs.edges(self.database, self.item, work, self.github, 13, {1010})
        self.fixture.add_dependency.reset_mock(); self.fixture.add_dependency.side_effect = self.fixture.edge
        bugs.edges(self.database, self.item, work, self.github, 13, {1010}); self.fixture.add_dependency.assert_not_called()
        self.fixture.deps[13] = []
        with self.assertRaises(repositories.RepositoryError) as caught: bugs.edges(self.database, self.item, work, self.github, 13, {1010})
        self.assertEqual(caught.exception.code, 'publication_uncertain'); self.fixture.add_dependency.assert_not_called()

    def test_bug_affected_observation_readback(self):
        work = self.work_record
        issue = self.fixture.issue(13, 'fixture bug'); self.fixture.issues[13] = issue
        self.fixture.issues[10]['body'] = self.fixture.issues[10]['body'].replace('None (can start immediately).', '- #13')
        self.fixture.deps[10] = [13]
        good = copy.deepcopy(self.fixture.issues[10])
        observation = self.observation()
        for boundary, faults in (('affected', ('scope', 'body', 'identity', 'native', 'membership')),
                                 ('published', ('body', 'status', 'scope', 'labels', 'native', 'huge', 'title'))):
            if boundary == 'affected': bugs.verify_affected(self.item, work, self.github, issue)
            else:
                bugs.verify_observation(self.item, work, observation)
                work['observation'] = copy.deepcopy(observation)
            for fault in faults:
                with self.subTest(boundary=boundary, fault=fault):
                    self.fixture.issues[10] = copy.deepcopy(good); self.fixture.deps[10] = [13]
                    bad = copy.deepcopy(observation)
                    member = self.fixture.members[10]
                    if boundary == 'affected':
                        if fault == 'scope': member['fields']['SCOPE'] = 'M2'
                        if fault == 'body': self.fixture.issues[10]['body'] = good['body'].replace('- #13', 'None.')
                        if fault == 'identity': self.fixture.issues[10]['id'] = 999
                        if fault == 'native': self.fixture.deps[10] = []
                        if fault == 'membership': self.fixture.members.pop(10)
                    else:
                        if fault == 'body': bad['issue']['body'] = bad['issue']['body'].replace('caf.', 'lost.')
                        if fault == 'status': bad['membership']['status'] = 'unknown'
                        if fault == 'scope': bad['scope'] = 'M2'
                        if fault == 'labels': bad['issue']['labels'] = []
                        if fault == 'native': bad['dependencies'] = [{'number': 12}]
                        if fault == 'huge': bad['issue']['body'] = bad['issue']['body'].replace('None (can start immediately).', '- #' + '9' * 6000)
                        if fault == 'title': bad['issue']['title'] = 'changed'
                    with self.assertRaises(repositories.RepositoryError):
                        if boundary == 'affected': bugs.verify_affected(self.item, work, self.github, issue)
                        else: bugs.verify_observation(self.item, work, bad)
                    self.fixture.members[10] = member; member['fields']['SCOPE'] = 'M1'
        self.github.request.assert_not_called()

    def observation(self):
        work = self.work_record; issue = self.fixture.issue(13, bugs.render(self.item, work)); issue['title'] = work['bug']['title']
        return {'issue': issue, 'scope': 'M1', 'membership': {'id': 'ITEM13', 'status': 'Ready'}, 'dependencies': []}


    def test_bug_publication_effect_admission_1(self):
        baseline = copy.deepcopy(self.item)
        for fault, expected in (('current-intent', 'publication_uncertain'), ('archived-intent', 'publication_uncertain'),
                                ('observation', 'bug_conflict'), ('board', 'projects_blocked')):
            with self.subTest(fault=fault):
                self.item = self.fixture.item = copy.deepcopy(baseline)
                work = self.item['bug_work']['accent']; work['status'] = 'publication_pending'
                if fault == 'current-intent': work['issue_intent'] = {'title': 'unknown'}
                if fault == 'archived-intent':
                    self.item['bug_work_history'] = [{'assignment_id': 'archived-unknown', 'issue_intent': {'title': 'unknown'}, 'publication_complete': False}]
                self.fixture.persist(); self.github.request.reset_mock()
                context = patch.object(bugs, 'work_context', side_effect=repositories.RepositoryError('projects_blocked', 'missing board')) if fault == 'board' else patch.object(bugs, 'work_context', return_value=(work['inputs'], work['board']))
                observations = work['candidates'] + ([{'issue': self.fixture.issue(20, 'new observation')}] if fault == 'observation' else [])
                with context, patch.object(bugs, 'observations', return_value=observations):
                    result = bugs.publish_locked(self.database, self.item, work, self.github)
                self.assertEqual(work['blocker']['code'], expected)
                self.assertFalse(work.get('publication_complete'))
                if fault in ('observation', 'board'):
                    self.assertNotIn('issue_intent', work); self.assertTrue(work['execution']); self.assertTrue(work['bug'])
                self.github.request.assert_not_called(); self.assertEqual(self.fixture.stored(), result)

    def test_single_current_bug_publication_then_read_only_replay_and_completed_release(self):
        work = self.work_record; work['status'] = 'publication_pending'
        self.fixture.persist()
        with patch.object(bugs, 'work_context', return_value=(work['inputs'], work['board'])):
            result = bugs.publish_locked(self.database, self.item, work, self.github)
            self.assertEqual(work['status'], 'published'); self.assertTrue(work['publication_complete'])
            self.assertEqual(work['identity'], {'number': 13, 'id': 1013, 'node_id': 'I13'})
            self.assertEqual(self.fixture.deps, {10: [13], 11: [10], 12: []})
            self.assertEqual(result['ticket_work']['frontier']['eligible'], [12, 13])
            self.assertEqual(self.fixture.issues[13]['body'], bugs.render(self.item, work))
            self.assertEqual(self.fixture.stored(), result)
            self.github.request.reset_mock(); self.fixture.add_dependency.reset_mock(); self.fixture.set_field.reset_mock()
            bugs.publish_locked(self.database, self.item, work, self.github)
            self.github.request.assert_not_called(); self.fixture.add_dependency.assert_not_called(); self.fixture.set_field.assert_not_called()
            self.fixture.issues[13].update(state='closed', state_reason='completed'); self.fixture.members[13]['fields']['STATUS'] = 'Done'
            self.assertEqual(tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']['eligible'], [10, 12])
        self.assertFalse(result['execution_allowed']); self.assertIsNone(result['correlation']['worker_run_id'])

    def test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once(self):
        work = self.work_record; work['status'] = 'publication_pending'; self.fixture.persist()
        def create(path, body, method=None):
            self.assertTrue(self.fixture.stored()['bug_work']['accent']['issue_intent'])
            self.fixture.request(path, body, method)
            raise repositories.RepositoryError('github_unavailable', 'lost creation response')
        with patch.object(bugs, 'work_context', return_value=(work['inputs'], work['board'])):
            self.github.request.side_effect = create
            self.assertEqual(bugs.publish_locked(self.database, self.item, work, self.github)['bug_work']['accent']['status'], 'blocked')
            self.assertEqual(len(self.fixture.issues), 4)
            self.github.request.side_effect = self.fixture.request; self.github.request.reset_mock()
            def member(*args):
                self.assertTrue(self.fixture.stored()['bug_work']['accent']['membership_intent'])
                self.fixture.member(*args); raise repositories.RepositoryError('github_unavailable', 'lost membership acknowledgement')
            self.fixture.add_item.side_effect = member
            self.assertEqual(bugs.publish_locked(self.database, self.item, work, self.github)['bug_work']['accent']['status'], 'blocked')
            self.fixture.add_item.side_effect = self.fixture.member; self.fixture.add_item.reset_mock()
            def field(*args):
                self.assertEqual(self.fixture.stored()['bug_work']['accent']['field_intents'][args[3].lower()], args[4])
                self.fixture.field(*args); raise repositories.RepositoryError('github_unavailable', 'lost field acknowledgement')
            self.fixture.set_field.side_effect = field
            self.assertEqual(bugs.publish_locked(self.database, self.item, work, self.github)['bug_work']['accent']['status'], 'blocked')
            self.fixture.set_field.side_effect = self.fixture.field
            result = bugs.publish_locked(self.database, self.item, work, self.github)
        self.assertTrue(work['publication_complete']); self.assertEqual(len(self.fixture.issues), 4)
        self.fixture.add_item.assert_not_called(); self.github.request.assert_called_once()
        self.assertEqual(self.fixture.stored(), result)


    def test_noncurrent_unknown_and_duplicate_normalized_scope_never_reserve(self):
        self.item.pop('bug_work'); self.fixture.persist()
        original = self.fixture.issues[10]['body']
        variants = []
        for scope in ('deferred', 'recovery', 'unknown', '', 'recovery\ncurrent'):
            body = original + '\n## Bug scope\n\n' + scope + '\n'
            variants += [body, body.replace('## Bug scope\n', '##  Bug scope \t\n'), body.replace('\n', '\r\n')]
        variants += [original + '\n## Bug scope\nrecovery\n## ' + heading + '\ncurrent\n' for heading in ('Bug scope', ' Bug scope', 'Bug scope \t')]
        for body in variants:
            with self.subTest(body=body):
                self.fixture.issues[10]['body'] = body
                frontier = tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']
                self.assertEqual(frontier['eligible'], [12])
                if tickets.sections(body) is None:
                    self.assertFalse(frontier['items'][0]['contract_valid'])
                else:
                    self.assertTrue(frontier['items'][0]['held'])
                with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, 10)
                self.assertEqual(caught.exception.code, 'ticket_ineligible'); self.database.rollback()
                self.assertNotIn('reservation', self.fixture.stored()); self.fixture.set_field.assert_not_called()

    def test_fresh_duplicate_adoption_cannot_erase_archived_unknown_creation(self):
        work = self.make_work(); work['discovery']['affected'] = [10, 12]; work['bug']['affected'] = [10, 12]
        work['scoped_inputs']['12'] = self.fixture.inputs
        work.update(status='publication_pending', duplicate_number=13)
        issue = self.fixture.issue(13, bugs.render(self.item, work)); issue['title'] = work['bug']['title']
        self.fixture.issues[13] = issue; self.fixture.members[13] = {'id': 'ITEM13', 'fields': {'STATUS': 'Ready', 'SCOPE': 'M1'}}
        self.fixture.deps[13] = []
        work['candidates'] = [bugs.observe(self.item, self.github, work['board'], i) for i in self.fixture.issues.values()]
        archived = {'assignment_id': 'obsolete', 'status': 'obsolete', 'requirement_disposition': {'status': 'obsolete'},
                    'discovery': copy.deepcopy(work['discovery']), 'issue_intent': {'title': 'unknown'}, 'publication_complete': False}
        self.item.update(bug_work={'accent': work}, bug_work_history=[archived]); self.fixture.persist()
        with patch.object(bugs, 'work_context', return_value=(work['inputs'], work['board'])):
            result = bugs.publish_locked(self.database, self.item, work, self.github)
        self.assertEqual(work['status'], 'duplicate'); self.assertTrue(work['publication_complete'])
        self.assertEqual(work['identity']['number'], 13); self.assertEqual(result['bug_work_history'], [archived])
        self.assertEqual(bugs.held_numbers(result), {10, 12})
        self.assertFalse(any(call.args[0] == '/repos/example/product/issues' for call in self.github.request.call_args_list))
        self.assertEqual(self.fixture.deps[10], [13]); self.assertEqual(self.fixture.deps[12], [13])
        self.assertEqual(self.fixture.stored(), result)
        self.fixture.issues[13].update(state='closed', state_reason='completed'); self.fixture.members[13]['fields']['STATUS'] = 'Done'
        frontier = tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']
        self.assertEqual(frontier['eligible'], [])
        with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, 10)
        self.assertEqual(caught.exception.code, 'ticket_ineligible'); self.database.rollback(); self.assertNotIn('reservation', self.fixture.stored())

    def test_revised_and_original_affected_scopes_publish_under_their_own_authority(self):
        original = self.fixture.inputs; revised = copy.deepcopy(original)
        revised['handoff']['issue']['number'] = 8
        revised['documents']['milestone.md']['url'] = original['documents']['milestone.md']['url'].replace('a' * 40, 'b' * 40)
        revision = {'spec': revised['handoff'], 'ticket_assignment': 'durable-notes', 'ticket_inputs': 'b' * 64}
        self.item.update(requirement_changes={'durable-notes': {'kind': 'material', 'status': 'resumed', 'affected': [10], 'new_inputs': revised}},
                         requirement_authority={'10': revision})
        self.fixture.issues[10]['body'] = self.fixture.issues[10]['body'].replace(original['documents']['milestone.md']['url'], revised['documents']['milestone.md']['url'])
        work = self.make_work(); work['discovery']['affected'] = [10, 12]; work['bug']['affected'] = [10, 12]
        work['inputs'] = revised; work['scoped_inputs'] = {'10': revised, '12': original}
        work['scoped_revisions'] = {str(n): requirements.authority(self.item, n) for n in (10, 12)}
        work['bug']['references'][0]['spec'] = revised['documents']['milestone.md']['url']
        work['execution'] = self.fixture.evidence(work['bug'], work)
        self.item['bug_work'] = {'accent': work}; self.fixture.persist(); untouched = copy.deepcopy(self.fixture.issues[11])
        with patch.object(tickets, 'pinned_inputs', side_effect=lambda _d, i, _g: copy.deepcopy(revised if i['handoff'] == revised['handoff'] else original)):
            result = bugs.publish_locked(self.database, self.item, work, self.github)
            self.assertTrue(work['publication_complete']); self.assertEqual(work['status'], 'published')
            self.assertEqual(requirements.authority(result, 13), revision); self.assertEqual(requirements.inputs(result, 13), revised)
            self.assertEqual(self.fixture.deps[10], [13]); self.assertEqual(self.fixture.deps[12], [13])
            for n, inputs in ((10, revised), (12, original)):
                self.assertIn(inputs['documents']['milestone.md']['url'], self.fixture.issues[n]['body'])
            self.assertEqual(self.fixture.issues[11], untouched); self.assertEqual(self.fixture.stored(), result)
            self.github.request.reset_mock(); bugs.publish_locked(self.database, self.item, work, self.github); self.github.request.assert_not_called()
