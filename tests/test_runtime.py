import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch
import unittest
from forge.runtime import DockerRuntime, parse_verdict, subprocess_env

class RuntimeTests(unittest.TestCase):
    def verdict(self, **kw):
        data = {"passed": True, "summary": "Checked", "head": "a"*40}
        data.update(kw)
        return json.dumps({"type": "result", "exit_code": 0, "text": json.dumps(data)})

    def test_explicit_verdict(self):
        self.assertTrue(parse_verdict(self.verdict())["passed"])
        self.assertFalse(parse_verdict(self.verdict(passed=False))["passed"])

    def test_malformed_missing_ambiguous_verdict_fails_closed(self):
        for output in ("", "done", self.verdict(passed="yes"), self.verdict(head="main"), self.verdict()+"\n"+self.verdict(), json.dumps({"type":"result","exit_code":1,"text":"{}"})):
            with self.subTest(output=output), self.assertRaises((ValueError, RuntimeError)):
                parse_verdict(output)

    def test_secret_environment_not_forwarded(self):
        os.environ["FORGE_TEST_SECRET"] = "do-not-forward"
        try:
            self.assertNotIn("FORGE_TEST_SECRET", subprocess_env())
            self.assertNotIn("GH_TOKEN", subprocess_env())
            self.assertNotIn("HERMES_HOME", subprocess_env())
        finally:
            del os.environ["FORGE_TEST_SECRET"]

    def test_confinement_flags_and_mounts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/"repo/.git").mkdir(parents=True)
            (p/"runtime.json").write_text(json.dumps({"image":"nousresearch/hermes-agent@sha256:"+"a"*64}))
            runtime = DockerRuntime(p)
            argv = runtime.command(p/"repo", name="test")
            joined = " ".join(argv)
            for expected in ("--read-only", "--cap-drop ALL", "no-new-privileges", "--network none", "dst=/workspace/.git,readonly", "HERMES_WRITE_SAFE_ROOT=/workspace"):
                self.assertIn(expected, joined)
            for forbidden in ("docker.sock", f"src={Path.home() / '.hermes'},", "--privileged", "--network host", "--pid host"):
                self.assertNotIn(forbidden, joined)
            readonly = " ".join(runtime.command(p/"repo", name="test", read_only=True))
            self.assertIn("dst=/workspace,readonly", readonly)

    def test_external_worktree_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/"runtime.json").write_text(json.dumps({"image":"nousresearch/hermes-agent@sha256:"+"a"*64}))
            (p/"repo").mkdir()
            (p/"repo/.git").write_text("gitdir: /personal/repo/.git")
            with self.assertRaises(ValueError):
                DockerRuntime(p).command(p/"repo", name="test")

    def test_stream_output_is_bounded_and_timeout_removes_container(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'runtime.json').write_text(json.dumps({'image':'nousresearch/hermes-agent@sha256:'+'a'*64}))
            runtime=DockerRuntime(root)
            output=runtime.execute([sys.executable,'-c',"print('real subprocess output')"],5,root/'normal.log',name='unit-only')
            self.assertIn('real subprocess output',output)
            # Only the Docker cleanup invocation is mocked; the child/output are real.
            with patch('forge.runtime.subprocess.run') as remove:
                with self.assertRaisesRegex(RuntimeError,'16 MiB'):
                    runtime.execute([sys.executable,'-c',"import sys; sys.stdout.buffer.write(b'x'*(17*1024*1024))"],5,root/'large.log',name='unit-only')
                self.assertLessEqual((root/'large.log').stat().st_size,16*1024*1024)
                self.assertEqual(remove.call_args.args[0],['docker','rm','-f','unit-only'])
            with patch('forge.runtime.subprocess.run') as remove:
                with self.assertRaises(TimeoutError):
                    runtime.execute([sys.executable,'-c','import time; time.sleep(5)'],0.05,root/'timeout.log',name='unit-only')
                remove.assert_called_once()

    def test_image_requires_digest(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            for image in ('nousresearch/hermes-agent:latest','nousresearch/hermes-agent@sha256:'+'z'*64):
                (p/'runtime.json').write_text(json.dumps({'image':image}))
                with self.assertRaises(ValueError):
                    DockerRuntime(p)

if __name__ == "__main__":
    unittest.main()
