"""Whole-agent Docker confinement. No personal home, Docker socket or GitHub grant.

The trusted controller owns Git, budgets and board mutations. Worker output is
advice; deterministic checks and independent review remain mandatory.
"""
from __future__ import annotations

import json
import os
import re
import selectors
from pathlib import Path
import subprocess
import time
import uuid
from .broker import Broker

ROOT = Path(__file__).resolve().parents[1]
ROLES = {"builder": "forge-implement", "simplifier": "forge-simplify", "reviewer": "forge-review"}


def parse_verdict(output: str) -> dict:
    results = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            results.append(event)
    if len(results) != 1 or results[0].get("exit_code") != 0 or results[0].get("error"):
        raise RuntimeError("Worker did not produce exactly one successful terminal result")
    text = results[0].get("text", "").strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    verdict = json.loads(text)
    if not isinstance(verdict, dict) or type(verdict.get("passed")) is not bool:
        raise RuntimeError("Worker verdict must contain an explicit boolean passed")
    if not isinstance(verdict.get("summary"), str) or not verdict["summary"].strip():
        raise RuntimeError("Worker verdict requires a nonempty summary")
    head = verdict.get("head", "")
    if not isinstance(head, str) or len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise RuntimeError("Worker verdict requires an exact 40-character Git HEAD")
    return verdict


def subprocess_env() -> dict[str, str]:
    """Do not forward provider, platform, vault, Git, hook or proxy secrets."""
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "XDG_RUNTIME_DIR") if k in os.environ}
    env["HOME"] = str(Path.home())
    return env


class DockerRuntime:
    def __init__(self, state_root: Path):
        self.root = Path(state_root).resolve()
        self.settings = json.loads((self.root / "runtime.json").read_text())
        self.image = self.settings["image"]
        if not re.fullmatch(r"nousresearch/hermes-agent@sha256:[0-9a-f]{64}",self.image):
            raise ValueError("Runtime image must be pinned by digest")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def command(self, workspace: Path, *, home: Path | None = None,
                read_only: bool = False, inference: bool = False, name: str) -> list[str]:
        workspace = Path(workspace).resolve(strict=True)
        if not (workspace / ".git").is_dir() or (workspace / ".git").is_symlink():
            raise ValueError("A standalone Git clone is required; external worktree metadata is forbidden")
        args = ["docker", "run", "--rm", "--init", "--name", name,
                "--user", f"{os.getuid()}:{os.getgid()}", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "256", "--memory", "4g", "--cpus", "2",
                "--network", "none",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
                "--tmpfs", f"/scratch:rw,nosuid,nodev,size=512m,uid={os.getuid()},gid={os.getgid()}",
                "--workdir", "/workspace",
                "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "TMPDIR=/scratch",
                "--env", "HERMES_HOME=/opt/data", "--env", "HOME=/opt/data",
                "--env", "HERMES_WRITE_SAFE_ROOT=/workspace",
                "--env", "GIT_CONFIG_NOSYSTEM=1", "--env", "GIT_CONFIG_GLOBAL=/dev/null",
                "--env", "GIT_TERMINAL_PROMPT=0",
                "--mount", f"type=bind,src={workspace},dst=/workspace{',readonly' if read_only else ''}",
                "--mount", f"type=bind,src={workspace / '.git'},dst=/workspace/.git,readonly"]
        if home:
            args += ["--mount", f"type=bind,src={home},dst=/opt/data",
                     "--mount", f"type=bind,src={home / 'config.yaml'},dst=/opt/data/config.yaml,readonly"]
        else:
            args += ["--tmpfs", f"/opt/data:rw,nosuid,nodev,size=128m,uid={os.getuid()},gid={os.getgid()}"]
        if inference:
            if home is None:
                raise ValueError("Inference requires a fresh worker home")
            auth = home / "auth.json"
            if auth.stat().st_mode & 0o077:
                raise RuntimeError("Attempt capability file must be mode 0600")
            args += ["--mount", f"type=bind,src={auth},dst=/opt/data/auth.json,readonly",
                     "--mount", f"type=bind,src={ROOT / 'skills'},dst=/forge-skills,readonly"]
            for skill in ("to-spec", "to-tickets", "code-review"):
                path = self.settings.get("uploaded_skills", {}).get(skill)
                if path:
                    args += ["--mount", f"type=bind,src={Path(path).resolve()},dst=/uploaded-skills/{skill},readonly"]
        return args

    def execute(self, args: list[str], timeout: float, log: Path, *, name: str) -> str:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(log, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        env=subprocess_env(), stdin=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + timeout
                    size = 0
                    with selectors.DefaultSelector() as selector:
                        selector.register(proc.stdout, selectors.EVENT_READ)
                        finished = False
                        while not finished:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError("Isolated command exceeded its wall-clock budget")
                            for key, _ in selector.select(timeout=min(1, remaining)):
                                chunk = os.read(key.fd, 65536)
                                if not chunk:
                                    finished = True
                                    break
                                if size + len(chunk) > 16 * 1024 * 1024:
                                    raise RuntimeError("Worker log exceeded 16 MiB")
                                stream.write(chunk)
                                size += len(chunk)
                    code = proc.wait(timeout=max(0.1, deadline-time.monotonic()))
                except BaseException:
                    # Killing the Docker client does NOT kill a running container.
                    subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30, env=subprocess_env())
                    proc.kill()
                    proc.wait(timeout=10)
                    raise
                finally:
                    proc.stdout.close()
            if code:
                raise RuntimeError(f"Isolated command exited {code}; evidence: {log}")
            if log.stat().st_size > 16 * 1024 * 1024:
                raise RuntimeError(f"Worker log exceeded 16 MiB; inspect evidence: {log}")
            return log.read_text(errors="replace")
        finally:
            log.chmod(0o600)

    def run(self, role: str, workspace: Path, prompt: str, timeout: float,
            log: Path, read_only: bool = False) -> dict:
        if role not in ROLES:
            raise ValueError("Unknown worker role")
        # A fresh home means prior conversation and mutable role memory cannot leak.
        run_id = uuid.uuid4().hex
        home = self.root / "workers" / run_id
        home.mkdir(parents=True, mode=0o700)
        config = self.root / "templates" / role / "config.yaml"
        (home / "config.yaml").write_bytes(config.read_bytes())
        (home / "config.yaml").chmod(0o600)
        (home / "auth.json").touch(mode=0o600)
        (home / "query.txt").write_text(prompt + '\n\nDo not commit or alter Git metadata. End with ONLY valid JSON: '
                                      '{"passed":true or false,"summary":"verified facts and blockers",'
                                      '"head":"exact git rev-parse HEAD"}. A blocker means passed=false.\n')
        name = f"forge-{role}-{run_id[:12]}"
        with Broker(self.root / "secrets" / "codex-access.json", home / "inference.sock", timeout) as broker:
            (home / "auth.json").write_text(json.dumps(broker.worker_auth()))
            (home / "auth.json").chmod(0o600)
            args = self.command(workspace, home=home, inference=True,
                                read_only=(read_only or role == "reviewer"), name=name)
            args += ["--env", f"HERMES_CODEX_BASE_URL={broker.url}",
                     "--mount", f"type=bind,src={ROOT / 'scripts/inference_relay.py'},dst=/forge-relay.py,readonly",
                     "--entrypoint", "python3", self.image,
                     "/forge-relay.py", "--socket", "/opt/data/inference.sock", "--", "/opt/hermes/bin/hermes",
                     "chat", "--oneshot", "--query-file", "/opt/data/query.txt",
                     "--format", "stream-json", "--max-turns", "60", "--run-budget", str(max(1, int(timeout) - 20)),
                     "--skills", ROLES[role], "--toolsets", "terminal,file,skills"]
            try:
                return parse_verdict(self.execute(args, timeout, log, name=name))
            finally:
                # Capability expires with this broker; provider grant was never mounted.
                (home / "auth.json").write_text("{}\n")
                (home / "query.txt").chmod(0o600)

    def verify(self, workspace: Path, commands: list[list[str]], timeout: float, log: Path) -> None:
        if not commands:
            raise ValueError("At least one verification command is required")
        deadline = time.monotonic() + timeout
        for index, command in enumerate(commands):
            if not command or any(not isinstance(x, str) or not x or "\x00" in x for x in command):
                raise ValueError("Verification requires explicit nonempty argv")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Verification exhausted its budget")
            name = f"forge-check-{uuid.uuid4().hex[:12]}"
            args = self.command(workspace, name=name)
            args += ["--entrypoint", command[0], self.image, *command[1:]]
            evidence = Path(str(log) + f".{index}")
            self.execute(args, remaining, evidence, name=name)
