# The `hermes hermcp` CLI

Every command, the day-to-day ones, and the exit-code contract.

[← README](../README.md) · [docs index](../README.md#documentation)

---

`hermes plugins enable hermcp` registers one sub-command, `hermes hermcp`, on the Hermes CLI. It is
the canonical way to drive hermcp on the host — same code, same flags as `python -m hermcp`, but
without `cd`-ing into the plugin directory or hunting for the right interpreter. Print any level with
`--help`:

```bash
hermes hermcp --help
hermes hermcp keys --help
hermes hermcp keys scopes --help
```

| Command | What it does |
|---|---|
| `hermes hermcp serve [--host H] [--port P] [--log-level L]` | Run the MCP server in the foreground (default `127.0.0.1:9230`). Exactly one instance may serve. |
| `hermes hermcp status` | Who owns the server: instance lock (pid, role), port, bind address, systemd unit state, verdict. |
| `hermes hermcp stop [--force]` | Stop the instance holding the lock; asks systemd when the unit manages it. `--force` signals a systemd-managed pid directly. |
| `hermes hermcp install-service [--enable-now] [--dry-run] [--out PATH] [--no-auto-restart]` | Write the systemd units (`hermcp.service` + `hermcp-restart.path`/`.service`), `daemon-reload`, do not start unless `--enable-now`. |
| `hermes hermcp doctor` | Install check: imports, permissions, key store, unit, proxy/allowlist, api_server keys per profile. `[warn]` is advice, exit 1 is a hard failure. |
| `hermes hermcp selftest [--profiles a,b] [--json] [--skip-slow]` | Compare hermcp's answers with fresh-process references for every profile. Read-only; exit 1 on any FAIL. |
| `hermes hermcp docs [--out PATH]` | Regenerate `docs/tools.html` from the live registry (also refreshed on every server start). |
| `hermes hermcp oauth init [--client-id ID] [--rotate]` | Create the static OAuth client for Claude.ai; secret shown once. `--rotate` invalidates every issued token. |
| `hermes hermcp oauth status` | Client id, token counts. |
| `hermes hermcp oauth revoke-all` | Invalidate every issued access/refresh token (client secret unchanged). |
| `hermes hermcp keys create …` | Create a key; the token is printed once. |
| `hermes hermcp keys list` | Every key with scopes, last use and status. |
| `hermes hermcp keys show <key>` | One key's scopes and metadata. |
| `hermes hermcp keys policy` | The table of what a profile-bound key may call. Creates nothing. |
| `hermes hermcp keys rotate <key>` | New secret, same id; every OAuth token minted from it stops working. |
| `hermes hermcp keys rename <key> <new-name>` | Metadata only — the token keeps working. |
| `hermes hermcp keys scopes <key> [--copy-from K] [--access …] [--toolsets …] [--profiles …] [--deny-tools …] [--(no-)approvals …]` | Change a key's scopes in place; the token keeps working, the next request sees the change. |
| `hermes hermcp keys revoke <key> [--delete]` | Disable a key (`--delete` removes the record). |
| `hermes hermcp profile-keys status [--profiles a,b] [--no-probe] [--json]` | Which api_server key each profile resolves and whether the api_server accepts it. |
| `hermes hermcp profile-keys setup [--dry-run] [--yes --generate] [--copy-from SRC]` | Give profiles their own `API_SERVER_KEY` (interactive; writes the profile `.env`). |
| `hermes hermcp profile-keys rotate <profile> --yes` / `--all --yes` | Replace a profile's api_server key (timestamped backup first, re-probe). |

Day-to-day, that comes down to a handful:

```bash
hermes hermcp keys list                       # who has which scopes
hermes hermcp keys show alice-researcher      # what one key may do
hermes hermcp keys scopes alice-r2 --toolsets status,sessions --no-approvals
hermes hermcp keys rename alice-researcher alice-r2
hermes hermcp doctor                          # after any config change
```

**`hermes hermcp` vs `python -m hermcp`.** Both run the same `cli.py`. Use `hermes hermcp` on the
host (no `cd`, `hermes` resolves its own interpreter); use `python -m hermcp` where no Hermes CLI is
around — a cron job, a container entrypoint, the systemd `ExecStart`, or a plugin that is not
enabled. In an agent shell that exports a foreign `PYTHONPATH`, prefix it with `env -u PYTHONPATH`
(see [Development](development.md)).

**Exit codes.** `0` success, `1` on a failed check (`doctor`, `selftest`, `profile-keys`), `2` when a
command refuses to act (stopping a systemd-managed instance without `--force`, `install-service
--enable-now` while a hand-started instance holds the lock, usage errors), `75` (`EX_TEMPFAIL`)
from `serve` when another instance holds the lock or the port, and `78` (`EX_CONFIG`) when the
environment is invalid (for example a typo in `HERMCP_SYSTEMD_SCOPE`). Both 75 and 78 are listed in
the unit's `RestartPreventExitStatus`, so systemd parks the unit on them — never an automatic retry
loop that cannot fix either cause.

