"""Issue 24 correction regressions: SQLite components, mocked external reads only."""
import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, attention, delivery, diagnosis, iteration, planning, requirement_publication, requirements, tickets
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.tests import test_iteration as fixtures


def fixture(owner, cls):
    value = cls(methodName='runTest')
    value.setUp()
    owner.addCleanup(value.doCleanups)
    return value


class CompletionRegressions(unittest.TestCase):
    def retired(self, kind, runtime):
        f = fixture(self, fixtures.IterationComponents)
        assignment = {'assignment_id': 'retired', 'ticket_scope': {'project': 'product', 'iteration': 'm1'}}
        if kind == 'diagnosis':
            f.item['stuck_work'] = {'old': {'status': 'released', 'affected': [1], 'blocks_verified': True,
                                          'retired_assignments': ['retired']}}
            assignment['stuck_disposition'] = 'superseded'
        else:
            f.item['requirement_changes'] = {'old': {'kind': 'material', 'status': 'resumed', 'affected': [1], 'blocks_verified': True}}
            assignment['requirement_disposition'] = {'change_id': 'old'}
        if runtime is not None:
            assignment['runtime'] = runtime
        f.store()
        return f, assignment

    def test_retired_runtime_requires_verified_container_absence(self):
        for kind in ('diagnosis', 'requirements'):
            for status in ('stopped', 'failed', 'complete'):
                for removed in (False, None):
                    with self.subTest(kind=kind, status=status, removed=removed):
                        f, assignment = self.retired(kind, {'status': status, 'container_removed': removed})
                        release = diagnosis.released_claim if kind == 'diagnosis' else requirements.released_claim
                        self.assertFalse(release(f.db, assignment))
                        with patch.object(assignments, 'rows', return_value=[assignment]):
                            with self.assertRaises(RepositoryError):
                                f.complete()
                        self.assertNotIn('completion', f.stored())
                        self.assertEqual(attention.records(f.db, 'product', 'm1', f.item), [])

    def test_retired_unlaunched_and_verified_terminal_workers_remain_admissible(self):
        for kind in ('diagnosis', 'requirements'):
            for runtime in (None, {'status': 'stopped', 'container_removed': True}):
                with self.subTest(kind=kind, runtime=runtime):
                    f, assignment = self.retired(kind, runtime)
                    release = diagnosis.released_claim if kind == 'diagnosis' else requirements.released_claim
                    self.assertTrue(release(f.db, assignment))
                    with patch.object(assignments, 'rows', return_value=[assignment]):
                        self.assertEqual(f.complete()['report']['delivered'], [f.delivered])

    def test_scope_and_raw_issue_drift_refuse_without_report_or_attention(self):
        for body in ('## Bug scope\ncurrent\n', '## Bug scope\nrecovery\n', '## Bug scope\ndeferred\n## Detail\nChanged\n'):
            with self.subTest(body=body):
                f = fixture(self, fixtures.IterationComponents)
                row = fixtures.work_row(2)
                row.update(status='deferred', held=True, issue_state='open')
                f.observed['items'].append(row)
                issue = {'body': '## Bug scope\ndeferred\n', 'state': 'open'}
                f.mocks['tickets.gh.read_issue'].side_effect = lambda api, repo, n: copy.deepcopy(issue) if n == 2 else {'body': 'accepted', 'state': 'closed'}
                def observe(config):
                    issue['body'] = body
                    return f.health
                f.mocks['delivery.observe'].side_effect = observe
                with self.assertRaises(RepositoryError):
                    f.complete()
                self.assertNotIn('completion', f.stored())
                self.assertEqual(attention.records(f.db, 'product', 'm1', f.item), [])


class DeliveryContinuationRegressions(unittest.TestCase):
    def test_failed_unknown_and_later_old_samples_reach_new_mutation_boundary(self):
        from factory_v1.tests.test_delivery import configuration, DeliveryTarget
        config = configuration('http://127.0.0.1:9876')
        for status in ('failed', 'unknown', 'healthy'):
            with self.subTest(status=status):
                f = fixture(self, fixtures.ContinuationComponents)
                target = DeliveryTarget(config)
                with patch.object(delivery, 'http_json', side_effect=lambda url, timeout: target.value('/health')):
                    healthy = delivery.observe(config, api=type('API', (), {'read': lambda self, suffix: target.value(suffix)})())
                self.assertEqual(healthy['status'], 'healthy')
                failed = dict(healthy, status=status, failure=None if status == 'healthy' else 'delivery_unknown')
                with patch.object(delivery, 'observe', return_value=failed):
                    delivery.sample(f.db, 'product', 'm1', '42', config)
                    before = delivery.context(f.db, 'product', 'm1', '42')
                    started = f.start()
                    self.assertEqual(started.get('delivery_monitor'), before['delivery_monitor'])
                    self.assertEqual(started.get('main_incident'), before.get('main_incident'))
                    boundary = delivery.mutation_boundary(f.db, 'product', 'm2', '42')
                    self.assertEqual(boundary.get('main_incident'), before.get('main_incident'))
                    self.assertEqual(attention.records(f.db, 'product', 'm2', started), [])
                later = dict(healthy, status='unknown', failure='delivery_unknown')
                with patch.object(delivery, 'observe', return_value=later):
                    delivery.sample(f.db, 'product', 'm1', '42')
                current = delivery.inspect(f.db, 'product', 'm2', '42')
                self.assertEqual(current['monitor']['latest']['status'], 'unknown')
                self.assertIsNotNone(current['incident'])
                with patch.object(delivery, 'observe', return_value=healthy):
                    delivery.sample(f.db, 'product', 'm1', '42')
                self.assertIsNone(delivery.inspect(f.db, 'product', 'm2', '42')['incident'])
                self.assertEqual(f.start()['delivery_monitor']['latest']['status'], 'healthy')


class ScopedInputRegressions(unittest.TestCase):
    def test_current_authorities_supply_all_documents_and_previous_pointers(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                f = fixture(self, fixtures.ContinuationComponents)
                updated = dict(copy.deepcopy(f.item['handoff']), commit='d' * 40, issue={'number': 9, 'id': 90})
                docs = {'milestone.md': {'url': 'https://example.test/updated-spec', 'sha256': 'e' * 64}}
                revision = {'spec': updated, 'ticket_assignment': 'revision1', 'ticket_inputs': 'f' * 64}
                f.item['requirement_authority'] = {'1': revision}
                f.item['requirement_changes'] = {'revision1': {'status': 'resumed', 'new_revision': revision,
                    'new_inputs': {'handoff': updated, 'documents': docs}}}
                delivered = [{'issue': {'number': 1}, 'requirements': revision}]
                if mixed:
                    delivered.append({'issue': {'number': 2}, 'requirements': requirements.authority(f.item, 2)})
                report = f.item['completion']['report']
                report['delivered'] = delivered
                report_id = assignments.digest(report)
                f.item['completion'].update(report_id=report_id, event_id='completion-' + report_id)
                f.request['event_id'] = f.item['completion']['event_id']
                save_iteration(f.db, f.item)
                f.db.commit()
                started = f.start()
                continuation = started['planning']['continuation']
                self.assertIn(docs['milestone.md']['url'], continuation['input_loads'])
                self.assertEqual(continuation['input_loads'][docs['milestone.md']['url']], 'e' * 64)
                self.assertEqual(continuation['previous_specs']['1'], updated)
                if mixed:
                    self.assertEqual(continuation['previous_specs']['2'], f.item['handoff'])
                    self.assertIn('https://example.test/spec', continuation['input_loads'])
                else:
                    self.assertEqual(continuation['previous_spec'], updated)
                    self.assertNotIn('https://example.test/spec', continuation['input_loads'])
                evidence = f.evidence(started)
                planning.continuation_evidence(started['planning'], evidence, started['planning']['skills'])
                evidence['execution']['loads'] = [load for load in evidence['execution']['loads'] if load['source'] != docs['milestone.md']['url']]
                with self.assertRaises(RepositoryError):
                    planning.continuation_evidence(started['planning'], evidence, started['planning']['skills'])
                request = f.evidence(started)
                request.update(assignment_id=started['planning']['assignment_id'], origin=fixtures.ORIGIN,
                    rationale='Actual answers', reference='agent', commit=updated['commit'], issue_number=11)
                request['documents']['milestone.md'] = '\n'.join('## ' + s + '\nAgreed prose.\n' for s in planning.SECTIONS)
                request['execution']['result_digest'] = assignments.digest(request['documents'])
                with f.db, self.assertRaisesRegex(RepositoryError, 'new next-iteration specification pointers'):
                    planning.complete(f.db, started, request, Mock())
                report['delivered'][0]['requirements'] = dict(revision, ticket_inputs='0' * 64)
                with self.assertRaisesRegex(RepositoryError, 'authority changed'):
                    iteration.continuation_inputs(f.item)


class RevisionPublicationRegressions(unittest.TestCase):
    def test_scoped_revision_readback_does_not_repeat_the_completed_interview(self):
        f = fixture(self, fixtures.ContinuationComponents)
        item = f.start()
        item['handoff'] = copy.deepcopy(f.item['handoff'])
        item['planning']['status'] = 'complete'
        docs = {name: '# Agreed revised prose' for name in ('milestone.md', 'vision.md', 'provenance.md')}
        docs['milestone.md'] = '\n'.join('## ' + s + '\nAgreed prose.\n' for s in planning.SECTIONS)
        record = {'status': 'synthesized', 'request': {'change_id': 'revision1'}, 'documents': docs, 'tickets': [],
                  'resolution': {'content': 'New agreed scope', 'reference': 'operator-direction'},
                  'synthesis_request': {'skills': f.request['skills']}, 'affected': []}
        item['requirement_changes'] = {'revision1': record}
        save_iteration(f.db, item)
        f.db.commit()
        corrupt = {'value': False}
        def read(path):
            if '/git/commits/' in path:
                return {'sha': 'e' * 40}
            if '/contents/' in path:
                name = path.split('/')[-1].split('?')[0]
                raw = (docs[name] + ('drift' if corrupt['value'] else '')).encode()
                return {'type': 'file', 'path': 'docs/revision1/' + name, 'encoding': 'base64',
                        'content': base64.b64encode(raw).decode(),
                        'sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}
            return {'number': 9, 'id': 90, 'html_url': 'https://github.com/example/product/issues/9',
                    'repository_url': 'https://api.github.com/repos/example/product', 'state': 'open',
                    'body': docs['milestone.md'], 'labels': [{'name': 'ready-for-agent'}]}
        github = Mock(base='http://127.0.0.1:9876')
        github.request.side_effect = read
        with patch.object(requirement_publication, 'verify_synthesis'), patch.object(requirement_publication, 'reconcile'), \
             patch.object(requirement_publication, 'version_documents', return_value={'path': 'docs/revision1', 'commit': 'e' * 40}), \
             patch.object(requirement_publication, 'spec_issue', return_value=9), \
             patch.object(tickets, 'pinned_inputs', side_effect=lambda db, shadow, api: {'handoff': shadow['handoff'], 'documents': {}}), \
             patch.object(requirement_publication, 'verify_publication'), patch.object(requirements, 'sync_blocks'), \
             patch.object(planning, 'resolve_planning', return_value={}), patch.object(planning, 'reconcile'):
            result = requirement_publication.publish(f.db, 'product', 'm2', '42', {'change_id': 'revision1'}, github)
            revised = result['requirement_changes']['revision1']
            self.assertEqual(revised['status'], 'published')
            self.assertEqual(revised['new_revision']['spec']['commit'], 'e' * 40)
            self.assertEqual(revised['new_revision']['spec']['issue']['number'], 9)
            self.assertEqual(result['planning'], item['planning'])
            self.assertEqual(result['handoff'], item['handoff'])
            revised['status'] = 'synthesized'
            save_iteration(f.db, result)
            f.db.commit()
            corrupt['value'] = True
            with self.assertRaisesRegex(RepositoryError, 'Published docs differ'):
                requirement_publication.publish(f.db, 'product', 'm2', '42', {'change_id': 'revision1'}, github)
        # Versioned readback is not a substitute for actual selected resynthesis.
        with patch('factory_v1.skills.resolve', return_value={}):
            with self.assertRaisesRegex(RepositoryError, 'Actual selected synthesis required'):
                requirement_publication.verify_synthesis(item, record)
