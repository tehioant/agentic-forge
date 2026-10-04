"""The operator-reviewed implement revision is admitted, not ambient edits."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from factory_v1 import role_skills
from factory_v1.repositories import RepositoryError

CURRENT_IMPLEMENT = '6b13bcb6119df090c97be3c8a90560071a26f21f4fc6ad0facde97b25ecb1da0'


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


if __name__ == '__main__':
    unittest.main()
