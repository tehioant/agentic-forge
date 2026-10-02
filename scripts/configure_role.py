"""Apply a new worker home's non-secret settings via the shipped Hermes CLI."""
import json
import subprocess
import sys
for key, value in json.loads(sys.argv[1]).items():
    subprocess.run(["/opt/hermes/bin/hermes", "config", "set", key, value],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
