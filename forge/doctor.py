"""Read-only local readiness checks; inference credentials are inspected by stat only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat


def check_configuration(home: Path) -> dict:
    """Report configuration readiness without reading credentials or changing state."""
    checks = {}
    failures = {}

    def record(name: str, passed: bool, message: str) -> None:
        checks[name] = passed
        if not passed:
            failures[name] = message

    def regular_file(path: Path) -> bool:
        try:
            return stat.S_ISREG(path.stat().st_mode)
        except (OSError, ValueError):
            return False

    try:
        home_exists = isinstance(home, Path) and home.is_dir()
    except (OSError, ValueError):
        home_exists = False
    record("home", home_exists, "Supply an existing Forge home directory with --home.")

    runtime_exists = home_exists and regular_file(home / "runtime.json")
    record("runtime", runtime_exists, "Provide a readable runtime.json file in the Forge home.")
    settings = None
    if runtime_exists:
        try:
            settings = json.loads((home / "runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            pass
    valid_settings = isinstance(settings, dict)
    record("settings", valid_settings, "Provide a valid JSON object in runtime.json.")
    image = settings.get("image") if valid_settings else None
    valid_image = isinstance(image, str) and re.fullmatch(
        r"nousresearch/hermes-agent@sha256:[0-9a-f]{64}", image
    ) is not None
    record("image", valid_image,
           "Set runtime.json image to nousresearch/hermes-agent@sha256: followed by 64 lowercase hex characters.")

    for role in ("builder", "simplifier", "reviewer"):
        exists = home_exists and regular_file(home / "templates" / role / "config.yaml")
        record(f"{role}_template", exists, f"Provide templates/{role}/config.yaml in the Forge home.")

    credential_stat = None
    if home_exists:
        try:
            credential_stat = (home / "secrets" / "codex-access.json").stat(follow_symlinks=False)
        except (OSError, ValueError):
            pass
    credential_exists = credential_stat is not None and stat.S_ISREG(credential_stat.st_mode)
    record("inference_snapshot", credential_exists,
           "Provide a regular, non-symlink secrets/codex-access.json inference snapshot.")
    safe_mode = credential_exists and stat.S_IMODE(credential_stat.st_mode) == 0o600
    record("inference_permissions", safe_mode, "Restrict the inference snapshot permissions to exactly 0600.")
    return {"ready": all(checks.values()), "checks": checks, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="Forge state directory to inspect (required for readiness)")
    args = parser.parse_args()
    home = Path(args.home) if args.home and args.home.strip() else None
    report = check_configuration(home)
    print(json.dumps(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
