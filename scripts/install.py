#!/usr/bin/env python3
"""Install only Forge-owned state. No daemon or personal-profile edits.

Use --refresh-access before a batch if the inference snapshot expires. It copies
one current Codex access grant, never any refresh token or other account secrets.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.hermes import managed_command

def call(home: Path, *args: str):
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
    env.update(HOME=str(Path.home()), HERMES_HOME=str(home))
    subprocess.run(managed_command() + list(args), env=env, check=True, stdout=subprocess.DEVNULL, timeout=60)

def snapshot(source: Path, target: Path):
    auth = json.loads(source.read_text())
    entries = auth.get("credential_pool", {}).get("openai-codex", [])
    singleton = auth.get("providers", {}).get("openai-codex", {})
    tokens = singleton.get("tokens", {})
    if tokens.get("access_token"):
        entries = [{**tokens, "base_url": singleton.get("base_url", "https://chatgpt.com/backend-api/codex")}, *entries]
    selected = None
    expiry = None
    for entry in entries:
        token = entry.get("access_token", "")
        base = entry.get("base_url", "https://chatgpt.com/backend-api/codex")
        if base != "https://chatgpt.com/backend-api/codex" or entry.get("last_status") == "exhausted":
            continue
        try:
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if claims.get("exp", 0) > time.time() + 3700:
                selected = {"id": "forge-access", "label": "Forge access-only snapshot", "source": "manual",
                            "auth_type": "oauth", "priority": 0, "access_token": token,
                            "base_url": base, "request_count": 0}
                expiry = claims["exp"]
                break
        except (ValueError, KeyError, IndexError):
            continue
    if selected is None:
        raise RuntimeError("No healthy current Codex access grant with at least one hour remaining. Reauthenticate securely, then retry.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    data = {"version": 1, "active_provider": "openai-codex", "providers": {},
            "credential_pool": {"openai-codex": [selected]}}
    temporary = target.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    temporary.chmod(0o600)
    temporary.replace(target)
    print(json.dumps({"inference_snapshot": str(target), "expires_at": expiry,
                      "refresh_token_copied": False, "other_credentials_copied": False}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home()/".local/share/agentic-forge")
    parser.add_argument("--image", default="nousresearch/hermes-agent:latest")
    parser.add_argument("--source-auth", type=Path, default=Path.home()/".hermes/auth.json")
    parser.add_argument("--refresh-access", action="store_true")
    args = parser.parse_args()
    home = args.home.resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    if args.refresh_access:
        snapshot(args.source_auth, home/"secrets/codex-access.json")
        return
    image = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{json .RepoDigests}}"], text=True)
    digests = json.loads(image)
    if not digests:
        raise RuntimeError("Image has no verified repository digest")
    pin = next(x for x in digests if x.startswith("nousresearch/hermes-agent@sha256:"))
    runtime = {"image": pin, "uploaded_skills": {name: str(Path.home()/".agents/skills"/name)
               for name in ("to-spec", "to-tickets", "code-review") if (Path.home()/".agents/skills"/name/"SKILL.md").is_file()}}
    (home/"runtime.json").write_text(json.dumps(runtime, indent=2)+"\n")
    control = home/"hermes"
    call(control, "config", "set", "kanban.dispatch_in_gateway", "false")
    # Only the reviewed planning skill subtrees are exposed to the factory controller.
    selected_root = home/"planning-skills"
    selected_root.mkdir(exist_ok=True, mode=0o700)
    for name, path in runtime["uploaded_skills"].items():
        link = selected_root/name
        if not link.exists():
            link.symlink_to(path, target_is_directory=True)
    call(control, "config", "set", "skills.external_dirs", json.dumps([str(selected_root), str(ROOT/"skills")]))
    for role in ("builder", "simplifier", "reviewer"):
        profile = home/"templates"/role
        settings = {
            "model.default": "gpt-6.1-sol", "model.provider": "openai-codex",
            "model.base_url": "https://chatgpt.com/backend-api/codex",
            "model.api_mode": "codex_responses", "terminal.backend": "local", "terminal.cwd": "/workspace",
            "memory.memory_enabled": "false", "memory.user_profile_enabled": "false",
            "plugins.enabled": "[]", "curator.enabled": "false",
            "skills.external_dirs": '["/forge-skills","/uploaded-skills"]',
            "security.redact_secrets": "true", "approvals.mode": "smart",
            "approvals.single_query_mode": "deny", "approvals.unattended_mode": "deny",
            "agent.max_turns": "60", "agent.reasoning_effort": "medium",
            "agent.auto_recovery_cycles": "0", "compression.enabled": "true",
        }
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(["docker", "run", "--rm", "--init", "--network", "none",
                        "--user", f"{os.getuid()}:{os.getgid()}", "--read-only",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                        "--tmpfs", "/tmp:mode=1777", "--env", "HERMES_HOME=/opt/data",
                        "--env", "HOME=/opt/data",
                        "--mount", f"type=bind,src={profile},dst=/opt/data",
                        "--mount", f"type=bind,src={ROOT/'scripts/configure_role.py'},dst=/setup.py,readonly",
                        "--entrypoint", "python3", pin, "/setup.py", json.dumps(settings)],
                       check=True, timeout=120)
    snapshot(args.source_auth, home/"secrets/codex-access.json")
    print(json.dumps({"factory_home": str(home), "image": pin, "roles": ["builder", "simplifier", "reviewer"],
                      "personal_profile_modified": False}))

if __name__ == "__main__":
    main()
