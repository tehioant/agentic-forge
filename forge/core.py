"""Safety-first local ticket orchestration for Agentic Forge.

Persistent state is JSON under state_root; Hermes Kanban remains authoritative for
card lifecycle. No operation integrates a ticket into its registered source repo.
"""
from __future__ import annotations

import fnmatch
import fcntl
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from .hermes import managed_command


class ForgeError(RuntimeError):
    """Expected, user-facing Forge failure."""


_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_MAX_TICKETS, _MAX_BUDGET, _MAX_ATTEMPTS, _MAX_CYCLES = 5, 3600, 2, 2


def _json_load(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as e:
        raise ForgeError(f"invalid state file {path}: {e}") from e


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _safe_slug(value: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ForgeError("project slug must be lowercase alphanumeric with internal hyphens")
    return value


def _run(argv: list[str], *, env: dict[str, str], timeout: float = 30,
         cwd: Path | None = None, input_text: str | None = None) -> str:
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, input=input_text, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ForgeError(f"command failed to execute: {argv[0]}: {e}") from e
    if p.returncode:
        raise ForgeError(f"command failed ({p.returncode}): {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.rstrip("\n")


def _git(workspace: Path, *args: str, timeout: float = 30) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(workspace),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
           "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    return _run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", str(workspace), *args], env=env, timeout=timeout)


class Forge:
    """Controller with isolated factory home/state and injectable test seams."""
    def __init__(self, home: str | Path | None = None, state_root: str | Path | None = None,
                 *, cli: Callable[..., str] | None = None, runtime: Any = None,
                 clock: Callable[[], float] = time.time):
        base = Path.home() / ".local/share/agentic-forge"
        root = Path(state_root or os.environ.get("FORGE_HOME") or base).expanduser().resolve()
        self.forge_root = root
        self.home = Path(home or (root / "hermes")).expanduser().resolve()
        self.state_root = root
        self.projects_path = self.state_root / "projects.json"
        self.home.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.clock, self.runtime = clock, runtime
        self._cli_override = cli

    def _env(self) -> dict[str, str]:
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home),
                "HERMES_HOME": str(self.home), "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

    def _cli(self, *args: str, timeout: float = 30) -> str:
        argv = ["hermes", *args]
        if self._cli_override:
            return self._cli_override(argv, env=self._env(), timeout=timeout)
        return _run(managed_command() + list(args), env=self._env(), timeout=timeout)

    def _show(self, project: str, ticket_id: str) -> dict[str, Any]:
        data = json.loads(self._cli("kanban", "--board", project, "show", ticket_id, "--json"))
        card = data.get("task", data)
        if not isinstance(card, dict) or str(card.get("id")) != ticket_id:
            raise ForgeError("native card readback mismatch")
        return card

    def _projects(self) -> dict[str, Any]:
        val = _json_load(self.projects_path, {})
        if not isinstance(val, dict):
            raise ForgeError("projects registry must be a JSON object")
        return val

    def _project(self, slug: str) -> dict[str, Any]:
        slug = _safe_slug(slug)
        item = self._projects().get(slug)
        if not isinstance(item, dict):
            raise ForgeError(f"unknown project: {slug}")
        return item

    def _campaign_path(self, slug: str, campaign: str) -> Path:
        if not _ID.fullmatch(campaign):
            raise ForgeError("invalid campaign name")
        return self.state_root / "campaigns" / slug / f"{campaign}.json"

    def _locked(self, path: Path, *, nonblocking: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True)
        f = path.open("a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError as e:
            f.close()
            raise ForgeError("another Forge run is already active") from e
        return f

    def register(self, slug: str, repo: str | Path, *, verify: str | list[str], remote: str | None = None) -> dict[str, Any]:
        slug = _safe_slug(slug)
        repo_path = Path(repo).expanduser().resolve()
        if not repo_path.is_dir():
            raise ForgeError("repository must be an existing directory")
        if _git(repo_path, "status", "--porcelain"):
            raise ForgeError("repository checkout must be clean before registration")
        verify_argv = shlex.split(verify) if isinstance(verify, str) else list(verify)
        if not verify_argv or any(not isinstance(a, str) or "\0" in a for a in verify_argv):
            raise ForgeError("verification command must be a non-empty argv")
        if remote is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", remote):
            raise ForgeError("remote must be OWNER/REPO")
        projects = self._projects()
        if slug in projects:
            raise ForgeError(f"project already registered: {slug}")
        lock = self._locked(self.state_root / "locks/registry.lock")
        try:
            projects = self._projects()
            if slug in projects:
                raise ForgeError(f"project already registered: {slug}")
            self._cli("project", "create", slug, str(repo_path), "--slug", slug, "--primary", str(repo_path))
            self._cli("kanban", "boards", "create", slug, "--default-workdir", str(repo_path))
            self._cli("project", "bind-board", slug, slug)
            # Read-after-write checks reject a half-created or mismatched native setup.
            project_view = self._cli("project", "show", slug)
            board_view = self._cli("kanban", "boards", "list", "--json")
            try:
                boards_list = json.loads(board_view)
            except json.JSONDecodeError as e:
                raise ForgeError("native project/board listing is not valid JSON") from e
            fields = dict(line.strip().split(":", 1) for line in project_view.splitlines() if ":" in line)
            if (fields.get("primary", "").strip() != str(repo_path)
                    or fields.get("board", "").strip() != slug
                    or not isinstance(boards_list, list)
                    or not any(isinstance(x, dict) and x.get("slug") == slug and x.get("default_workdir") == str(repo_path) for x in boards_list)):
                raise ForgeError(f"native project/board verification failed for {slug}")
            record = {"slug": slug, "repo": str(repo_path), "verify": verify_argv,
                      "remote": remote, "baseline": _git(repo_path, "rev-parse", "HEAD")}
            projects[slug] = record
            _atomic_json(self.projects_path, projects)
            return record
        finally:
            lock.close()

    def ticket(self, project: str, title: str, *, spec_file: str | Path,
               allow_path: list[str] | tuple[str, ...]) -> dict[str, Any]:
        p = self._project(project)
        if not title.strip() or "\0" in title:
            raise ForgeError("ticket title is required")
        spec = Path(spec_file).expanduser().resolve()
        if not spec.is_file():
            raise ForgeError("spec file does not exist")
        globs = list(allow_path)
        if not globs or any(not g or g.startswith("/") or ".." in Path(g).parts or "\0" in g for g in globs):
            raise ForgeError("one or more safe --allow-path globs are required")
        spec_text = spec.read_text(encoding="utf-8")
        if re.search(r"(?im)^\s*(?:depends(?:[- ]on)?|after ticket|blocked by)\b(?:\s*[:#]|\s+)", spec_text):
            raise ForgeError("dependent tickets are unsupported; manually integrate and register a new baseline")
        result = self._cli("kanban", "--board", project, "create", title, "--body-file", str(spec),
                           "--workspace", f"dir:{p['repo']}", "--initial-status", "blocked",
                           "--assignee", "forge-external", "--completion-contract", "local-only",
                           "--max-runtime", "15m", "--max-retries", "2", "--json")
        try:
            card = json.loads(result)
            tid = str(card.get("id", ""))
        except (json.JSONDecodeError, AttributeError) as e:
            raise ForgeError("native Kanban did not return a JSON card") from e
        if not _ID.fullmatch(tid):
            raise ForgeError("native Kanban returned an invalid card id")
        # Verify the card is still blocked and carries the requested local-only contract.
        try:
            persisted = self._show(project, tid)
        except json.JSONDecodeError as e:
            raise ForgeError("could not verify created native card") from e
        if str(persisted.get("id")) != tid or persisted.get("status") != "blocked":
            raise ForgeError("created card failed blocked-state verification")
        if persisted.get("completion_contract") != "local-only" or persisted.get("assignee") != "forge-external":
            raise ForgeError("created card is not isolated in the forge-external local-only lane")
        registry = self._projects()
        registry[project].setdefault("tickets", {})[tid] = {
            "title": title, "spec": spec_text, "allow_path": globs, "approved": False,
            "status": "blocked", "created": self.clock()}
        _atomic_json(self.projects_path, registry)
        return {"id": tid, "status": "blocked", "approved": False}

    def approve(self, project: str, ticket_id: str) -> dict[str, Any]:
        p = self._project(project)
        tickets = p.get("tickets", {})
        if ticket_id not in tickets:
            raise ForgeError(f"unknown ticket: {ticket_id}")
        self._cli("kanban", "--board", project, "unblock", ticket_id)
        readback = self._show(project, ticket_id)
        if str(readback.get("id")) != ticket_id or readback.get("status") != "ready":
            raise ForgeError("native card did not enter the ready state")
        registry = self._projects()
        registry[project]["tickets"][ticket_id].update(approved=True, status="approved")
        _atomic_json(self.projects_path, registry)
        return {"id": ticket_id, "approved": True, "status": "approved"}

    def pause(self, project: str) -> dict[str, Any]:
        self._project(project)
        path = self.state_root / "projects" / project / "state.json"
        data = _json_load(path, {"paused": False, "failed": False})
        data["paused"] = True
        _atomic_json(path, data)
        return {"project": project, "paused": True}

    def resume(self, project: str) -> dict[str, Any]:
        self._project(project)
        path = self.state_root / "projects" / project / "state.json"
        data = _json_load(path, {"paused": False, "failed": False})
        data["paused"] = False
        # Explicit resume is the operator-controlled recovery path after a failed run.
        data["failed"] = False
        _atomic_json(path, data)
        return {"project": project, "paused": False, "failed": False}

    def status(self, project: str) -> dict[str, Any]:
        p = self._project(project)
        local = _json_load(self.state_root / "projects" / project / "state.json", {"paused": False, "failed": False})
        try:
            native = json.loads(self._cli("kanban", "--board", project, "list", "--json"))
        except (json.JSONDecodeError, ForgeError):
            native = None
        return {"project": project, "paused": local.get("paused", False), "failed": local.get("failed", False),
                "tickets": p.get("tickets", {}), "native_cards": native}

    def _get_runtime(self):
        if self.runtime is None:
            try:
                from .runtime import DockerRuntime
            except ImportError as e:
                raise ForgeError("Forge runtime is unavailable; install the Docker runtime module") from e
            self.runtime = DockerRuntime(self.forge_root)
        return self.runtime

    def _allowed(self, changed: list[str], globs: list[str]) -> bool:
        return all(any(fnmatch.fnmatchcase(name, pattern) for pattern in globs) for name in changed)

    def _changed(self, workspace: Path) -> list[str]:
        out = _git(workspace, "status", "--porcelain", "-z", "--untracked-files=all")
        # With text mode NULs remain; parse porcelain's two-byte status and path.
        parts = out.split("\0")
        names = []
        for part in parts:
            if len(part) >= 4:
                name = part[3:]
                if " -> " in name:
                    name = name.rsplit(" -> ", 1)[1]
                names.append(name)
        return names

    def _check_credentials(self, workspace: Path, changed: list[str]) -> None:
        secret_name = re.compile(r"(?i)(?:^|/)(?:[.]env(?:[.].*)?|.*(?:secret|credential|private[-_]?key|token).*)$")
        secret_value = re.compile(
            r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
            r"(?:^|[^A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})(?:$|[^A-Za-z0-9_])|"
            r"(?:password|passwd|secret|api[_-]?key|access[_-]?token)[ \t]*[:=][ \t]*[A-Za-z0-9/+_=-]{16,}"
        )
        for name in changed:
            if secret_name.search(name):
                raise ForgeError(f"credential-like file is not allowed: {name}")
            path = workspace / name
            if not path.resolve().is_relative_to(workspace.resolve()):
                raise ForgeError(f"changed path escapes workspace: {name}")
            if path.is_symlink():
                raise ForgeError(f"symbolic links in changed files are not allowed: {name}")
            if not path.is_file():
                continue
            try:
                raw = path.read_bytes()
            except OSError as e:
                raise ForgeError(f"could not inspect changed file: {name}") from e
            if len(raw) > 2 * 1024 * 1024:
                raise ForgeError(f"changed file too large to inspect safely: {name}")
            if bytes([0]) not in raw and secret_value.search(raw.decode("utf-8", errors="replace")):
                raise ForgeError(f"credential-like value detected in changed file: {name}")

    def _head(self, workspace: Path) -> str:
        return _git(workspace, "rev-parse", "HEAD")

    def run(self, project: str, *, campaign: str = "default", max_tickets: int = 5,
            budget: int = 3600, ticket_ids: list[str] | None = None) -> dict[str, Any]:
        project = _safe_slug(project)
        if not 1 <= max_tickets <= _MAX_TICKETS or not 1 <= budget <= _MAX_BUDGET:
            raise ForgeError("max_tickets must be <=5 and budget must be <=3600 seconds")
        self._project(project)
        global_lock = self._locked(self.state_root / "locks/global-run.lock", nonblocking=True)
        project_lock = self._locked(self.state_root / "locks" / f"{project}.lock", nonblocking=True)
        try:
            local_path = self.state_root / "projects" / project / "state.json"
            local = _json_load(local_path, {"paused": False, "failed": False})
            if local.get("paused") or local.get("failed"):
                raise ForgeError("project is paused or failed; resume/repair before running")
            path = self._campaign_path(project, campaign)
            state = _json_load(path, None)
            if state is None:
                state = {"project": project, "campaign": campaign, "started": self.clock(),
                         "deadline": self.clock() + budget, "max_tickets": max_tickets, "ticket_ids": [], "tickets": {}}
                _atomic_json(path, state)
            elif state.get("project") != project or state.get("campaign") != campaign:
                raise ForgeError("campaign state identity mismatch")
            if budget > _MAX_BUDGET:
                raise ForgeError("campaign budget exceeds maximum")
            registry = self._projects()
            known = registry[project].get("tickets", {})
            selected = ticket_ids if ticket_ids is not None else [i for i, t in known.items() if t.get("approved")]
            if len(selected) > max_tickets or len(set(selected)) != len(selected):
                raise ForgeError("selected tickets exceed cap or contain duplicates")
            if len(set(state["ticket_ids"]) | set(selected)) > min(max_tickets, state.get("max_tickets", max_tickets)):
                raise ForgeError("campaign ticket cap already reached")
            results = []
            for tid in selected:
                if self.clock() >= state["deadline"]:
                    raise ForgeError("campaign deadline expired")
                ticket = known.get(tid)
                if not ticket or not ticket.get("approved"):
                    raise ForgeError(f"ticket is not approved: {tid}")
                if tid not in state["tickets"]:
                    state["ticket_ids"].append(tid)
                    state["tickets"][tid] = {"status": "pending", "attempts": {"builder": 0, "simplifier": 0, "reviewer": 0}, "cycles": 0}
                    _atomic_json(path, state)
                ts = state["tickets"][tid]
                if ts.get("status") == "review":
                    results.append({"id": tid, "status": "review"}); continue
                # Native Kanban owns card lifecycle; transition and verify before workers start.
                running = self._show(project, tid)
                if running.get("status") == "blocked" and ts.get("status") == "failed":
                    self._cli("kanban", "--board", project, "unblock", tid)
                    running = self._show(project, tid)
                if running.get("status") == "ready":
                    self._cli("kanban", "--board", project, "claim", tid, "--ttl", "3600")
                    running = self._show(project, tid)
                if running.get("status") != "running":
                    raise ForgeError("native card did not enter running state")
                item = self._execute_ticket(project, tid, campaign, registry[project], ticket, ts, path, state)
                results.append(item)
            return {"project": project, "campaign": campaign, "tickets": results,
                    "deadline": state["deadline"]}
        finally:
            project_lock.close()
            global_lock.close()

    def _execute_ticket(self, project: str, tid: str, campaign: str, proj: dict[str, Any], ticket: dict[str, Any],
                        ts: dict[str, Any], campaign_path: Path, state: dict[str, Any]) -> dict[str, Any]:
        workspace = self.state_root / "workspaces" / project / campaign / tid
        if not workspace.exists():
            workspace.parent.mkdir(parents=True, exist_ok=True)
            _run(["git", "clone", "--no-hardlinks", "--", proj["repo"], str(workspace)], env=self._env(), timeout=120)
            _git(workspace, "switch", "-c", f"forge/{project}/{tid}")
            ts["baseline"] = _git(workspace, "rev-parse", "HEAD")
            if ts["baseline"] != proj["baseline"]:
                raise ForgeError("registered repository HEAD changed; register a new baseline before running")
            ts["last_head"] = ts["baseline"]
            _atomic_json(campaign_path, state)
        baseline = ts["baseline"]
        if self._head(workspace) != ts.get("last_head", baseline):
            raise ForgeError("workspace HEAD drifted from persisted campaign state")
        try:
            runtime = self._get_runtime()
            for stage in ("builder", "simplifier"):
                if stage in ts.get("completed_stages", []):
                    continue
                current_project_state = _json_load(self.state_root / "projects" / project / "state.json", {})
                if current_project_state.get("paused"):
                    raise ForgeError("project paused between stages")
                if ts["status"] == "review":
                    break
                if ts["attempts"][stage] >= _MAX_ATTEMPTS:
                    raise ForgeError(f"{stage} attempt limit reached")
                ts["attempts"][stage] += 1  # Interrupted work still consumes an attempt.
                _atomic_json(campaign_path, state)
                prompt = self._worker_prompt(stage, project, tid, ticket, workspace, baseline)
                remaining = int(state["deadline"] - self.clock())
                if remaining <= 0:
                    raise ForgeError("campaign deadline expired")
                response = runtime.run(stage, workspace, prompt, timeout=min(900, remaining),
                                       log=self.state_root / "logs" / project / campaign / f"{tid}-{stage}-{ts['attempts'][stage]}.log")
                if not isinstance(response, dict) or response.get("passed") is not True:
                    raise ForgeError(f"{stage} did not return passing evidence")
                if response.get("head") != self._head(workspace):
                    raise ForgeError(f"{stage} evidence does not bind current HEAD")
                changed = self._changed(workspace)
                if not self._allowed(changed, ticket["allow_path"]):
                    raise ForgeError("workspace scope drift outside allowed paths")
                self._check_credentials(workspace, changed)
                remaining = int(state["deadline"] - self.clock())
                if remaining <= 0:
                    raise ForgeError("campaign deadline expired")
                runtime.verify(workspace, [proj["verify"]], timeout=min(600, remaining),
                               log=self.state_root / "logs" / project / campaign / f"{tid}-{stage}-verify-{ts['attempts'][stage]}.log")
                if self._changed(workspace) != changed:
                    raise ForgeError("verification changed the proposed file set")
                if stage == "builder" and changed:
                    _git(workspace, "add", "--", *changed)
                    _git(workspace, "-c", "core.hooksPath=/dev/null", "-c", "user.name=Agentic Forge", "-c", "user.email=forge@localhost", "commit", "--no-gpg-sign", "-m", f"forge: {tid}")
                elif stage == "simplifier" and changed:
                    _git(workspace, "add", "--", *changed)
                    _git(workspace, "-c", "core.hooksPath=/dev/null", "-c", "user.name=Agentic Forge", "-c", "user.email=forge@localhost", "commit", "--no-gpg-sign", "-m", f"forge: simplify {tid}")
                ts["last_head"] = self._head(workspace)
                ts.setdefault("completed_stages", []).append(stage)
                _atomic_json(campaign_path, state)
            # Reviewer examines the exact post-verify tree read-only.
            if ts["attempts"]["reviewer"] >= _MAX_ATTEMPTS:
                raise ForgeError("reviewer attempt limit reached")
            before_head, before_changes = self._head(workspace), self._changed(workspace)
            ts["attempts"]["reviewer"] += 1
            _atomic_json(campaign_path, state)
            remaining = int(state["deadline"] - self.clock())
            if remaining <= 0:
                raise ForgeError("campaign deadline expired")
            response = runtime.run("reviewer", workspace,
                                   self._worker_prompt("reviewer", project, tid, ticket, workspace, baseline),
                                   timeout=min(900, remaining), log=self.state_root / "logs" / project / campaign / f"{tid}-reviewer-{ts['attempts']['reviewer']}.log", read_only=True)
            if isinstance(response, dict) and response.get("passed") is False:
                ts["cycles"] = ts.get("cycles", 0) + 1
                _atomic_json(campaign_path, state)
                if ts["cycles"] > _MAX_CYCLES:
                    raise ForgeError("review-fix cycle limit reached")
            if (not isinstance(response, dict) or response.get("passed") is not True
                    or not isinstance(response.get("summary"), str) or not response["summary"].strip()
                    or response.get("head") != before_head or self._head(workspace) != before_head
                    or self._changed(workspace) != before_changes):
                raise ForgeError("review evidence invalid, missing, or workspace mutated")
            remaining = int(state["deadline"] - self.clock())
            if remaining <= 0:
                raise ForgeError("campaign deadline expired")
            runtime.verify(workspace, [proj["verify"]], timeout=min(600, remaining),
                           log=self.state_root / "logs" / project / f"{tid}-final-verify.log")
            if self._head(workspace) != before_head or self._changed(workspace) != before_changes:
                raise ForgeError("verification mutated reviewed workspace")
            ts["status"] = "review"
            ts["review"] = {"passed": True, "summary": str(response.get("summary", "")), "head": before_head}
            _atomic_json(campaign_path, state)
            # Native lifecycle remains in review; never mark done or integrate.
            self._cli("kanban", "--board", project, "request-review", tid, "--force", "--summary", response["summary"], "--metadata", json.dumps({"reviewed_head": before_head, "workspace": str(workspace)}))
            reviewed = self._show(project, tid)
            if str(reviewed.get("id")) != tid or reviewed.get("status") != "review":
                raise ForgeError("native card did not enter review state")
            registry = self._projects()
            registry[project]["tickets"][tid]["status"] = "review"
            _atomic_json(self.projects_path, registry)
            _atomic_json(campaign_path, state)
            return {"id": tid, "status": "review", "head": before_head}
        except Exception as e:
            ts["status"] = "failed"
            ts["error"] = str(e)
            local_path = self.state_root / "projects" / project / "state.json"
            local = _json_load(local_path, {"paused": False, "failed": False})
            local["failed"] = True
            _atomic_json(local_path, local)
            try:
                self._cli("kanban", "--board", project, "block", tid, str(e), "--kind", "needs_input")
            except Exception as native_error:
                ts["native_block_error"] = str(native_error)
            _atomic_json(campaign_path, state)
            if isinstance(e, ForgeError):
                raise
            raise ForgeError(f"ticket execution failed: {e}") from e

    @staticmethod
    def _worker_prompt(stage: str, project: str, tid: str, ticket: dict[str, Any],
                       workspace: Path, baseline: str) -> str:
        return (f"Role: {stage}. Project: {project}; ticket: {tid}.\n"
                f"Workspace inside container: /workspace\nBaseline HEAD: {baseline}\n"
                f"Allowed paths: {json.dumps(ticket['allow_path'])}\n"
                f"Specification:\n{ticket['spec']}\n"
                "Do not access credentials, network, or the source repository. Do not edit .git or commit. "
                "Return explicit JSON {\"passed\":true|false,\"summary\":\"...\",\"head\":\"exact git HEAD\"}. "
                "Builder/simplifier make local changes only; reviewer is read-only. Passing artifacts remain local, "
                "awaiting operator PR/CI and human merge; never claim integration or deployment.")
