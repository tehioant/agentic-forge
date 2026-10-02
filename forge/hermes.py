"""Resolve the installed PM runtime once, without printing redacted paths.

A custom HERMES_HOME must not trigger a fresh PM dependency build for every
role. The documented runtime-command shim selects the existing installation;
the invoked CLI still reads the explicitly supplied isolated home.
"""
import functools
import json
import os
import subprocess
from pathlib import Path

@functools.lru_cache(maxsize=1)
def managed_command() -> list[str]:
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "XDG_RUNTIME_DIR") if k in os.environ}
    env["HOME"] = str(Path.home())
    result = subprocess.run(["hermes", "--print-runtime-command"], env=env,
                            text=True, capture_output=True, check=True, timeout=60)
    command = json.loads(result.stdout)
    if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
        raise RuntimeError("Invalid installed Hermes runtime command")
    return command
