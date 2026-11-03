#!/usr/bin/env python3
"""Recording stand-in for the `hermes` CLI (tests must never run the real one: it ignores a temp
HERMES_HOME for kanban/profile commands). Appends {argv, stdin} to $FAKE_HERMES_LOG and prints a
canned reply: $FAKE_HERMES_REPLY (raw text) if set, else a JSON echo of argv."""
import json
import os
import sys

argv = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["FAKE_HERMES_LOG"], "a") as f:
    f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")
if argv[:1] == ["--version"]:
    print("Hermes Agent v0.0.0-fake")
elif os.environ.get("FAKE_HERMES_FAIL"):
    print(os.environ["FAKE_HERMES_FAIL"], file=sys.stderr)
    sys.exit(2)
elif os.environ.get("FAKE_HERMES_REPLY"):
    print(os.environ["FAKE_HERMES_REPLY"])
else:
    print(json.dumps({"argv": argv, "stdin": stdin}))
