#!/bin/sh
# Run the hermcp test suite in a defined, reproducible environment.
#
# Why this script exists: several tests (skills/profiles/matrix) start a fresh probe interpreter with
# `sys.executable -I` (adapters/common.py:run_probe). `-I` ignores PYTHONPATH, so that interpreter must
# itself see Hermes' dependencies (ruamel, yaml, ...). A plain pytest venv does not, and 16 tests fail with
# "No module named 'ruamel'". Here the test venv is a thin uv venv (pytest only) that is made to see the
# Hermes venv's site-packages and the Hermes checkout through a .pth file, which -I honours.
# Nothing is installed into the Hermes venv; the test venv lives in a throw-away directory.
#
# Usage:  scripts/run_tests.sh [pytest args]        e.g.  scripts/run_tests.sh -q -k served
# Env:    HERMCP_HERMES_AGENT_DIR  Hermes checkout (default: ~/.hermes/hermes-agent)
#         HERMCP_TEST_VENV         reuse/keep this test venv (default: temp dir, removed afterwards)
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_DIR=$(dirname "$HERE")                      # .../plugins/hermcp
PLUGINS=$(dirname "$PLUGIN_DIR")                   # .../plugins  (so `import hermcp` resolves)
AGENT_DIR=${HERMCP_HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}
HERMES_PY="$AGENT_DIR/venv/bin/python"

[ -x "$HERMES_PY" ] || { echo "run_tests: no Hermes venv python at $HERMES_PY (set HERMCP_HERMES_AGENT_DIR)" >&2; exit 2; }
command -v uv >/dev/null 2>&1 || { echo "run_tests: uv not found" >&2; exit 2; }

HERMES_SITE=$("$HERMES_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
KEEP=${HERMCP_TEST_VENV:-}
VENV=${KEEP:-$(mktemp -d "${TMPDIR:-/tmp}/hermcp-testenv.XXXXXX")}
HOME_TMP=$(mktemp -d "${TMPDIR:-/tmp}/hermcp-testhome.XXXXXX")
cleanup() { rm -rf "$HOME_TMP"; [ -n "$KEEP" ] || rm -rf "$VENV"; }
trap cleanup EXIT INT TERM

[ -x "$VENV/bin/python" ] || uv venv -q --python "$HERMES_PY" "$VENV"
"$VENV/bin/python" -c 'import pytest' 2>/dev/null || uv pip install -q --python "$VENV/bin/python" pytest httpx
SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
printf '%s\n%s\n' "$HERMES_SITE" "$AGENT_DIR" > "$SITE/zz_hermes.pth"

cd "$PLUGINS"
# env -u PYTHONPATH: a foreign PYTHONPATH (some agent shells point at a python3.14 env) breaks the mcp import.
env -u PYTHONPATH HERMCP_HERMES_AGENT_DIR="$AGENT_DIR" HERMES_HOME="$HOME_TMP" \
    "$VENV/bin/python" -m pytest hermcp/tests -p no:cacheprovider "$@"
