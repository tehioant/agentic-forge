"""Operator-reviewed engineering/diff-review revisions, never ambient edits."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from factory_v1 import role_skills
from factory_v1.repositories import RepositoryError

CURRENT_IMPLEMENT = '290299fd286a7bab61001e3c0f0b898079332511e39b2772faac42ce99514288'
CURRENT_CODE_REVIEW = '0eec800da607853d6038c4fae3815896588b25a6ba84bcbcefb26218cef4df0f'


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
            with self.assertRaises(RepositoryError) as refused:
                role_skills.snapshots('implementation', [descriptor])
            self.assertEqual(refused.exception.code, 'skill_blocked')

    def test_both_review_axes_admit_reviewed_bytes_and_refuse_drift_or_caller_repins(self):
        raw = (Path(__file__).parent / 'role_skill_snapshots/code-review/SKILL.md').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), CURRENT_CODE_REVIEW)
        self.assertEqual(role_skills.SELECTED['code-review'][1], CURRENT_CODE_REVIEW)
        for stage in ('review-standards', 'review-spec'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(role_skills.closure(stage), ['code-review'])
                path = Path(directory) / 'SKILL.md'
                path.write_bytes(raw)
                descriptor = {'name': 'code-review', 'source': role_skills.SELECTED['code-review'][0],
                              'path': str(path), 'sha256': CURRENT_CODE_REVIEW, 'dependencies': []}
                self.assertEqual(role_skills.snapshots(stage, [descriptor])[0]['instructions'], raw.decode())
                for field, value in [('sha256', '47f4e52c21694def9c7c11cbfbf891ca35eac7a93e395797515be3c8a409ae50'),
                                     ('dependencies', ['references/release-readiness.md']),
                                     ('source', '/unreviewed/code-review/SKILL.md')]:
                    with self.subTest(field=field), self.assertRaises(RepositoryError) as refused:
                        role_skills.snapshots(stage, [{**descriptor, field: value}])
                    self.assertEqual(refused.exception.code, 'skill_blocked')
                path.write_bytes(raw + b'\nUNREVIEWED CHANGE\n')
                for pin in (CURRENT_CODE_REVIEW, hashlib.sha256(path.read_bytes()).hexdigest()):
                    with self.subTest(pin=pin), self.assertRaises(RepositoryError) as refused:
                        role_skills.snapshots(stage, [{**descriptor, 'sha256': pin}])
                    self.assertEqual(refused.exception.code, 'skill_blocked')

    def test_updated_adaptations_defer_final_suite_and_release_review_to_explicit_stages(self):
        for stage in ('implementation', 'corrections', 'repair'):
            with self.subTest(stage=stage):
                self.assertIn('stabilized', ' '.join(role_skills.STAGE_RULES[stage]))
                self.assertIn('controller-final-suite', role_skills.ADAPTATIONS[stage])
        self.assertIn('Interrupted', ' '.join(role_skills.STAGE_RULES['implementation']))
        self.assertIn('unselected', role_skills.SUPPORT_POLICY['code-review'])
        for stage in ('review-standards', 'review-spec'):
            self.assertIn('correction delta', ' '.join(role_skills.STAGE_RULES[stage]))


if __name__ == '__main__':
    unittest.main()
