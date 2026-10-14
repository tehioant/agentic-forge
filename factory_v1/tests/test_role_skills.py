"""Operator-reviewed engineering/diff-review revisions, never ambient edits."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from factory_v1 import role_skills
from factory_v1.repositories import RepositoryError

CURRENT_IMPLEMENT = '136a8987143241a0229952a56d9c41042646073769fcad466d808bd5e40bb4f0'
CURRENT_CODE_REVIEW = '1ff8f642af17fe58110d991738b8d881cb8ae3a1b5bc6b1abddc380f8b26212e'
STRUCTURED_TEXT = 'code-review/references/structured-text-contracts.md'
CURRENT_STRUCTURED_TEXT = '3f38253626d8b1a828d2511e2dfe1ac6937a5a25544138c0530ddfa0031b02a8'


class ReviewedImplementTests(unittest.TestCase):
    def test_current_operator_revision_has_no_forced_tdd_dependency(self):
        self.assertEqual(role_skills.closure('implementation'), ['implement'])
        self.assertEqual(role_skills.SELECTED['implement'][1], CURRENT_IMPLEMENT)
        self.assertNotIn('tdd', role_skills.DEPENDENCIES.get('implement', []))
        self.assertIn('simplif', role_skills.SUPPORT_POLICY['implement-review'])
        self.assertIn('controller', role_skills.SUPPORT_POLICY['implement-review'])

    def test_reviewed_snapshot_is_admitted_but_later_byte_drift_is_refused(self):
        snapshot = Path(__file__).parent / 'role_skill_snapshots/implement/SKILL.md'
        raw = snapshot.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), CURRENT_IMPLEMENT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'SKILL.md'
            path.write_bytes(raw)
            descriptor = {'name': 'implement', 'source': role_skills.SELECTED['implement'][0],
                          'path': str(path), 'sha256': CURRENT_IMPLEMENT, 'dependencies': []}
            selected = role_skills.snapshots('implementation', [descriptor])
            self.assertEqual(selected[0]['instructions'], raw.decode())
            path.write_bytes(raw + b'\nUNREVIEWED CHANGE\n')
            for pin in (CURRENT_IMPLEMENT, hashlib.sha256(path.read_bytes()).hexdigest()):
                with self.subTest(pin=pin), self.assertRaises(RepositoryError) as refused:
                    role_skills.snapshots('implementation', [{**descriptor, 'sha256': pin}])
                self.assertEqual(refused.exception.code, 'skill_blocked')

    def review_descriptors(self, directory):
        descriptors = []
        for name, suffix, pin, dependencies in (
                ('code-review', 'code-review/SKILL.md', CURRENT_CODE_REVIEW, [STRUCTURED_TEXT]),
                (STRUCTURED_TEXT, STRUCTURED_TEXT, CURRENT_STRUCTURED_TEXT, [])):
            raw = (Path(__file__).parent / 'role_skill_snapshots' / suffix).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), pin)
            self.assertEqual(role_skills.SELECTED[name][1], pin)
            path = Path(directory) / suffix
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            descriptors.append({'name': name, 'source': '/home/ops/.hermes/skills/' + suffix,
                                'path': str(path), 'sha256': pin, 'dependencies': dependencies})
        return descriptors

    def test_both_review_axes_admit_reviewed_bytes_and_refuse_drift_or_caller_repins(self):
        for stage in ('review-standards', 'review-spec'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(role_skills.closure(stage), ['code-review', STRUCTURED_TEXT])
                descriptors = self.review_descriptors(directory)
                selected = role_skills.snapshots(stage, descriptors)
                for item, descriptor in zip(selected, descriptors):
                    self.assertEqual(item['instructions'], Path(descriptor['path']).read_text())
                for index, descriptor in enumerate(descriptors):
                    for field, value in [('sha256', '47f4e52c21694def9c7c11cbfbf891ca35eac7a93e395797515be3c8a409ae50'),
                                         ('dependencies', ['references/release-readiness.md']),
                                         ('source', '/unreviewed/code-review/SKILL.md')]:
                        changed = copy.deepcopy(descriptors)
                        changed[index][field] = value
                        with self.subTest(name=descriptor['name'], field=field), self.assertRaises(RepositoryError) as refused:
                            role_skills.snapshots(stage, changed)
                        self.assertEqual(refused.exception.code, 'skill_blocked')
                    path = Path(descriptor['path'])
                    raw = path.read_bytes()
                    path.write_bytes(raw + b'\nUNREVIEWED CHANGE\n')
                    for pin in (descriptor['sha256'], hashlib.sha256(path.read_bytes()).hexdigest()):
                        changed = copy.deepcopy(descriptors)
                        changed[index]['sha256'] = pin
                        with self.subTest(name=descriptor['name'], pin=pin), self.assertRaises(RepositoryError) as refused:
                            role_skills.snapshots(stage, changed)
                        self.assertEqual(refused.exception.code, 'skill_blocked')
                    path.write_bytes(raw)

    def test_both_review_axes_refuse_dependency_omission_without_hiding_the_graph_guard(self):
        for stage in ('review-standards', 'review-spec'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                descriptors = self.review_descriptors(directory)
                missing_edge = copy.deepcopy(descriptors)
                missing_edge[0]['dependencies'] = []
                for label, configuration in [('missing reference', descriptors[:1]),
                                             ('missing dependency edge with complete count', missing_edge),
                                             ('unknown extra reference', descriptors + [{**descriptors[1], 'name': 'release-readiness'}])]:
                    with self.subTest(case=label), self.assertRaises(RepositoryError) as refused:
                        role_skills.snapshots(stage, configuration)
                    self.assertEqual(refused.exception.code, 'skill_blocked')
                self.assertEqual([s['name'] for s in role_skills.snapshots(stage, descriptors)],
                                 ['code-review', STRUCTURED_TEXT])

    def test_review_dependency_omission_refuses_before_tracker_or_claim(self):
        import sqlite3
        from unittest.mock import Mock, patch
        from factory_v1 import assignments
        from factory_v1.model_access import SUBSCRIPTION_SCOPE
        for stage in ('review-standards', 'review-spec'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                descriptors = self.review_descriptors(directory)
                def artifact(name, content):
                    return {'name': name, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
                request = {'stage': stage, 'repository': 'example/product', 'repository_id': 123,
                           'workspace': directory + '/workspace',
                           'issue': {'number': 10, 'id': 1010, 'node_id': 'I10', 'body_sha256': 'a' * 64},
                           'spec_commit': 'a' * 40, 'baseline': 'a' * 40, 'candidate': 'b' * 40,
                           'standards': [artifact('AGENTS.md', 'Meaningful deterministic tests.')],
                           'preceding': [artifact('candidate', 'b' * 40), artifact('simplification-evidence', 'Test evidence.')],
                           'profile': {'name': 'test-' + stage, 'home': directory + '/profile', 'role': 'review',
                                       'provider': SUBSCRIPTION_SCOPE[0], 'model': SUBSCRIPTION_SCOPE[1],
                                       'operation': SUBSCRIPTION_SCOPE[2], 'reasoning': 'high'},
                           'skills': descriptors, 'capabilities': ['read_workspace', 'scratch', 'model'],
                           'result_contract': assignments.CONTRACT, 'claim_id': 'test-review-claim'}
                self.assertEqual([s['name'] for s in assignments.validate(request)], ['code-review', STRUCTURED_TEXT])
                database, github = sqlite3.connect(':memory:'), Mock()
                try:
                    for defect in ('missing-reference', 'missing-edge', 'reference-pin'):
                        changed = copy.deepcopy(request)
                        if defect == 'missing-reference':
                            changed['skills'].pop()
                        elif defect == 'missing-edge':
                            changed['skills'][0]['dependencies'] = []
                        else:
                            changed['skills'][1]['sha256'] = '0' * 64
                        with self.subTest(defect=defect), patch.object(assignments, 'current') as current:
                            with self.assertRaises(RepositoryError) as refused:
                                assignments.prepare(database, 'product', 'm1', '42', changed, github)
                            self.assertEqual(refused.exception.code, 'skill_blocked')
                            current.assert_not_called()
                            github.request.assert_not_called()
                            self.assertEqual(database.execute('SELECT name FROM sqlite_master').fetchall(), [])
                finally:
                    database.close()

    def test_updated_adaptations_defer_final_suite_and_release_review_to_explicit_stages(self):
        for stage in ('implementation', 'corrections', 'repair'):
            with self.subTest(stage=stage):
                self.assertIn('stabilized', ' '.join(role_skills.STAGE_RULES[stage]))
                self.assertIn('controller-final-suite', role_skills.ADAPTATIONS[stage])
                self.assertIn('contract-fixture-audit', role_skills.ADAPTATIONS[stage])
                self.assertIn('failed-suite-fixture-evidence', role_skills.ADAPTATIONS[stage])
        self.assertIn('Interrupted', ' '.join(role_skills.STAGE_RULES['implementation']))
        self.assertIn('hand-built', ' '.join(role_skills.STAGE_RULES['implementation']))
        self.assertIn('assertion ASTs', role_skills.SUPPORT_POLICY['implement-review'])
        self.assertIn('complete failure trace', ' '.join(role_skills.STAGE_RULES['corrections']))
        self.assertIn('unselected', role_skills.SUPPORT_POLICY['code-review'])
        for stage in ('review-standards', 'review-spec'):
            self.assertIn('correction delta', ' '.join(role_skills.STAGE_RULES[stage]))
            self.assertIn('structured-text-contracts', ' '.join(role_skills.STAGE_RULES[stage]))
            self.assertEqual(role_skills.STAGES[stage], ('code-review',))


if __name__ == '__main__':
    unittest.main()
