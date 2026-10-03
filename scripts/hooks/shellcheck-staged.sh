#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

status=0
while IFS= read -r -d '' path; do
  case "$path" in
    *.sh | *.bash)
      printf 'ShellCheck: %s\n' "$path"
      if ! git show ":$path" | shellcheck -; then
        status=1
      fi
      ;;
  esac
done < <(git diff --cached --name-only --diff-filter=ACMR -z)

exit "$status"
