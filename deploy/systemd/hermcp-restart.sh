#!/bin/sh
# hermcp-restart: restart hermcp.service after a Hermes update, and only then.
# Started by hermcp-restart.service (oneshot), which hermcp-restart.path triggers when Hermes
# atomically replaces /usr/local/lib/hermes-agent/install-stamp.json.
#
# Restarts only when ALL of these hold:
#   - hermcp.service is active,
#   - the stamp is a real build record (no "adoptedAt": boot adoption rewrites the stamp but
#     changes no code),
#   - the stamp's builtAt is newer than hermcp's ActiveEnterTimestamp (hermcp already runs the
#     current build otherwise, e.g. after a boot-time rewrite).
# Touches hermcp.service only: never the gateway, never the dashboard.
# Test overrides: HERMCP_STAMP, HERMCP_SYSTEMCTL, HERMCP_PYTHON.
set -eu

STAMP="${HERMCP_STAMP:-/usr/local/lib/hermes-agent/install-stamp.json}"
SYSTEMCTL="${HERMCP_SYSTEMCTL:-/usr/bin/systemctl}"
PY="${HERMCP_PYTHON:-/usr/bin/python3}"
UNIT=hermcp.service

if [ "$("$SYSTEMCTL" is-active "$UNIT" || true)" != "active" ]; then
    echo "hermcp-restart: $UNIT is not active, nothing to do"
    exit 0
fi

# Prints "<builtAt epoch>" for a build stamp, or "adopted" for a boot-time adoption record.
verdict=$("$PY" - "$STAMP" <<'PYEOF'
import datetime, json, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
if "adoptedAt" in s:
    print("adopted")
else:
    print(datetime.datetime.fromisoformat(s["builtAt"]).timestamp())
PYEOF
)
if [ "$verdict" = "adopted" ]; then
    echo "hermcp-restart: stamp is a boot-time adoption record (no new build), skipping"
    exit 0
fi

started_txt=$("$SYSTEMCTL" show "$UNIT" -p ActiveEnterTimestamp --value)
started=$(date -d "$started_txt" +%s)

if "$PY" -c 'import sys; sys.exit(0 if float(sys.argv[1]) > float(sys.argv[2]) else 1)' "$verdict" "$started"; then
    echo "hermcp-restart: build $verdict is newer than $UNIT start $started, restarting $UNIT"
    "$SYSTEMCTL" restart "$UNIT"
    echo "hermcp-restart: restart issued"
else
    echo "hermcp-restart: $UNIT already runs this build (built $verdict <= start $started), skipping"
fi
