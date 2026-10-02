from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from forge.core import Forge, ForgeError


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


class FakeNative:
    """Deliberate unit-test double matching the shipped CLI, not live evidence."""
    def __init__(self):
        self.cards = {}
        self.commands = []
        self.envs = []
        self.projects = {}

    def __call__(self, argv, *, env, timeout):
        self.commands.append(list(argv))
        self.envs.append(dict(env))
        args = argv[1:]
        if args[:2] == ["project", "create"]:
            self.projects[args[2]] = args[3]
            return "created"
        if args[:3] == ["kanban", "boards", "create"] or args[:2] == ["project", "bind-board"]:
            return "ok"
        if args[:2] == ["project", "show"]:
            slug=args[2]
            return f"{slug} [project-id]\n  primary: {self.projects[slug]}\n  board: {slug}"
        if args[:3] == ["kanban", "boards", "list"]:
            return json.dumps([{"slug":s,"default_workdir":r} for s,r in self.projects.items()])
        if "create" in args:
            tid = f"T{len(self.cards) + 1}"
            self.cards[tid] = {"id": tid, "status": "blocked", "completion_contract": "local-only", "assignee": "forge-external"}
            return json.dumps(self.cards[tid])
        if "show" in args:
            return json.dumps({"task":self.cards[args[-2] if args[-1] == "--json" else args[-1]]})
        if "unblock" in args:
            tid=args[args.index("unblock")+1]
            self.cards[tid]["status"]="ready"
            return "ok"
        for verb,status in (("claim","running"),("request-review","review"),("block","blocked")):
            if verb in args:
                tid=args[args.index(verb)+1]
                self.cards[tid]["status"]=status
                return "ok"
        if "list" in args and "--json" in args:
            return json.dumps(list(self.cards.values()))
        raise ForgeError(f"Unexpected native test-double command: {args}")


class FakeRuntime:
    def __init__(self, *, fail_stage=None, reviewer_pass=True, mutate_reviewer=False):
        self.fail_stage = fail_stage
        self.reviewer_pass = reviewer_pass
        self.mutate_reviewer = mutate_reviewer
        self.calls = []
        self.verify_calls = 0

    def run(self, role, workspace, prompt, *, timeout, log, read_only=False):
        self.calls.append((role, read_only))
        head = git(workspace, "rev-parse", "HEAD")
        if role == self.fail_stage:
            raise RuntimeError("injected crash")
        if role == "builder":
            (workspace / "answer.txt").write_text("implemented\n", encoding="utf-8")
        if role == "reviewer" and self.mutate_reviewer:
            (workspace / "answer.txt").write_text("tampered\n", encoding="utf-8")
        return {"passed": self.reviewer_pass if role == "reviewer" else True,
                "summary": "looks good", "head": head}

    def verify(self, workspace, commands, *, timeout, log):
        self.verify_calls += 1
        for command in commands:
            subprocess.run(command, cwd=workspace, check=True, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class ForgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Forge Test")
        git(self.repo, "config", "user.email", "forge@example.invalid")
        (self.repo / "README.md").write_text("baseline\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "baseline")
        self.spec = self.root / "spec.md"
        self.spec.write_text("Implement answer file.\n")
        self.native = FakeNative()
        self.runtime = FakeRuntime()
        self.forge = Forge(self.root / "factory-home", self.root / "state",
                           cli=self.native, runtime=self.runtime)
        self.forge.register("demo", self.repo, verify=["python", "-c", "from pathlib import Path; assert Path('answer.txt').exists()"])
        self.ticket = self.forge.ticket("demo", "Implement answer", spec_file=self.spec,
                                        allow_path=["answer.txt"])["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_isolated_register_and_native_card_approval(self):
        self.assertNotEqual(self.forge.home, Path.home())
        self.assertEqual(self.native.cards[self.ticket]["status"], "blocked")
        self.assertFalse(self.forge.status("demo")["tickets"][self.ticket]["approved"])
        self.forge.approve("demo", self.ticket)
        self.assertEqual(self.native.cards[self.ticket]["status"], "ready")
        self.assertTrue(self.forge.status("demo")["tickets"][self.ticket]["approved"])
        self.assertTrue(all(env.get("HERMES_HOME") == str(self.forge.home) for env in self.native.envs))

    def test_success_creates_local_review_artifact_no_source_integration(self):
        marker = self.root / "hook-ran"
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text(f"#!/bin/sh\nprintf ran > {marker}\n")
        hook.chmod(0o755)
        self.forge.approve("demo", self.ticket)
        result = self.forge.run("demo", campaign="pilot", ticket_ids=[self.ticket])
        self.assertEqual(result["tickets"][0]["status"], "review")
        self.assertEqual(self.native.cards[self.ticket]["status"], "review")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertFalse((self.repo / "answer.txt").exists())
        self.assertFalse(marker.exists(), "host-side Git hook must never run")
        self.assertEqual(self.runtime.verify_calls, 3)
        self.assertTrue(self.runtime.calls[-1][1])
        campaign = json.loads((self.root / "state/campaigns/demo/pilot.json").read_text())
        self.assertEqual(campaign["tickets"][self.ticket]["status"], "review")
        self.assertIn("awaiting operator PR/CI", self.forge._worker_prompt("builder", "demo", self.ticket,
                     {"allow_path": ["answer.txt"], "spec": "spec"}, self.root, "head"))

    def test_unapproved_ticket_never_runs_and_pause_is_independent(self):
        with self.assertRaisesRegex(ForgeError, "not approved"):
            self.forge.run("demo", ticket_ids=[self.ticket])
        self.assertEqual(self.runtime.calls, [])
        self.forge.pause("demo")
        with self.assertRaisesRegex(ForgeError, "paused or failed"):
            self.forge.run("demo")
        self.forge.resume("demo")
        self.forge.approve("demo", self.ticket)
        self.assertEqual(self.forge.run("demo", ticket_ids=[self.ticket])["tickets"][0]["status"], "review")

    def test_invalid_evidence_and_scope_drift_fail_closed(self):
        self.forge.approve("demo", self.ticket)
        self.runtime.mutate_reviewer = True
        with self.assertRaisesRegex(ForgeError, "review evidence invalid"):
            self.forge.run("demo", campaign="tamper", ticket_ids=[self.ticket])
        self.assertTrue(self.forge.status("demo")["failed"])
        self.forge.resume("demo")
        # Separate project runtime run produces disallowed untracked file.
        self.runtime = FakeRuntime()
        original = self.runtime.run
        def escape(role, workspace, prompt, **kwargs):
            result = original(role, workspace, prompt, **kwargs)
            if role == "builder":
                (workspace / "outside.txt").write_text("oops")
                result["head"] = git(workspace, "rev-parse", "HEAD")
            return result
        self.runtime.run = escape
        self.forge.runtime = self.runtime
        self.forge.approve("demo", self.ticket)
        with self.assertRaisesRegex(ForgeError, "scope drift"):
            self.forge.run("demo", campaign="scope", ticket_ids=[self.ticket])

    def test_stage_attempts_persist_across_restart_and_resume(self):
        self.forge.approve("demo", self.ticket)
        self.forge.runtime = FakeRuntime(fail_stage="builder")
        with self.assertRaisesRegex(ForgeError, "injected crash"):
            self.forge.run("demo", campaign="retry", ticket_ids=[self.ticket])
        saved = json.loads((self.root / "state/campaigns/demo/retry.json").read_text())
        self.assertEqual(saved["tickets"][self.ticket]["attempts"]["builder"], 1)
        # New Forge object reloads state and consumes the second attempt.
        new_runtime = FakeRuntime(fail_stage="builder")
        restarted = Forge(self.root / "factory-home", self.root / "state", cli=self.native, runtime=new_runtime)
        restarted.resume("demo")
        with self.assertRaisesRegex(ForgeError, "injected crash"):
            restarted.run("demo", campaign="retry", ticket_ids=[self.ticket])
        saved = json.loads((self.root / "state/campaigns/demo/retry.json").read_text())
        self.assertEqual(saved["tickets"][self.ticket]["attempts"]["builder"], 2)
        restarted.resume("demo")
        with self.assertRaisesRegex(ForgeError, "attempt limit"):
            restarted.run("demo", campaign="retry", ticket_ids=[self.ticket])

    def test_campaign_deadline_cap_and_global_lock(self):
        self.forge.approve("demo", self.ticket)
        with self.assertRaisesRegex(ForgeError, "max_tickets"):
            self.forge.run("demo", max_tickets=6)
        with self.assertRaisesRegex(ForgeError, "budget"):
            self.forge.run("demo", budget=3601)
        lock = self.forge._locked(self.root / "state/locks/global-run.lock")
        try:
            with self.assertRaisesRegex(ForgeError, "another Forge run"):
                self.forge.run("demo", campaign="locked", ticket_ids=[self.ticket])
        finally:
            lock.close()
        now = [100.0]
        timed = Forge(self.root / "h2", self.root / "s2", cli=self.native, runtime=self.runtime,
                      clock=lambda: now[0])
        timed.register("demo", self.repo, verify=["true"])
        # Populate corresponding approved ticket registry without using a second native card id.
        timed.ticket("demo", "deadline", spec_file=self.spec, allow_path=["answer.txt"])
        tid = "T2"
        timed.approve("demo", tid)
        timed.run("demo", campaign="expired", budget=1, ticket_ids=[])
        now[0] = 101.0
        with self.assertRaisesRegex(ForgeError, "deadline expired"):
            timed.run("demo", campaign="expired", budget=1, ticket_ids=[tid])

    def test_simplifier_noop_is_valid(self):
        self.forge.approve("demo", self.ticket)
        runtime = FakeRuntime()
        old_run = runtime.run
        def no_op(role, workspace, prompt, **kwargs):
            if role == "simplifier":
                runtime.calls.append((role, kwargs.get("read_only", False)))
                return {"passed": True, "summary": "no simplification", "head": git(workspace, "rev-parse", "HEAD")}
            return old_run(role, workspace, prompt, **kwargs)
        runtime.run = no_op
        self.forge.runtime = runtime
        self.assertEqual(self.forge.run("demo", campaign="noop", ticket_ids=[self.ticket])["tickets"][0]["status"], "review")

    def test_malicious_project_ticket_and_path_names_rejected(self):
        for slug in ("../bad", "Upper", "-bad", "bad/name"):
            with self.assertRaises(ForgeError):
                self.forge.register(slug, self.repo, verify=["true"])
        for patterns in ([], ["../*"], ["/tmp/*"]):
            with self.assertRaises(ForgeError):
                self.forge.ticket("demo", "bad", spec_file=self.spec, allow_path=patterns)
        self.spec.write_text("Depends-on: T1\n")
        with self.assertRaisesRegex(ForgeError, "dependent tickets"):
            self.forge.ticket("demo", "dependent", spec_file=self.spec, allow_path=["*"])

    def test_credential_content_is_rejected_before_host_commit(self):
        self.forge.approve("demo", self.ticket)
        runtime = FakeRuntime()
        run = runtime.run
        def leak(role, workspace, prompt, **kwargs):
            result = run(role, workspace, prompt, **kwargs)
            if role == "builder":
                (workspace / "answer.txt").write_text("password = this_is_a_long_secret_value\\n")
            return result
        runtime.run = leak
        self.forge.runtime = runtime
        with self.assertRaisesRegex(ForgeError, "credential-like value"):
            self.forge.run("demo", campaign="credential", ticket_ids=[self.ticket])
        self.assertEqual(self.native.cards[self.ticket]["status"], "blocked")

    def test_failed_runtime_marks_project_and_recovery_clears_it(self):
        self.forge.approve("demo", self.ticket)
        self.forge.runtime = FakeRuntime(reviewer_pass=False)
        with self.assertRaisesRegex(ForgeError, "review evidence invalid"):
            self.forge.run("demo", campaign="bad-review", ticket_ids=[self.ticket])
        self.assertTrue(self.forge.status("demo")["failed"])
        self.assertFalse(self.forge.resume("demo")["failed"])


if __name__ == "__main__":
    unittest.main()
