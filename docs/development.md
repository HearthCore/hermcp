# Development

Running the tests, the repository layout and the local environment traps.

[← README](../README.md) · [docs index](../README.md#documentation)

---

```bash
# tests: use the wrapper. It builds a throw-away uv venv (pytest + pytest-cov) that sees the Hermes venv's
# site-packages through a .pth file. That matters: the skills/profiles tests start a probe interpreter
# with `sys.executable -I`, which ignores PYTHONPATH, so a plain pytest venv fails ~16 tests with
# "No module named 'ruamel'". The wrapper also unsets PYTHONPATH and uses an empty HERMES_HOME.
scripts/run_tests.sh -q                      # whole suite, ~2.5 min (expected: all passed)
scripts/run_tests.sh -q -k served            # any pytest args are passed through
# HERMCP_HERMES_AGENT_DIR=<checkout> overrides ~/.hermes/hermes-agent; HERMCP_TEST_VENV=<dir> keeps the venv

# coverage: the CI test job reports it on every run (no threshold, nothing fails on the number).
# Locally the same command works through the wrapper:
scripts/run_tests.sh -q --cov=hermcp --cov-report=term-missing
# `--cov=hermcp.tools.providers` narrows it to one module while you work on that module.

# if the shell exports PYTHONPATH (some agent shells point it at a foreign python3.14 env),
# unset it first: `env -u PYTHONPATH …` -- the Hermes venv's `mcp` import breaks otherwise

# the suite never reads the install's own .env or the shell's HERMCP_* settings: an autouse fixture
# in tests/conftest.py blanks every variable load_settings reads and points HERMCP_ENV_FILE at an
# empty temp dir, so a host that serves the Ledger (HERMCP_LEDGER=1, HERMCP_LEDGER_AUTH=none,
# HERMCP_HOST=0.0.0.0) runs the same tests as CI. tests/test_settings_isolation.py holds the list
# against settings.py.

# regenerate the tool catalog after changing tools
/usr/local/lib/hermes-agent/venv/bin/python -m hermcp docs
```

Layout:

```
plugin.yaml      # Hermes plugin manifest (registers the CLI only, never a server)
cli.py           # serve / keys / oauth / docs / status / stop / install-service / doctor / selftest / profile-keys
oauth.py         # OAuth 2.1 provider on the MCP SDK auth module, paste-key consent page
bridge.py        # in-gateway approvals bridge (AF_UNIX, 0600) + client
server.py        # Streamable-HTTP MCP endpoint, auth, host gate, instructions
registry.py      # tool registry: scope filtering, schema validation, dry_run, audit
scopes.py        # access level + toolset/profile/flag model
keys.py          # HMAC-hashed key store
lock.py          # single-instance flock + role detection + busy reports
audit.py         # append-only JSONL with secret redaction
validation.py    # stdlib JSON-Schema subset (jsonschema is not usable in the Hermes venv)
tools/           # one module per toolset
adapters/        # Hermes internals + CLI bridges
deploy/          # systemd unit template
docs/tools.html  # generated catalog — the review surface for prompts and schemas
```

[`docs/DESIGN.md`](DESIGN.md) is the design contract (architecture, hard constraints, per-phase scope).
`docs/` also holds the research that shaped the design:
OAuth 2.1 requirements, how approvals actually work inside Hermes, the reverse-proxy route,
and a 383-command CLI→tool parity table.

