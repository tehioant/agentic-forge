import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

spec=importlib.util.spec_from_file_location("forge_install", Path(__file__).resolve().parents[1]/"scripts/install.py")
assert spec is not None and spec.loader is not None
install=importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

class SnapshotTests(unittest.TestCase):
    def test_access_only_snapshot_excludes_all_other_credentials(self):
        # Clearly synthetic local auth fixture. No provider is called.
        payload=base64.urlsafe_b64encode(json.dumps({"exp":time.time()+7200}).encode()).decode().rstrip("=")
        token=f"header.{payload}.signature"
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            source=p/"source.json"
            source.write_text(json.dumps({"providers":{"spotify":{"refresh_token":"music-test"}},"credential_pool":{"openai-codex":[{"access_token":token,"refresh_token":"refresh-test","base_url":"https://chatgpt.com/backend-api/codex"}],"openai-api":[{"api_key":"api-test"}]}}))
            out=p/"secrets/access.json"
            with contextlib.redirect_stdout(io.StringIO()) as captured:
                install.snapshot(source,out)
            data=json.loads(out.read_text())
            self.assertEqual(data["providers"],{})
            self.assertEqual(list(data["credential_pool"]),["openai-codex"])
            self.assertNotIn("refresh_token",data["credential_pool"]["openai-codex"][0])
            self.assertNotIn(token,captured.getvalue())
            self.assertEqual(out.stat().st_mode & 0o777,0o600)
            self.assertEqual(out.parent.stat().st_mode & 0o777,0o700)

    def test_missing_or_expired_auth_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            source=p/"source.json"
            source.write_text('{"credential_pool":{"openai-codex":[]}}')
            with self.assertRaises(RuntimeError):
                install.snapshot(source,p/"output.json")
            self.assertFalse((p/"output.json").exists())

if __name__ == '__main__':
    unittest.main()
