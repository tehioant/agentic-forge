import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from forge.notify import collect, main

class NotifyTests(unittest.TestCase):
    def test_changes_dedup_and_secrets_never_emitted(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            (home/'projects/p').mkdir(parents=True)
            (home/'projects.json').write_text(json.dumps({'p':{'tickets':{'T1':{'status':'review','spec':'PRIVATE SPEC','summary':'PRIVATE SUMMARY'}}}}))
            (home/'projects/p/state.json').write_text(json.dumps({'failed':True,'error':'PRIVATE ERROR'}))
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['--home',str(home)]),0)
            self.assertIn('blocked',out.getvalue())
            self.assertIn('review-ready',out.getvalue())
            self.assertNotIn('PRIVATE',out.getvalue())
            self.assertEqual(collect(home)[0],[])
            (home/'projects/p/state.json').write_text('{"failed":false}')
            with contextlib.redirect_stdout(io.StringIO()):
                main(['--home',str(home)])
            (home/'projects/p/state.json').write_text('{"failed":true}')
            self.assertEqual(len(collect(home)[0]),1)

    def test_no_registered_projects_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(collect(Path(d))[0],[])

if __name__=='__main__':
    unittest.main()
