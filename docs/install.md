# Installing hermcp

Everything the five-step install holds: the runtime `.env`, the systemd managers, the service units, auto-restart after a Hermes update and the single-instance lock.

[← README](../README.md) · [docs index](../README.md#documentation)

---

Four steps: clone it, enable the plugin, configure `.env`, run it as a service. Then create one key
and verify the install.

### 1. Clone into the plugins directory

```bash
git clone https://github.com/HearthCore/hermcp.git ~/.hermes/plugins/hermcp
```

The folder name must be `hermcp` — Hermes imports the plugin by that name.

### 2. Enable the plugin — this gives you `hermes hermcp`

```bash
hermes plugins enable hermcp     # adds `hermcp` to plugins.enabled
hermes hermcp --help             # the CLI is registered now
```

The plugin registers the CLI command and, inside the gateway, the approval hooks and the approvals
socket — it never starts a server and never opens a TCP port. Everything below therefore works both as
`hermes hermcp <cmd>` (canonical once the plugin is enabled) and as `python -m hermcp <cmd>` from
`~/.hermes/plugins` (works without the plugin, which is what systemd units, cron jobs and scripts use —
see [The `hermes hermcp` CLI](cli.md)).

Enabling the plugin also arms the **agent half** in the gateway (the approvals socket and the
approval hooks). That part only comes alive on the gateway's next start — the owner restarts it;
hermcp never restarts the gateway for you.

### 3. Create the runtime env

`~/.hermes/plugins/hermcp/.env`, mode 0600:

```ini
HERMCP_PUBLIC_URL=https://hermes.example.com/mcp   # drives OAuth metadata + Host allowlist
HERMCP_ALLOWED_HOSTS=hermes.example.com,127.0.0.1,localhost
HERMCP_HOST=127.0.0.1        # loopback only; the proxy is the single entry path
HERMCP_PORT=9230
HERMCP_TRUST_PROXY=1         # trust X-Forwarded-For for the failed-auth limiter
HERMCP_DEBUG_CAPTURE=1          # discovery aid: record what MCP clients send (headers, clientInfo, _meta; no secrets, no argument values); stops after HERMCP_DEBUG_CAPTURE_HOURS (default 12); read with the admin tool capture_report
HERMES_API_URL=http://127.0.0.1:8642
# HERMCP_SYSTEMD_SCOPE=user       # only if the gateway runs as a systemd USER unit, see below
# HERMCP_SELF_SYSTEMD_SCOPE=user  # only if hermcp.service itself is a user unit (default: system)
```

Local-only installs need none of the proxy lines: with no `.env` at all the defaults are
`HERMCP_HOST=127.0.0.1`, `HERMCP_PORT=9230` and `HERMES_API_URL=http://127.0.0.1:8642`.

#### systemd managers of `gateway_restart`, `ops_gateway_logs` and `hermcp_restart`

Two independent settings (env or `.env`, both default to `system`, so existing installs behave exactly as before):

| Variable | Which unit | Used by |
|---|---|---|
| `HERMCP_SYSTEMD_SCOPE=system\|user` | the Hermes gateway unit (`hermes-gateway.service`) | `gateway_restart`, `ops_gateway_logs` |
| `HERMCP_SELF_SYSTEMD_SCOPE=system\|user` | hermcp itself (`hermcp.service`) | the payload of `hermcp_restart` |

The self-restart is a transient systemd timer that runs `systemctl restart hermcp.service` after a few seconds, outside
hermcp's own cgroup. The timer lives in the **user manager as soon as either variable is `user`** (a user manager
exists, and a transient timer there needs no authorization), otherwise in the system manager.

| Gateway unit | hermcp unit | `HERMCP_SYSTEMD_SCOPE` | `HERMCP_SELF_SYSTEMD_SCOPE` | Gateway restart / logs | Self-restart timer | Self-restart payload | Rights the account needs |
|---|---|---|---|---|---|---|---|
| system | system | `system` (default) | `system` (default) | `systemctl restart ...` / `journalctl -u ...` | `systemd-run` (system manager) | `systemctl restart hermcp.service` | permission to restart both units in the system manager |
| user (`Linger=yes`) | system | `user` | `system` | `systemctl --user restart ...` / `journalctl --user -u ...` | `systemd-run --user` | `systemctl restart hermcp.service` (system manager) | permission to restart `hermcp.service` in the system manager; none for the gateway or the timer |
| user (`Linger=yes`) | user | `user` | `user` | `systemctl --user ...` / `journalctl --user -u ...` | `systemd-run --user` | `systemctl --user restart hermcp.service` | none |
| system | user | `system` | `user` | `systemctl restart ...` / `journalctl -u ...` | `systemd-run --user` | `systemctl --user restart hermcp.service` | permission to restart the gateway unit in the system manager |

An invalid value is reported by `hermes hermcp doctor` as a `[FAIL] systemd scope` row naming the variable, and
`hermcp serve` refuses to start with one readable error. Inside the gateway process (approval hooks) a bad value never
switches anything off: it falls back to `system`. The plan returned by `dry_run` names the exact command and the scopes
in use (`systemd_scope`, and for `hermcp_restart` also `timer_scope`).

**User-manager calls:** `XDG_RUNTIME_DIR` is set at run time to `/run/user/<uid>` of the hermcp process if it is
missing, so no uid is written into a unit file and the shipped units stay generic.

**Rights in the system manager.** hermcp never grants itself anything. If the account that runs hermcp is not root
and a restart goes through the system manager, grant exactly that, for example with a polkit rule pinned to the
unit and the verb (generic example, adapt the unit and the account):

```js
// /etc/polkit-1/rules.d/50-hermcp-restart.rules  (requires polkit, e.g. the polkitd package)
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "hermcp.service" &&
        action.lookup("verb") == "restart" &&
        subject.user == "hermcp-account") {
        return polkit.Result.YES;
    }
});
```

Keep `NoNewPrivileges=yes` in the unit: polkit works over D-Bus and needs no setuid helper. Do not use wildcards on
the unit or the verb.

**Transient timers are a second permission.** The self-restart does not only restart the unit, it first creates a
transient timer with `systemd-run` — and that is a *different* right than restarting a unit. A non-root account
therefore also needs the right to create transient timers in the system manager; a polkit rule for `restart
hermcp.service` alone does not cover it, and `systemd-run` fails with "Interactive authentication required".
Either run hermcp as root, widen the rule to the transient units, or put both scopes on `user` (see the table
above), where no authorization is needed.

### 4. Run it as a service

The server must live **outside** the gateway's process tree:

```bash
hermes hermcp install-service              # writes /etc/systemd/system/hermcp.service (+ the auto-restart units)
systemctl enable --now hermcp
systemctl enable --now hermcp-restart.path # auto-restart after Hermes updates, see below
hermes hermcp status                       # who serves, which role, unit state, verdict
```

`install-service` also takes `--enable-now` (installs, enables and starts in one go), `--dry-run`
(prints the units, writes nothing), `--out <path>` (different unit location) and `--no-auto-restart`.
To try it without systemd: `hermes hermcp serve --port 9230` — exactly one instance may serve, see
[Single instance](install.md#single-instance).

#### Auto-restart after Hermes updates (the `hermcp-restart.path` unit above)

hermcp keeps its Python modules in memory, so after a Hermes update it answers from old code until it
restarts (`_meta.hermcp_older_than_hermes_build` tells you). `install-service` therefore also installs two
small units, `hermcp-restart.path` and `hermcp-restart.service`, **on by default**:

- They watch `install-stamp.json` in the Hermes checkout, which Hermes rewrites at the end of every update.
- 60 s after a new build they restart `hermcp.service` and nothing else (never the gateway or the dashboard),
  at most 3 times per 10 minutes. A rewrite that carries no new build (boot-time adoption) is ignored.

The checkout is not hardcoded. It comes from `HERMCP_HERMES_AGENT_DIR`, else the reference path
`/usr/local/lib/hermes-agent` if it exists, else it is detected from the `hermes` executable (where its
interpreter imports `hermes_cli` from, or the `<checkout>/venv/bin/hermes` layout). If no stamp is found, for
example because Hermes runs in a container hermcp cannot see, the units are skipped with a hint.

| Flag | Effect |
|---|---|
| `--no-auto-restart` | do not install the restart units |
| `--dry-run` | print the units that would be written, change nothing |
| `--enable-now` | also enable `hermcp-restart.path` (besides `hermcp.service`) |

Units that are already symlinks (`systemctl link` of the files in `deploy/systemd/`) are never overwritten.
To restart on demand instead, use the admin tool `hermcp_restart`. Details:
[`docs/stale-hermcp-after-hermes-update.md`](stale-hermcp-after-hermes-update.md) and
[`deploy/systemd/README.md`](../deploy/systemd/README.md).

### 5. First key and a smoke test

```bash
hermes hermcp keys create --name admin-key --access admin   # prints hmcp_<id>_<secret> ONCE
hermes hermcp keys list                                     # scopes, last use, status
hermes hermcp doctor                                        # install check, exits 1 on a hard failure
curl -fsS http://127.0.0.1:9230/health                      # 200 when the server is up
```

Point a client at `http://127.0.0.1:9230/mcp` (locally) or `https://hermes.example.com/mcp` (behind
the proxy) with `Authorization: Bearer hmcp_<id>_<secret>`. Read-only first is the sane default:

```bash
hermes hermcp keys create --name readme-readonly --access read \
  --toolsets status,sessions --profiles default
```

`hermes hermcp selftest --skip-slow` then compares hermcp's answers against fresh-process
references; it is read-only and exits 1 on any FAIL.

---

## Single instance

The server can be started by hand, by systemd, or (in future) from the plugin surface.
Exactly one may serve — an `flock` on `~/.hermes/plugin-data/hermcp/serve.lock` decides,
with metadata (pid, role `standalone|systemd`, host, port, start time, argv).

- A second `serve` prints who holds it and the next step, then exits **75 (EX_TEMPFAIL)**.
- The unit uses `Restart=on-failure` + `RestartPreventExitStatus=75 78`, so it parks with a
  readable message instead of restart-looping. `75` covers a taken port, `78` an invalid
  configuration — a restart cannot fix a typo in `.env` either, it would just flap every 5 s.
  A preflight port probe converts "port taken by a foreign process" into exit `75` (a raw bind
  error would exit `1` and flap).
- Migration both ways:
  - hand-started → systemd: `hermes hermcp stop` then `systemctl enable --now hermcp`
  - systemd → hand-started: `systemctl stop hermcp` then `hermes hermcp serve`

