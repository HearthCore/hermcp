# hermcp — H.E.R.MCP (Hermes Environment Remote MCP)

A Hermes plugin that exposes the **complete Hermes control surface** (everything the
CLI and the dashboard API can do) as a remote MCP server, with per-key scopes so it is
ready for multi-user and per-agent use.

Goal: a blank Hermes install + hermcp + one admin token is enough to fully configure and
operate Hermes from Claude.ai (incl. voice), Claude Desktop, Codex, Cursor, Gemini, etc.

## Hard constraints (do not violate)

- Code lives in `~/.hermes/plugins/hermcp/`. Runtime data lives in
  `~/.hermes/plugin-data/hermcp/` (key store, OAuth store, audit log). Resolve paths via
  `HERMES_HOME` (default `~/.hermes`), never hardcode `/root`.
- **Never modify the Hermes core** (`/usr/local/lib/hermes-agent/**`). Read it, import it,
  don't patch it.
- **Never edit `~/.hermes/config.yaml` directly** during development/tests. All config
  writes in the product go through Hermes' own config API (`hermes_cli.config` save path /
  `hermes config set` semantics) with backup + YAML validation. Tests must use a
  temporary `HERMES_HOME` fixture.
- Never restart `hermes-gateway` or `hermes-dashboard`. Never run anything bound to a
  non-loopback address during development. Dev server port: `127.0.0.1:9230`.
- Runs with the Hermes venv: `/usr/local/lib/hermes-agent/venv/bin/python`
  (mcp 2.0.0 — note `mcp.server.fastmcp` was removed, use `mcp.server.MCPServer`; check
  `/usr/local/lib/hermes-agent/mcp_serve.py` for current usage), fastapi 0.133,
  uvicorn 0.41, starlette 1.3, pydantic 2.13. No new heavy deps; anything extra must be
  justified in README.
- Secrets are never returned by any tool, never written to the audit log, never logged.

## Architecture

```
MCP client ──HTTPS──> Pangolin (mcp.example.com/mcp) ──> hermcp server (own process)
                                                              ├─ auth (Bearer / OAuth 2.1)
                                                              ├─ scope check per tool call
                                                              ├─ audit log (JSONL)
                                                              └─ adapters → Hermes internals
                                                                   (python imports first,
                                                                    `hermes … ` subprocess fallback,
                                                                    api_server :8642 for chat/runs)
```

- **Own process**, Streamable HTTP transport at path `/mcp`, plus `GET /health`.
  Stateless-friendly: tool calls must not depend on long-lived MCP session state
  (lesson from the old supergateway setup: proxies that drop `Mcp-Session-Id` broke it).
- Entry points: `python -m hermcp serve [--host 127.0.0.1] [--port 9230]` and a
  systemd unit template in `deploy/hermcp.service` (NOT installed by the code).
- Agent-side plugin half: `plugin.yaml` + `__init__.py` registering a CLI command
  `hermes hermcp …` (key management, serve, doctor) via `ctx.register_cli_command`.
  No agent tools, no hooks (risk-free to enable).
- Config via env / `.env` next to the package with sane defaults (`HERMCP_HOST`,
  `HERMCP_PORT`, `HERMCP_PUBLIC_URL`, `HERMCP_ALLOWED_HOSTS`, `HERMES_API_URL`,
  `HERMES_API_KEY` …). Process env > `.env` > defaults.

## Auth & keys

- Key store: `plugin-data/hermcp/keys.json` (0600). Keys stored **hashed** (HMAC-SHA256
  or sha256 + per-key salt), shown once at creation. Prefix `hmcp_`.
- Key record: `id, name, owner, created, last_used, expires (optional), disabled,
  scopes{…}`.
- **Scope model** (enforced server-side on every call; tools outside scope are also
  hidden from `tools/list` for that key):
  - `access`: `read` | `write` | `admin` (admin ⊃ write ⊃ read)
  - `toolsets`: allowlist of toolset names or `*`
  - `profiles`: allowlist of Hermes profile names or `*` (pin a key to one agent, e.g.
    Alice → `researcher`)
  - `deny_tools`: explicit per-tool denylist
  - `approvals`: bool, default false, never implied by `*` (must be set explicitly)
  - `secrets_write`: bool, default false
- Auth paths:
  1. Static `Authorization: Bearer hmcp_…` (Codex, Cursor, scripts, MetaMCP).
  2. **OAuth 2.1 + PKCE (S256)** for Claude.ai custom connectors: authorization-server
     metadata (`/.well-known/oauth-authorization-server`, protected-resource metadata),
     `/authorize` shows a minimal HTML page where the user pastes an hmcp key → the
     issued access/refresh tokens inherit that key's scopes. Tokens persisted to disk
     (hashed) so restarts don't kill connectors. Redirect URIs exact-allowlisted
     (default Claude callbacks). DCR optional/off by default.
- Rate-limit failed auth per IP (simple in-memory token bucket).
- CLI: `hermes hermcp keys create --name … --access … --toolsets … --profiles …`,
  `keys list`, `keys revoke`, `keys rotate`, `keys show`.

## Audit

Append-only JSONL `plugin-data/hermcp/audit.jsonl`: ts, key_id, key_name, tool, args
(secret-like fields redacted), profile, result status, duration. Read tool `audit_tail`
(admin).

## Tool surface

Keep the advertised tool count small (models and voice degrade with huge tool lists):

- Group tools into **toolsets**. Each concrete tool has `name`, `toolset`,
  `access` level, `profile_aware` flag, JSON schema, handler.
- Advertise ~20–30 "core" tools directly; everything else reachable through
  `hermes_search_tools(query)` → `hermes_describe_tool(name)` → `hermes_call(name, args)`
  meta tools (same scope checks apply inside `hermes_call`).
- Every write/admin tool supports `dry_run: bool` returning the planned change (diff for
  config) without applying it.
- Profile-aware tools take optional `profile` (default `default`); validate against key
  scope.

### Toolsets (cover CLI parity — derive the full list from `hermes --help` and every
subcommand's `--help`, plus `hermes_cli/web_routers/*.py`)

| toolset | examples (read / write / admin) |
|---|---|
| `status` | health, version, gateway state, doctor summary, usage/insights |
| `chat` | send a message to an agent (api_server `/v1/chat/completions` or `/v1/runs`), poll run, answer run approval, list conversations, read history |
| `sessions` | list, search, read, export, rename/pin, archive, delete (admin) |
| `models` | current model, list providers/models, set model, aliases, fallbacks list/add/remove, auxiliary models |
| `config` | get/show (secrets masked), set/unset (validated + backup), check, compaction & memory settings |
| `providers` | list, add/remove custom provider, auth status; secrets via write-only `secret_set` (secrets_write) |
| `tools` | list, enable/disable tools & toolsets per platform |
| `plugins` | list, info, enable/disable, install/update/remove (admin) |
| `mcp` | list servers, add/remove/enable/disable, test |
| `skills` | list, view, enable/disable, install (admin) |
| `profiles` | list, show, create/delete/rename (admin), describe |
| `kanban` | boards, list, show, create, edit, comment, assign, block/unblock, complete |
| `cron` | list, show, create, edit, pause/resume, run now, delete |
| `approvals` | list pending, approve/deny, approval mode settings (requires `approvals` scope) |
| `memory` | provider status, settings |
| `ops` | logs tail, gateway restart **only as a request that returns the exact command** (never execute restart itself) |
| `admin` | hermcp key management, audit tail |

Implementation rule: **prefer importing Hermes internals** (`hermes_cli.config`,
`hermes_cli.kanban_db*`, `hermes_cli.approval*`, `hermes_state`, web_routers logic …)
for structured data. Where no stable importable API exists, call the CLI as subprocess
(`hermes <cmd> … --json` when supported) with timeout, and parse. Each adapter is
isolated so a Hermes upgrade breaking one doesn't break the server (tool returns a clear
`adapter_error`). Investigate how pending approvals for gateway sessions can be
listed/resolved out-of-process (gateway/api_server, approval_* modules); if impossible
without core changes, implement what works (`/v1/runs/{id}/approval`) and document the gap.

## Deliverables per phase (historical build plan)

1. Skeleton: package layout, settings, key store, scopes, auth (Bearer), audit,
   tool registry with scope filtering + meta tools, `/health`, `/mcp`, CLI command.
   Tests (pytest) for auth, scopes, redaction, registry filtering.
2. Toolsets: status, sessions, models, config, tools, plugins, mcp, skills, profiles,
   kanban, cron, chat — read + write + admin, dry_run.
3. OAuth 2.1 (persistent), approvals, providers/secrets, memory, ops, audit tool.
4. README (setup, Pangolin route, Claude.ai connector, key examples incl. a
   "Alice → researcher only" key), `deploy/hermcp.service`, `hermes hermcp doctor`.

## Single-instance contract (do not break)

The server can be started from a terminal, from the plugin surface, or from systemd. Exactly
one may serve:

- `lock.py` takes an exclusive `flock` on `plugin-data/hermcp/serve.lock` and writes metadata
  (pid, role=standalone|systemd via `INVOCATION_ID`, host, port, started, argv).
- `hermes hermcp serve` refuses to start when the lock is held, prints who holds it and the
  next step, and exits **75 (EX_TEMPFAIL)**. The systemd unit uses
  `Restart=on-failure` + `RestartPreventExitStatus=75` so it parks instead of flapping.
- A preflight port probe converts "port bound by a foreign/older process" into the same 75
  (a raw uvicorn bind error would exit 1 and restart-loop).
- `hermes hermcp status` reports lock holder, role, port health, unit state and a verdict.
  `hermes hermcp stop` ends a standalone instance and **refuses** for a systemd-managed pid
  (points at `systemctl stop`), unless `--force`.
- `hermes hermcp install-service [--enable-now]` writes the unit, runs `daemon-reload`, and
  refuses `--enable-now` while a standalone instance still holds the lock.
- Migration paths, both directions: plugin/terminal → systemd = `hermes hermcp stop` then
  `systemctl enable --now hermcp`; systemd → manual = `systemctl stop hermcp` then
  `hermes hermcp serve` (the unit stays enabled, its next boot start would be blocked by the
  lock and park at 75 — that is intended, not a bug).

## Verification gate

- `pytest` green; byte-compile clean.
- Live smoke: start `python -m hermcp serve --port 9230` on 127.0.0.1, create a read key
  and an admin key in a temp HERMES_HOME **and** a read-only key against the real home,
  run MCP `initialize` → `tools/list` → a few read tool calls with curl; confirm a
  write tool is rejected for the read key, and `tools/list` hides it.
- Never leave the dev server running at the end.

---

## Research notes and verified facts

Background research lives in `docs/`, verified against the Hermes sources:

- `docs/oauth.md` — concrete OAuth 2.1 spec for hermcp
- `docs/approvals.md` — how approvals work inside Hermes + what is reachable out-of-process
- `docs/deployment.md` — Pangolin route, env values, systemd unit template
- `docs/cli-parity.md` — full CLI→tool mapping table, dangerous-command list, core-tool shortlist

### Verified facts — build on these, don't re-derive

1. **OAuth** — do not hand-roll. The Hermes venv ships the MCP SDK auth module
   (`mcp/server/auth/`): use its `AuthorizationServerMiddleware`/token verifier.
   Design (from `docs/oauth.md`): static `client_id` + `client_secret` (pre-shared,
   shown once), PKCE **S256** mandatory, **DCR disabled** (`/register` → `unsupported`),
   exact-match `redirect_uri` allowlist (Claude callbacks), `resource` = `<public_url>`,
   metadata documents at `/.well-known/oauth-authorization-server` and
   `/.well-known/oauth-protected-resource/mcp`, and a 401 on `/mcp` carrying
   `WWW-Authenticate: Bearer resource_metadata="<public_url>/.well-known/oauth-protected-resource/mcp"`.
   Tokens persisted **hashed** at rest so a restart does not kill client connectors;
   refresh grant supported, `client_secret` enforced on both grants. The OAuth access
   token must inherit the scopes of the `hmcp` key it was minted from — the `/authorize`
   page is where the user pastes that key (mlennie/hermes-mcp pattern).
   Additional verified specifics: advertise **only** `client_secret_post`
   in the AS metadata (the SDK default also advertises `client_secret_basic`, which a
   confidential-only server must not offer); pin the redirect allowlist to exactly
   `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`;
   opaque `token_urlsafe(32)` tokens with access 1h / refresh 30d rotating / code 60s;
   echo the `resource` value back on token lookup so a token cannot be replayed against a
   different host.
   **Metadata URLs live at the public ORIGIN, never under the endpoint path.** `public_url`
   points at the endpoint (`https://host/mcp`), so build every `/.well-known/...` URL from
   `Settings.public_origin` (scheme+host only). `server.py:unauthorized()` already does this;
   any new metadata route must too.

2. **Approvals** — pending approvals exist ONLY in the gateway process memory
   (`tools/approval.py` → `_gateway_queues`; `gateway/session_pending_controls.py` →
   `PendingControls.pending`). There is no table, file, or durable record; a sqlite write
   would never be noticed (the gateway resolves via an in-process `threading.Event`).
   Consequence: hermcp can list/answer approvals **only for runs it started itself** via the
   api_server contract `POST /v1/runs` → `GET /v1/runs/{id}/events` →
   `POST /v1/runs/{id}/approval` (`{choice, request_id}`). Pending approvals of live
   Telegram/chat sessions are NOT reachable out-of-process; those sessions approve via
   their own `/approve` surface. Cross-session approvals are reachable through **this plugin's
   own agent half**: `register(ctx)` runs *inside* the gateway process, so it can import
   `tools.approval.list_gateway_approvals` / `resolve_gateway_approval` /
   `set_gateway_approval_choice` and `gateway.session_pending_controls.PendingControls`
   (`.snapshot(session_id, generation)` / `.respond(...)`) directly — no core patch, no
   subprocess. Design: the agent half publishes the pending queue and accepts answers over a
   loopback-only channel (AF_UNIX socket under `HERMES_HOME/plugin-data/hermcp/`, mode 0600,
   or `127.0.0.1` HTTP with a shared secret), and the standalone server proxies MCP calls to
   it. Cross-session tools stay behind the per-key `approvals` flag + `profiles` pinning, and
   every list/answer lands in the audit log. Loading the agent half needs `plugins.enabled`
   plus a gateway restart (restarts need operator approval).
   Exact calls for the run-scoped path: start with `POST /v1/runs`, follow
   `GET /v1/runs/{id}/events` until an `approval.request` event, answer with
   `POST /v1/runs/{id}/approval` body `{"choice": "once|session|always|deny",
   "request_id": "<optional>", "resolve_all": true}` → response
   `{"object": "hermes.run.approval_response", …, "resolved": N}`, `409 approval_not_pending`
   when nothing is queued.
   **Note:** `approvals.unattended_mode` (default `deny`) does NOT kill
   this path. In `tools/approval.py::_presence` an unattended *platform* keeps `is_ask` while a
   notifier is registered, and `/v1/runs` + streaming chat completions register one
   (`api_server_runs.py:906`). Only `_NOTIFIER_BLIND_APPROVAL_PLATFORMS`
   (`webhook`, `msgraph_webhook`, `approval_context.py:155`) resolves from
   `unattended_mode` instead of parking a pending approval. So MCP-started runs do surface
   real, answerable approval prompts.

3. **Deployment** — Pangolin path routing IS possible: add a **second target** to the
   existing `mcp.example.com` resource (do NOT create a second resource): host
   `127.0.0.1`, port `9230`, Match Path `/mcp`, type `prefix`, Rewrite **empty** (hermcp
   serves `/mcp` itself). Path-scoped routers get priority 113 vs 1 for the `/` catch-all.
   Traefik forwards the **original Host** (`passHostHeader` default true; leave Pangolin's
   "Set Host Header" empty) → `HERMCP_ALLOWED_HOSTS` must contain `mcp.example.com`.
   Resource-level SSO/basic auth **must stay OFF**, otherwise the badger middleware
   consumes the `Authorization: Bearer` before hermcp sees it. Bind hermcp to `127.0.0.1`
   only; Pangolin is the only entry path. Env values and the systemd unit template:
   `docs/deployment.md` §2-§4, `deploy/hermcp.service.template`.

4. **Later additions (do not remove, do not duplicate)** — `tools/ops.py`
   (`gateway_restart` admin + `confirm=true`, `ops_gateway_logs`), `tools/providers.py`
   (`providers_list`, `providers_add`, the remote-auth quartet `provider_auth_start` /
   `_continue` / `_status` / `_cancel` built on a PTY running
   `hermes auth add <provider> --type oauth --no-browser`, and the write-only
   `secret_set` / `secret_list` / `secret_delete`), and `skills_edit` in `tools/skills.py`.
   `docs/tools.html` is generated from the live registry by `scripts/gen_tool_docs.py`
   (`python -m hermcp docs`) and refreshed automatically on every `serve` start — keep both
   paths working, never hand-edit the HTML.

5. **CLI parity** — `docs/cli-parity.md` is the authoritative CLI→tool mapping. The
   toolset names in this SPEC are binding; the shortlist proposed there is the set that
   gets advertised directly to clients, everything else via `hermes_call`.

### Open design questions

- Approvals: run-scoped only (recommended now) or later an in-gateway plugin for
  cross-session approvals?
- Should the public `/mcp` route get an extra Pangolin-level rate limit and/or IP
  allowlist (Anthropic egress `160.79.104.0/21`)?
