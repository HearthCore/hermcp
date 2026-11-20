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

# the suite never reads the install's own .env, the shell's settings or the live ~/.hermes: see
# "Testing principles" below. A hostile-host check before a test-infrastructure change:
HERMCP_LEDGER=1 HERMCP_LEDGER_AUTH=none HERMCP_HOST=0.0.0.0 HERMES_PROFILE=researcher \
  TZ=Pacific/Kiritimati scripts/run_tests.sh -q              # expected: the same result as without

# regenerate the tool catalog after changing tools
/usr/local/lib/hermes-agent/venv/bin/python -m hermcp docs
```

## Testing principles

The suite is a contract, not a coverage number. CI reports coverage so a drop is visible; nobody
writes a test to move it. The audit in [#87](https://github.com/HearthCore/hermcp/issues/87)
applied these rules to every test, and new tests follow them:

- **A test earns its place by a risk it pins**: a behaviour a user or client relies on, a security
  boundary (scopes, masking, auth, file modes), or a regression that happened. A one-line check of
  a private helper with no failure mode, or a second copy of an assertion that another test already
  makes, is removed, not kept "for coverage".
- **Test behaviour, not mechanics.** Drive the real thing where it is cheap (a real PTY, a real
  temp `keys.json`, a fake api_server on an ephemeral port) instead of replacing the stdlib call the
  code happens to use today. A test that breaks when `select.poll` becomes `selectors` without any
  change in behaviour is a maintenance cost, not a guard.
- **No host state.** `tests/conftest.py` blanks every `HERMCP_*` and the host variables hermcp
  reads (`HERMES_API_URL`, `HERMES_PROFILE`, `HERMES_PLATFORM*`, `HERMES_SESSION*`,
  `INVOCATION_ID`), points the plugin `.env` at a file that does not exist, gives every test an
  empty `HERMES_HOME`, an api_server URL that refuses at once and an hermcp port nothing serves.
  `tests/test_settings_isolation.py` scans the product code so a new variable cannot slip past.
  A test that needs a value sets it itself; a test that needs a server starts a fake one.
- **Only committed files.** A test that checks the repository's own files lists them with
  `conftest.tracked_files()` (`git ls-files`), never a glob over the working tree: a live install
  keeps git-excluded notes under `docs/prep/`, a developer has `node_modules/` or a scratch script.
- **No order, no shared state.** The tool registry, module-level session tables and class-level
  request logs are process-wide. Register throw-away tools with the `scratch_tools` fixture, clear
  a shared log at the start of the test that reads it. The suite must pass in any order: it runs
  in file order, so after touching fixtures check a shuffled run once (e.g. `pytest-randomly`
  installed into the throw-away venv kept with `HERMCP_TEST_VENV`).
- **No wall-clock or zone assumptions.** Timestamps in fixtures age with `time.time()`; a test of
  time logic sets `TZ` itself (`server_zone` in `test_ledger_page.py`). Timing tests assert an upper
  bound with generous headroom, never an exact duration.
- **No environment limits taken for granted.** The fd limit, a free port, the presence of
  `systemctl` or `git` differ between hosts: raise the limit inside the test and restore it, bind
  port 0, fake the binary — or skip with the reason when the premise cannot be built.
- **A product bug found by a test becomes an issue**, not a silent fix inside a test PR. If the
  test is worth keeping until the fix lands, mark it `xfail(strict=True, reason="#<issue>")`: it
  turns red the day the fix makes it pass, so the marker cannot be forgotten.

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
validation.py    # stdlib JSON-Schema subset (jsonschema is not usable in the Hermes venv); `pattern` is ECMA-262, so `$` is end-of-input
tools/           # one module per toolset
adapters/        # Hermes internals + CLI bridges
deploy/          # systemd unit template
docs/tools.html  # generated catalog — the review surface for prompts and schemas
```

[`docs/DESIGN.md`](DESIGN.md) is the design contract (architecture, hard constraints, per-phase scope).
`docs/` also holds the research that shaped the design:
OAuth 2.1 requirements, how approvals actually work inside Hermes, the reverse-proxy route,
and a 383-command CLI→tool parity table.

