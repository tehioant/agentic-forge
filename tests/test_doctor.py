import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from forge.doctor import check_configuration, main


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.runtime = self.home / "runtime.json"
        self.runtime.write_text(json.dumps({"image": "nousresearch/hermes-agent@sha256:" + "a" * 64}))
        for role in ("builder", "simplifier", "reviewer"):
            template = self.home / "templates" / role / "config.yaml"
            template.parent.mkdir(parents=True)
            template.write_text("model: fixture\n")
        self.secret = self.home / "secrets" / "codex-access.json"
        self.secret.parent.mkdir()
        # Synthetic, invalid UTF-8/JSON content: nothing here may be parsed.
        self.secret.write_bytes(b"\xff\x00arbitrary synthetic secret /do/not/disclose")
        self.secret.chmod(0o600)

    def assert_failed(self, check):
        report = check_configuration(self.home)
        self.assertIs(report["ready"], False)
        self.assertIs(report["checks"][check], False)
        self.assertTrue(report["failures"][check])
        self.assertTrue(all(type(value) is bool for value in report["checks"].values()))
        return report

    def test_ready_stat_only_and_no_mutations(self):
        original_open = Path.open

        def settings_only(path, *args, **kwargs):
            self.assertEqual(path, self.runtime, "Only runtime settings may be opened")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", settings_only), \
                patch.object(Path, "chmod", side_effect=AssertionError("chmod forbidden")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir forbidden")), \
                patch.object(Path, "write_text", side_effect=AssertionError("write forbidden")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("write forbidden")), \
                patch("subprocess.run", side_effect=AssertionError("subprocess forbidden")):
            report = check_configuration(self.home)
        self.assertIs(report["ready"], True)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["failures"], {})
        self.assertNotIn("arbitrary synthetic secret", json.dumps(report))
        self.assertNotIn("/do/not/disclose", json.dumps(report))

    def test_missing_runtime(self):
        self.runtime.unlink()
        self.assert_failed("runtime")

    def test_runtime_aliases_never_open_credentials(self):
        for alias in ("symlink", "hardlink", "snapshot_symlink"):
            with self.subTest(alias=alias):
                self.runtime.unlink()
                if alias == "symlink":
                    self.runtime.symlink_to(self.secret)
                elif alias == "hardlink":
                    self.runtime.hardlink_to(self.secret)
                else:
                    self.runtime.write_bytes(b"synthetic credential content")
                    self.secret.unlink()
                    self.secret.symlink_to(self.runtime)
                with patch.object(Path, "open", side_effect=AssertionError("credential read forbidden")):
                    report = self.assert_failed("runtime")
                self.assertIs(report["checks"]["settings"], False)
                self.assertNotIn("synthetic credential content", json.dumps(report))

    def test_dangling_runtime_snapshot_alias_is_not_opened(self):
        self.runtime.unlink()
        self.secret.unlink()
        self.runtime.symlink_to(self.secret)
        with patch.object(Path, "open", side_effect=AssertionError("credential read forbidden")):
            self.assert_failed("runtime")

    def test_malformed_settings(self):
        for content in (b"{", b"\xff", b"[]", b"null", b'"text"', b"42"):
            with self.subTest(content=content):
                self.runtime.write_bytes(content)
                self.assert_failed("settings")

    def test_unreadable_settings(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("sensitive exception text")):
            report = self.assert_failed("settings")
        self.assertNotIn("sensitive exception text", json.dumps(report))

    def test_invalid_images(self):
        prefix = "nousresearch/hermes-agent@sha256:"
        for image in (None, 42, [], {}, "", "nousresearch/hermes-agent:latest",
                      prefix + "a" * 63, prefix + "a" * 65, prefix + "z" * 64,
                      prefix + "A" * 64, prefix + "a" * 64 + "\n",
                      "other/image@sha256:" + "a" * 64):
            with self.subTest(image=image):
                self.runtime.write_text(json.dumps({"image": image}))
                self.assert_failed("image")
        self.runtime.write_text("{}")
        self.assert_failed("image")

    def test_each_missing_template(self):
        for role in ("builder", "simplifier", "reviewer"):
            with self.subTest(role=role):
                template = self.home / "templates" / role / "config.yaml"
                template.unlink()
                self.assert_failed(f"{role}_template")
                template.write_text("fixture")

    def test_missing_snapshot(self):
        self.secret.unlink()
        self.assert_failed("inference_snapshot")

    def test_unsafe_permissions(self):
        for mode in (0o644, 0o640, 0o666, 0o700, 0o400, 0o000, 0o4600):
            with self.subTest(mode=oct(mode)):
                self.secret.chmod(mode)
                self.assert_failed("inference_permissions")
        self.secret.chmod(0o600)

    def test_snapshot_must_be_regular_non_symlink(self):
        self.secret.unlink()
        self.secret.mkdir(mode=0o600)
        self.assert_failed("inference_snapshot")
        self.secret.rmdir()
        self.secret.symlink_to(self.runtime)
        self.runtime.chmod(0o600)
        self.assert_failed("inference_snapshot")

    def test_stat_errors_fail_closed_without_disclosing_exception(self):
        original_stat = Path.stat

        def deny_secret(path, *args, **kwargs):
            if path == self.secret:
                raise PermissionError("private exception")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", deny_secret):
            report = self.assert_failed("inference_snapshot")
        self.assertNotIn("private exception", json.dumps(report))

    def test_empty_missing_and_non_directory_home(self):
        with tempfile.TemporaryDirectory() as empty:
            for home in (Path(empty), Path(empty) / "missing", self.runtime, None):
                with self.subTest(home=home):
                    report = check_configuration(home)
                    self.assertIs(report["ready"], False)
                    self.assertTrue(report["failures"])
            self.assertFalse((Path(empty) / "missing").exists())
            self.assertEqual(list(Path(empty).iterdir()), [])

    def test_cli_exit_and_json(self):
        for ready in (True, False):
            with self.subTest(ready=ready):
                if not ready:
                    self.secret.unlink()
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "forge.doctor", "--home", str(self.home)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0 if ready else 1)
                self.assertIs(json.loads(result.stdout)["ready"], ready)
                self.assertEqual(result.stderr, "")
                self.assertNotIn("arbitrary synthetic secret", result.stdout)

    def test_cli_missing_or_empty_home(self):
        for argv in (["doctor"], ["doctor", "--home", ""], ["doctor", "--home", "   "]):
            with self.subTest(argv=argv), patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(), 1)
                self.assertIs(json.loads(output.getvalue())["ready"], False)


if __name__ == "__main__":
    unittest.main()
