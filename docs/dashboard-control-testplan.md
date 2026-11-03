# Dashboard control: test plan for an MCP client (dry-run first)

Audience: Claude (or any MCP client) connected to hermcp with an **admin** key. Every step 0 to 9 only reads or plans:
nothing in them restarts anything. Step 10 is a real restart and is **OPTIONAL and needs the operator's explicit approval**.

Rules for the whole run:

- Stop at the first abort criterion and report it with the tool answer (no retries that change state).
- Do not call `gateway_restart`. Do not restart hermcp. Do not touch any config.
- Record the baseline in step 0; every later "unchanged" check compares against that, not against a number in this
  document (PIDs change over time).
- The tools are `dashboard_status` (read) and `dashboard_restart` (admin). Call them directly or through
  `hermes_call` with `name` and `args`. If `dashboard_status` is unknown (`unknown_tool`), the running hermcp does not have
  this branch yet: abort and report "branch not deployed".

Expected values below are what the reference host reports (unit `hermes-dashboard.service`, system scope, port 9119).
On another host replace them by what `dashboard_status` of step 1 shows and keep the logic.

## Step 0: baseline

Call `hermes_health` with `{"detail": false}`. Record `gateway.pid` (also `_meta.gateway_pid`).
Expected: `gateway.pid` is a number and `_meta.gateway_alive` is true.
Abort if: the gateway is not alive (do not start a restart test on a sick system).

## Step 1: dashboard_status on the real system

Call `dashboard_status` with `{}`.

Expected:

| field | value |
|-------|-------|
| `stages[*].stage` in order | `config`, `hermes`, `known_unit`, `custom_unit`, `process` |
| `stages[config]` | `hit: false`, reason mentions `mode=auto` |
| `stages[hermes]` | `hit: true`, `pids` = [dashboard PID] |
| `stages[known_unit]` | `hit: true`, `scopes: ["system"]` |
| `stages[custom_unit]`, `stages[process]` | `hit: false` with a reason |
| `mode` / `stage` | `systemd` / `known_unit` |
| `unit.unit`, `unit.scope` | `hermes-dashboard.service`, `system` |
| `port` | `9119` |
| `start_command` | `/usr/local/bin/hermes dashboard --port 9119 --host 0.0.0.0 --no-open` |
| `http["/"]` | `302` or `200` |
| `http["/api/status"]` | `200` |
| `resources` | `rss_mb`, `cgroup_memory_mb`, `memory_max` present |
| `_meta` | present |

Why `known_unit` and not "own unit": Hermes has no installer for a dashboard service. `hermes-dashboard.service` is
hand-written here, but it carries the exact name Hermes' own code restarts after `hermes update`
(`_DASHBOARD_SYSTEMD_UNIT`), so stage 2 (known unit) is the stage that hits. Stage 3 (`custom_unit`) is for a unit with
any other name.

Record `pid` as **dashboard baseline PID**.
Abort if: `ok` is false, `mode` is not `systemd`, `http["/"]` is not 200/302, `pid` is missing, or any value of an
environment variable / token shows up in the answer.

## Step 2: dry_run plan

Call `dashboard_restart` with `{"dry_run": true, "confirm": true}`.

Expected: `applied: false`, `dry_run: true`, `mode: "systemd"`, `plan.commands == ["systemctl restart hermes-dashboard.service"]`,
`plan.side_effects` lists that only this unit is restarted and the gateway and hermcp are not touched, `plan.after`
(verification) and `plan.fallback` (`reset-failed` then `start`) present.
Abort if: `applied` is true.

## Step 3: without confirm

Call `dashboard_restart` with `{}`.

Expected: `applied: false`, `needed: "confirm=true"`, `refused` text, the same `plan` as in step 2.
Abort if: `applied` is true.

## Step 4: dry_run without confirm

Call `dashboard_restart` with `{"dry_run": true}`.

Expected: same as step 3 (plan only).

## Step 5: nothing changed

Call `dashboard_status` with `{}` and `hermes_health` with `{"detail": false}`.

Expected: `pid` equals the dashboard baseline PID (same process, `unit.active_since` unchanged), `http["/"]` still 302/200;
`gateway.pid` equals the step 0 value.
Abort if: either PID differs.

## Step 6: profile argument

Call `dashboard_status` with `{"profile": "researcher"}` (any existing profile name).

Expected: same detection as step 1, `configured_mode: "auto"`, `config_source: "defaults"` (no per-profile override exists).

## Step 7: input validation

Call `dashboard_status` with `{"profile": "../etc"}`.

Expected: `ok: false`, `error.code == "invalid_arguments"`.

## Step 8: override cases as dry_run (no change to production config)

The override keys live in hermcp's own environment/`.env`/JSON, so testing them on the production hermcp would mean a config
change and a hermcp restart. Use the **second instance** from the section "Isolated second instance" below instead, or the
automated pytest cases (`tests/test_dashboard.py`, section "config override"). Expected results per override:

| override env of the test instance | `dashboard_status` | `dashboard_restart` `{"dry_run": true, "confirm": true}` |
|-----------------------------------|--------------------|-------------------------------------------------|
| `HERMCP_DASHBOARD_MODE=systemd`, `_UNIT=hermes-dashboard.service`, `_SCOPE=system` | `mode: systemd`, `stage: config`, `stages[config].hit: true`, `known_unit` shown as skipped | plan `systemctl restart hermes-dashboard.service`, `applied: false` |
| `HERMCP_DASHBOARD_MODE=process`, `_COMMAND=/usr/local/bin/hermes dashboard --port 9119 --host 0.0.0.0 --no-open`, `_CWD=~/.hermes` | `mode: process`, `process.supervised_by: hermes-dashboard.service (system)` | `plan.blocked` says the PID is the MainPID of the unit and to use `mode=systemd`; `applied: false`; with `confirm: true` and no dry_run: error `not_restartable`, nothing killed |
| `HERMCP_DASHBOARD_MODE=systemd` without unit | detection as in auto, `config_error` says `needs unit=<name>.service`, `configured_mode: auto` | plan as in auto |

Abort if: any of these changes a PID, or a `confirm: true` call in the second row applies something.

## Step 9: audit

Call `audit_tail` with `{"tool": "dashboard_restart", "n": 5}` and `{"tool": "dashboard_status", "n": 3}` (admin read).

Expected: one entry per call of steps 1 to 7 with `status: "ok"` (validation failures show their error code), the key name
of the caller, args without secrets.

## Step 10 (OPTIONAL, NEEDS EXPLICIT OPERATOR APPROVAL): real restart

Only after the operator wrote an explicit go for a real dashboard restart in this conversation. Never run it as part of an
automatic dry-run pass.

1. Call `dashboard_restart` with `{"confirm": true}`.
2. Expected: `applied: true`, `old_pid` = dashboard baseline PID, `new_pid` different, `verify.healthy: true`,
   `verify.http["/"]` 302/200, `verify.http["/api/status"]` 200, `verify.log_errors` empty (non-empty = report the lines,
   not a failure by itself).
3. Call `dashboard_status`: `pid` equals `new_pid`, `unit.state: active`.
4. Call `hermes_health`: `gateway.pid` equals the step 0 value (the gateway must not have been touched).
5. If the answer is `error.code == "restart_failed"`: do not repeat. Report `error.verify`, `error.steps`,
   `error.manual`. hermcp already tried `reset-failed` + `start` once.

Abort if: `gateway.pid` changed, or the dashboard is down after the call.

## Isolated second instance (no production risk, nothing restarted)

Runs the branch on another port with a throw-away `HERMES_HOME` (own key store, own audit log, own inbox). It never touches
the production hermcp, its keys or its data; it only reads the dashboard's state. The helper script
`scripts/dashboard_testplan_check.py` performs steps 1 to 5 against any URL.

```bash
S=$(mktemp -d); git -C <repo> worktree add "$S/hermcp" feat/dashboard-control   # or any checkout of the branch
PY=/usr/local/lib/hermes-agent/venv/bin/python
export HERMES_HOME="$S/home" PYTHONPATH="$S"; mkdir -p "$HERMES_HOME"
umask 077; $PY -m hermcp keys create --name dc-test --access admin --expires 1d | grep -o 'hmcp_[A-Za-z0-9_-]*' > "$S/tok"
# optional override under test, e.g. HERMCP_DASHBOARD_MODE=systemd HERMCP_DASHBOARD_UNIT=hermes-dashboard.service
HERMCP_PORT=9231 HERMCP_HOST=127.0.0.1 $PY -m hermcp serve &        # keep the PID
python3 "$S/hermcp/scripts/dashboard_testplan_check.py" --url http://127.0.0.1:9231/mcp --token-file "$S/tok"
# add --expect-stage config when an override is set
kill %1; git -C <repo> worktree remove --force "$S/hermcp"; rm -rf "$S"
```

The test instance's `_meta.gateway_pid` is empty (its own `HERMES_HOME` has no `gateway_state.json`); the script reads the
real `~/.hermes/gateway_state.json` for the gateway comparison instead.

## Offline suite

```bash
cd ~/.hermes/plugins && env -u PYTHONPATH /usr/local/lib/hermes-agent/venv/bin/python -m pytest hermcp/tests -q
```

`tests/test_dashboard.py` covers every stage with canned `systemctl show`, `hermes dashboard --status` and `/proc` data:
known unit (user and system scope), custom unit, free-standing process (complete and incomplete recipe), nothing found,
ambiguous, config override (env, JSON per profile, invalid), redaction, dry_run changes nothing, confirm required, verified
restart, restart with recovery attempt, kill that does not stop the process.
