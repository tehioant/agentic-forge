# Pilot: a safe local factory readiness report

## Problem
An operator needs to know whether Forge's local configuration is ready without dumping tokens or changing state.

## Approved implementation scope
Only add `forge/doctor.py` and `tests/test_doctor.py`. Do not change existing controller, runtime, installer, CI, dependencies or documentation. Standard library only.

## Acceptance criteria
- Provide `check_configuration(home: pathlib.Path) -> dict` and a runnable `python3 -m forge.doctor --home PATH` CLI.
- Check whether runtime.json exists and has a valid 64-character hex SHA-256-pinned image; malformed/missing JSON must become a failed check, not a traceback.
- Check that builder, simplifier and reviewer config templates exist.
- Check inference snapshot existence and restrictive 0600 permissions by stat ONLY. Never read the secret file contents, print paths within the credential file or attempt provider/network access.
- Report a JSON object with explicit `ready` boolean, named boolean checks and concise actionable failure messages. Exit 0 only if ready; otherwise exit 1.
- Treat empty/missing home and missing/unsafe secrets as not ready. No filesystem writes, chmod, creating directories, shell calls or subprocesses.
- Include meaningful tests for ready config, missing/malformed settings, invalid/nonhex digest, missing each role template, missing credential file, unsafe mode and the CLI failure exit. Tests must prove secret file is not read (e.g. valid permissions with unreadable arbitrary content).
- Full repository tests remain passing.

## Out of scope
No daemon, repair, authentication, network calls, installing packages, changing personal profiles, publishing or merging. Any cleanup is limited to these new files and must preserve behavior.
