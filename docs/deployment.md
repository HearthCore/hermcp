# hermcp deployment

Target: expose `hermcp` (own process, Streamable HTTP at `/mcp`, dev port `9230`)
running on a host reachable at e.g. `10.0.0.10` (a Proxmox LXC in the reference setup) publicly at
`https://mcp.example.com/mcp`, reachable by Claude.ai / Claude Desktop /
Codex / Cursor. Reverse proxy: Pangolin (fosrl/pangolin).

Verified against the Pangolin source (`fosrl/pangolin`, main branch) in October 2026. File paths
below refer to a shallow clone of that repository.

---

## 1. Pangolin findings — what is / isn't possible for path routing

**Pangolin's reverse proxy is Traefik.** Pangolin does not proxy itself for
HTTP; it generates a Traefik config (`server/lib/traefik/getTraefikConfig.ts`)
and Traefik does the actual routing. A Traefik *plugin* middleware named
`badger` (`server/lib/traefik/TraefikConfigManager.ts:616`) handles per-resource
auth.

### Path routing IS possible (per-target, on one domain)

Path matching is configured on the **target**, not the resource
(`getTraefikConfig.ts` reads `path` / `pathMatchType` / `rewritePath` /
`rewritePathType` / `priority` from the `targets` table). Each target on a
resource can carry a path scope.

- `server/lib/traefik/rule.ts:appendPathMatch()` appends
  `Path()` (exact) / `PathPrefix()` (prefix) / `PathRegexp()` (regex) to the
  Traefik rule, using Traefik v3 semantics.
- `rule.ts:computeRoutePriority()` gives path-scoped routers higher priority
  than the catch-all: base `100` + `10` for "has a path" + `3` for `prefix` =
  `113`, vs a catch-all `/` which is forced down to `1`. A `/mcp` prefix route
  therefore cleanly beats the existing `mcp.example.com` `/` catch-all that
  points at the dashboard.
- Consequence: **yes**, `/mcp` on the existing domain can route to a *different*
  upstream port (9230) than the rest of the domain (dashboard on 9119). You add
  a *second target* to the existing resource — you do **not** create a second
  resource/domain.

Evidence: `rule.ts:17-37` (path clause), `rule.ts:83-107` (priority),
`getTraefikConfig.ts:483-518` (router rule + priority assembly).

### Host header: preserved by default

- Traefik's default `passHostHeader` is `true`. For ordinary HTTP resources the
  generated config emits **no** `passHostHeader` key at all
  (`getTraefikConfig.ts:545-555`), so the Traefik default applies and the
  original client `Host` header is forwarded to the upstream unchanged.
- The upstream `servers[].url` is `http://<ip>:<port>` (or the tunnel subnet for
  `newt` sites) — the `url` field only sets the backend address, it does **not**
  rewrite `Host` (`loadBalancer.ts:46-64`).
- Pangolin has an explicit **"Set Host Header"** override
  (`resources.setHostHeader`, surfaced in
  `getTraefikConfig.ts:101` and applied in
  `headersMiddleware.ts:49-51`). If left empty, Host is **not** touched.
- Therefore the hermcp server receives `Host: mcp.example.com` (the public
  hostname), and `HERMCP_ALLOWED_HOSTS` must contain `mcp.example.com`.

X-Forwarded headers: Traefik adds `X-Forwarded-For`, `X-Forwarded-Host`,
`X-Forwarded-Proto` (and `X-Forwarded-Port`/`-Server`). It does **not** set
`X-Real-IP` (that's an nginx convention) — use `X-Forwarded-For`. hermcp's
`client_ip()` reads `X-Forwarded-For` when `HERMCP_TRUST_PROXY=1`
(`server.py:76-81`), which is how the per-IP failed-auth limiter works behind
the proxy.

### Authorization header survives — with one condition

Pangolin's own auth never uses `Authorization: Bearer`:

- Resource access tokens use custom headers `P-Access-Token-Id` +
  `P-Access-Token`, or the query param `p_token` (defaults in
  `readConfigFile.ts:101-109`).
- User sessions use the cookie `p_session_token` (default,
  `readConfigFile.ts:97-100`).
- Resource *passwords* use HTTP Basic auth — `badger/verifySession.ts` checks
  `Authorization` only for `Basic` (`verifySession.ts:1878-1886`, "Authorization
  header is not Basic Auth").

So a `Bearer hmcp_…` header sent by an MCP client is **not** consumed by Pangolin
and is forwarded verbatim to hermcp.

**The one condition:** the resource must be **public** (no Pangolin SSO /
password / access-token enabled). If you turn on Pangolin's own auth on the
resource, the `badger` middleware intercepts the request (401 / redirect to
login) *before* it reaches hermcp and the MCP Bearer auth breaks. The design is:
Pangolin does TLS + routing only; hermcp does its own Bearer/OAuth auth.

---

## 2. Exact route configuration (Pangolin UI)

All under the **existing** `mcp.example.com` resource (HTTP mode). Leave
everything else untouched.

### New target (route `/mcp` → hermcp)

| Field | Value | Notes |
|---|---|---|
| Site | `hermes` (the tunneled site) | the `pangolin up` client runs on hermes |
| Method | `http` | plain HTTP to the loopback port |
| Host | `127.0.0.1` | address *on the hermes host* |
| Port | `9230` | hermcp dev/upstream port |
| Match Path | `/mcp` | |
| Match Path Type | `prefix` | matches `/mcp` and `/mcp/…` |
| Rewrite Path | *(empty)* | do NOT strip — hermcp serves at `/mcp` itself |
| Priority | `100` (default) | auto-raised to 113 for path match; beats the `/` catch-all (1) |
| Health check | optional — path `/health` | loopback-only is also fine (see §4) |

The existing dashboard target (`/` catch-all → `127.0.0.1:9119`) stays as-is.

### Resource-level settings to verify

| Field | Value | Why |
|---|---|---|
| Set Host Header | *(empty)* | preserve original `Host: mcp.example.com` |
| Request Headers / Response Headers | *(empty)* | no custom header needed |
| SSO / Password / Basic auth | **OFF** | must be public, else badger intercepts the Bearer token |
| SSL / TLS | **ON** (Let's Encrypt) | already enabled for the domain |

Routing result: `POST https://mcp.example.com/mcp` reaches hermcp at
`POST /mcp` with the original Host header. The `GET /health` endpoint of hermcp
is *not* reachable via `https://mcp.example.com/health` (that path falls to
the dashboard target) — health checks are done on the host directly (see §4).

---

## 3. Required env values for hermcp

Set these in `~/.hermes/plugins/hermcp/.env` (read by both `settings.py`
and the systemd `EnvironmentFile`).

```
# Public URL clients connect to (also drives OAuth resource metadata).
HERMCP_PUBLIC_URL=https://mcp.example.com/mcp

# Host header values the server accepts. Traefik forwards the ORIGINAL Host,
# so this must be the public hostname (not 127.0.0.1). Keep the loopback
# entries for the local curl health check and direct on-host use.
HERMCP_ALLOWED_HOSTS=mcp.example.com,127.0.0.1,localhost

# Bind loopback only — Pangolin is the only entry path from outside.
HERMCP_HOST=127.0.0.1
HERMCP_PORT=9230

# Trust X-Forwarded-For for the per-IP failed-auth limiter (set by Traefik).
HERMCP_TRUST_PROXY=1

# Upstream Hermes control API (same host, loopback) + its key.
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=<api key>
```

### Why `HERMCP_ALLOWED_HOSTS=mcp.example.com` (Host header reasoning)

hermcp validates the `Host` header for DNS-rebinding protection
(`server.py:host_ok`, lines 83-88). Because Traefik's default is
`passHostHeader: true` and Pangolin's `setHostHeader` is left empty, the Host
header that reaches hermcp is the **public** name `mcp.example.com` — not
the upstream `127.0.0.1:9230`. If the allowlist only contained the loopback
default, every public request would be rejected with 403 "host not allowed".

Note: `settings.py` auto-appends the `HERMCP_PUBLIC_URL` hostname to the
allowlist (lines 85-93), so setting only `HERMCP_PUBLIC_URL` would *also* work —
but `HERMCP_ALLOWED_HOSTS` is set explicitly here so the intent is unambiguous
and the loopback entries stay for on-host health checks.

---

## 4. systemd unit (full text)

Written to `deploy/hermcp.service.template`. Full text:

```ini
[Unit]
Description=hermcp - Hermes Remote MCP server (Streamable HTTP at /mcp)
After=network-online.target
Wants=network-online.target
After=hermes-gateway.service
Wants=hermes-gateway.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=%h/.hermes/plugins
Environment="PYTHONPATH=%h/.hermes/plugins"
ExecStart=/usr/local/lib/hermes-agent/venv/bin/python -m hermcp serve
EnvironmentFile=-%h/.hermes/plugins/hermcp/.env
Environment="HOME=%h"
Environment="HERMES_HOME=%h/.hermes"
Environment="PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

NoNewPrivileges=yes
ProtectSystem=full
ReadWritePaths=%h/.hermes/plugin-data/hermcp
PrivateTmp=yes

ExecStartPost=/bin/sh -c 'for i in $(seq 1 30); do curl -fsS --max-time 2 http://127.0.0.1:9230/health >/dev/null 2>&1 && exit 0; sleep 1; done; echo "hermcp did not become healthy on :9230" >&2; exit 1'

[Install]
WantedBy=multi-user.target
```

### Why each hardening choice is safe

- **`WorkingDirectory=~/.hermes/plugins` + `PYTHONPATH`**: `python -m
  hermcp` must import the `hermcp` package, which lives *inside* the plugins
  dir. `-m` puts the CWD on `sys.path[0]`; `PYTHONPATH` makes it robust even if
  the CWD assumption changes.
- **`ExecStart` uses the Hermes venv python**: the system python lacks the
  `mcp`/`uvicorn`/`starlette` deps; the venv at `/usr/local/lib/hermes-agent/venv`
  has them (per DESIGN.md). This path is read-only under the hardening below.
- **`NoNewPrivileges=yes`**: hermcp never needs to escalate (setuid/caps), so
  forbidding it removes a whole class of privesc. No downside.
- **`ProtectSystem=full`**: makes `/usr`, `/boot`, `/etc` read-only but leaves
  the home directory writable — exactly the split needed. hermcp *reads* `~/.hermes`
  (config, plugins, skills) and *writes* only `~/.hermes/plugin-data/hermcp`
  (`settings.py:data_dir()`, `keys.py`, `audit.py` all target that dir). `full`
  (not `strict`) avoids having to whitelist every read path under the home directory.
- **`ReadWritePaths=~/.hermes/plugin-data/hermcp`**: belt-and-suspenders so
  key/audit writes survive even if someone later flips to `ProtectSystem=strict`.
- **`PrivateTmp=yes`**: hermcp uses neither `/tmp` nor `/var/tmp`; this only
  shrinks the surface.
- **Deliberately NOT setting `ProtectHome=read-only`**: it would make the home directory
  read-only and break the key/audit writes. Left at its default (unset).
- **`ExecStartPost` curl gate**: systemd has no Docker-style HEALTHCHECK; a
  startup health gate is the systemd idiom. It retries `GET
  http://127.0.0.1:9230/health` for up to ~30 s and fails the unit otherwise,
  which `Restart=always` then retries. For continuous monitoring, point
  Uptime-Kuma / netwatch at the same loopback `/health`.

---

## 5. Security checklist for public exposure

**What MUST NOT be exposed**
- hermcp binds `127.0.0.1` only (`HERMCP_HOST`). Never set it to `0.0.0.0` —
  the only public entry path is Pangolin.
- Only `/mcp` and `/health` are routed; nothing else on the host is reachable
  via the domain except the existing dashboard target.
- The key store `keys.json`, audit log, and `.env` live under
  `~/.hermes/plugin-data/hermcp/` (0600) and are never served by any HTTP
  route.

**Auth must stay at hermcp**
- Do **not** enable Pangolin SSO/password/basic-auth on this resource — it
  would consume/intercept the request before the MCP Bearer token reaches
  hermcp. Keep the resource public; enforce per-key scopes in hermcp itself.

**Rate limiting**
- hermcp already rate-limits failed auth per IP (in-memory token bucket,
  `auth_fail_limit`/`auth_fail_window` in `settings.py`; needs
  `HERMCP_TRUST_PROXY=1` to see the real client IP via `X-Forwarded-For`).
- Pangolin's Traefik can add a `ratelimit` middleware if you want a proxy-level
  cap, but that is not required for correctness.

**fail2ban-ish / brute-force measures**
- The hermcp per-IP limiter is the first line. For host-level defense, monitor
  `journalctl -u hermcp` for repeated 401s; a fail2ban jail on the audit log is
  optional. Pangolin's badger plugin itself is a Traefik plugin, not sshd, so a
  standard fail2ban sshd jail does not apply.

**IP allowlisting option**
- If you want to restrict *who can even reach* the endpoint, the clean lever is
  the Pangolin resource access policy (or a Traefik IP allowlist middleware) —
  but **be careful**: Claude.ai's custom connectors originate from Anthropic's
  infrastructure, not from your machine. See below.

**Anthropic / Claude outbound IP ranges — do they matter?**
Yes, if you IP-allowlist. Claude's MCP client calls reach your server **from**
Anthropic's *outbound* range, so any allowlist must include it. Current values
(platform.claude.com/docs/en/api/ip-addresses, checked 2026-10-20):

- **Outbound IPv4 (MCP tool calls to external servers): `160.79.104.0/21`** —
  this is the documented outbound range, current as of the check above.
- Inbound IPv4 `160.79.104.0/23`, inbound IPv6 `2607:6bc0::/48` (these are for
  *incoming* connections to Anthropic, not relevant to hermcp).
- Phased-out (remove from any old rules): `34.162.46.92/32`,
  `34.162.102.82/32`, `34.162.136.91/32`, `34.162.142.92/32`,
  `34.162.183.95/32`.
- Claude.ai MCP egress is **IPv4-only** (no IPv6 egress for custom connectors),
  so an IPv6-only allowlist would break it.

Note: allowing `160.79.104.0/21` means *any* Claude.ai user's connector traffic
comes from that range — IP allowlisting alone is **not** a substitute for
hermcp's own per-key auth. Treat the IP range as a coarse network filter, not an
auth layer.

**Rotating a key without downtime**
- `hermes hermcp keys rotate <id-or-name>` issues a new secret for the *same*
  key id (`cli.py`), keeping the key's scopes/audit history intact. The old
  token is invalidated; update the client with the new token (shown once).
- To avoid a hard cutover for a live client, create a *second* key with the same
  scopes (`keys create`), switch the client to it, then revoke the old one.
  Either way there is no server restart needed — the key store is re-read per
  request (`keys.py`), so rotation is zero-downtime.

---

## 6. Open questions / risks

1. **newt-tunnel Host passthrough is inferred, not measured.** The Traefik
   config is proven to preserve Host (default `passHostHeader:true`, no
   `setHostHeader`), but the final hop is the Pangolin "newt" tunnel (hermes
   runs `pangolin up`, site type `newt`). The tunnel is HTTP passthrough and is
   expected to preserve headers, but this was not empirically confirmed on this
   host. **Verify after deploy**: `curl -H 'Host: mcp.example.com'
   http://127.0.0.1:9230/health` should return 200 (Host allowlisted), and a
   request with a bogus Host should 403. If public requests 403 with "host not
   allowed", the tunnel is rewriting Host — then add the upstream form to
   `HERMCP_ALLOWED_HOSTS` or set Pangolin's "Set Host Header" to
   `mcp.example.com` explicitly.

2. **`/health` is not publicly reachable via `/mcp`.** `GET
   https://mcp.example.com/health` hits the dashboard, not hermcp. Remote
   monitoring must either (a) add a second `/health` prefix target → 9230, or
   (b) monitor on the host directly (loopback). Recommended: (b), using
   Uptime-Kuma on the homelab.

3. **MCP Streamable-HTTP session semantics.** hermcp is deliberately stateless
   (no `Mcp-Session-Id` dependence). This is a known-good fit for proxies that
   drop session ids, but confirm Claude.ai custom connectors and Codex both
   tolerate a stateless server (they should — the server answers `DELETE` with
   200 and sends no SSE stream).

4. **`HERMES_API_KEY` and the 8642 API.** The exact scoping/format of the key
   the Hermes control API (127.0.0.1:8642) expects was not verified in this
   research; confirm with the Hermes api_server before deploying.

5. **Target address form.** For `newt` sites Pangolin maps the target's `port`
   to an allocated `internalPort` on the tunnel subnet
   (`target/createTarget.ts:400-423`). The UI "Address" field is
   `Host` + `Port`; `127.0.0.1` + `9230` is correct because the `pangolin` CLI
   runs on the same host as hermcp. If the tunnel client ever moves off-host,
   the Host field must change to the LAN address of hermes.

---

## 7. Sources

Pangolin source (`fosrl/pangolin`, main):

- `server/lib/traefik/rule.ts` — path clause + router priority (`appendPathMatch`,
  `computeRoutePriority`).
- `server/lib/traefik/getTraefikConfig.ts` — router/service/middleware assembly,
  `setHostHeader`, no explicit `passHostHeader` (Traefik default = true).
- `server/lib/traefik/loadBalancer.ts` — upstream `url` construction, `newt` vs
  `local`/`wireguard` site types.
- `server/lib/traefik/headersMiddleware.ts` — `setHostHeader` override.
- `server/lib/traefik/TraefikConfigManager.ts` — `badger` plugin middleware
  definition.
- `server/lib/readConfigFile.ts` — default auth header/cookie names
  (`P-Access-Token-Id`, `P-Access-Token`, `p_token`, `p_session_token`).
- `server/routers/badger/verifySession.ts` — `Authorization` checked only for
  `Basic` auth.
- `server/routers/target/createTarget.ts` — `internalPort` allocation for
  `newt` sites.

hermcp source (local):

- `~/.hermes/plugins/hermcp/server.py` — `host_ok`, `client_ip`,
  `authenticate` (Bearer).
- `~/.hermes/plugins/hermcp/settings.py` — env keys, defaults, allowlist
  auto-append.
- `~/.hermes/plugins/hermcp/cli.py` — `serve` entrypoint (uvicorn,
  `proxy_headers`/`forwarded_allow_ips`).
- `docs/DESIGN.md` — venv path, stateless design, routes.

Anthropic IP ranges:

- https://platform.claude.com/docs/en/api/ip-addresses (outbound
  `160.79.104.0/21`, inbound `160.79.104.0/23` + `2607:6bc0::/48`, phased-out
  `34.162.*/32` list).
- https://github.com/anthropics/claude-ai-mcp/issues/1121 — Claude.ai MCP egress
  is IPv4-only.

Pangolin docs:

- https://docs.pangolin.net — resource/target model (path, rewrite, priority,
  custom headers).
