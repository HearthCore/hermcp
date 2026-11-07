# Dashboard control

How `dashboard_status` and `dashboard_restart` find out which manager runs the Hermes dashboard and restart exactly that.

[← README](../README.md) · [docs index](../README.md#documentation)

---

`dashboard_status` and `dashboard_restart` (toolset `ops`) find out how the Hermes dashboard is run and restart exactly
that, nothing else. Detection order, the first stage that identifies a manager wins:

| # | stage | what it does |
|---|-------|--------------|
| 0 | `config` | explicit override (below); only evaluated when `mode` is not `auto` |
| 1 | `hermes` | `hermes dashboard --status` (a flag, not a sub-command: text output, no JSON, always exit 0) lists live dashboard PIDs. If it fails, hermcp scans `/proc` itself |
| 2 | `known_unit` | `hermes-dashboard.service`, user scope first, then system scope. This is the unit name Hermes itself restarts after `hermes update`; Hermes ships no installer for it |
| 3 | `custom_unit` | any other service unit that owns a dashboard PID (cgroup and `MainPID` must match) |
| 4 | `process` | free-standing process: argv, cwd and environment **names** from `/proc` give the start recipe |

Mode in the answer: `systemd`, `process`, `none` (nothing found) or `ambiguous` (several managers or processes; hermcp never
picks one). `dashboard_restart` for a free-standing process only runs when the recipe is complete (absolute executable,
cwd, readable environment, `hermes dashboard`, fixed port); otherwise the answer is a plan marked "manual / config needed".
A real restart is verified (new PID, port, HTTP status of `/`, error-like lines in the journal or start log) and, if the
dashboard does not come back, retried once. Environment values are never returned, only names; commands pass through the
central redaction.

### Override (hermcp config, not Hermes' `config.yaml`)

Everything is optional; without any of it the mode is `auto`. Keys in the process environment or `.env`:

```ini
HERMCP_DASHBOARD_MODE=auto            # auto | systemd | process
HERMCP_DASHBOARD_UNIT=my-dash.service # mode=systemd: unit name
HERMCP_DASHBOARD_SCOPE=system         # mode=systemd: system | user
HERMCP_DASHBOARD_COMMAND=/usr/local/bin/hermes dashboard --port 9119 --no-open   # mode=process
HERMCP_DASHBOARD_CWD=/home/user/.hermes    # mode=process: working directory
HERMCP_DASHBOARD_PORT=9119            # fallback when the command carries no --port
HERMCP_DASHBOARD_CONFIG=/path/dashboard.json   # default: plugin-data/hermcp/dashboard.json
```

Per profile, in the JSON file (the tools take a `profile` argument, default `default`; `profiles.<name>` beats `default`,
which beats the environment keys):

```json
{"default": {"mode": "auto"},
 "profiles": {"researcher": {"mode": "process", "command": "/usr/local/bin/hermes dashboard --port 9300 --no-open",
                        "cwd": "/home/user/.hermes"}}}
```

An invalid override (unknown key, `mode=systemd` without unit, a command that is not `hermes dashboard ...`) is reported in
`config_error` and ignored. Process-mode restarts start the new dashboard detached but inside hermcp's cgroup: restarting
`hermcp.service` stops it unless that unit has `KillMode=process`. A systemd-managed dashboard has no such coupling.
Test plan for a client: [docs/dashboard-control-testplan.md](dashboard-control-testplan.md).
