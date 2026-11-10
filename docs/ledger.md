# hermcp Ledger — read-only situation page (opt-in)

One page that answers "what is going on right now" for this Hermes install, served by hermcp
itself on the same port. It is **opt-in and off by default**: without `HERMCP_LEDGER=1` the routes
do not exist (they 404).

Not to be confused with the **cron ledger** (`cron_runs` / `cron_run_output`): that is a tool for
run records, this is a page. The module is `hermcp/ledger_page.py` for the same reason.

```bash
# in the hermcp runtime env (mode 0600), or the unit's Environment=
HERMCP_LEDGER=1
HERMCP_LEDGER_AUTH=none     # no key needed; only private/loopback clients are served
systemctl restart hermcp
```

Open `http://<hermcp-host>:9230/ledger`.

## Two auth modes

| `HERMCP_LEDGER_AUTH` | What it does |
|---|---|
| `key` (default) | the page needs a hermcp key: `/ledger?key=hmcp_…` is answered with a 303 to `/ledger` and an HttpOnly cookie, so the key never stays in the URL or the history. The JSON endpoints also accept a normal `Authorization: Bearer` header. |
| `none` | no key and no cookie at all — but only for **private/loopback clients**. |

`none` is for the trusted case: a read-only page on a port that is already only reachable from your
own network. It is **not** a way to publish the page: see the guard below.

### The network guard (`AUTH=none` only)

A request is served only when the client address is loopback, RFC1918 (`10/8`, `172.16/12`,
`192.168/16`), CGNAT/Tailscale (`100.64/10`), an IPv6 ULA (`fc00::/7`) or IPv6 link-local
(`fe80::/10`). Everything else gets **403**:

* a public client address;
* **any** request carrying `X-Forwarded-For`, `Forwarded`, `X-Real-IP`, `CF-Connecting-IP` or
  `True-Client-IP`. Behind a reverse proxy the client address is the proxy's, and without a
  configured trusted proxy hermcp cannot tell a private client from a public one — so it refuses
  instead of opening. Adding a trusted-proxy allowlist is deliberately left for later;
* a request whose client address is unknown.

The ranges are listed explicitly rather than taken from `ipaddress.is_private`, which also calls
documentation ranges such as `203.0.113.0/24` private.

`none` is **safe only inside a trusted network**. Never put it behind an edge that does not strip or
overwrite forwarding headers, and never forward the port to the internet.

### Deprecated (one-day) names

The page shipped for one day as "Lagebild". These still work and are read only where the new name is
unset — **the new name always wins**:

| Old (deprecated) | New |
|---|---|
| `HERMCP_LAGEBILD` | `HERMCP_LEDGER` |
| `HERMCP_LAGEBILD_AUTH` | `HERMCP_LEDGER_AUTH` |
| `HERMCP_LAGEBILD_BASE_URL` | `HERMCP_LEDGER_BASE_URL` |
| `HERMCP_LAGEBILD_POLL_SECONDS` / `_WINDOW_HOURS` / `_CACHE_TTL` | `HERMCP_LEDGER_…` |
| `/lagebild`, `/lagebild/overview`, `/lagebild/jobs` | 308 to `/ledger…` |

### Outlook: OIDC instead of a key in the browser

The key mode exists because hermcp has no user model yet: the alternative today would be a key
stored in a browser. The intended direction is per-user OIDC for the page (sign-in against the
existing identity provider, a principal per user, scopes from groups) so `AUTH=key` can go away for
humans and `AUTH=none` stays what it is: a private-network convenience. Not implemented yet.

## Read-only, by construction

Every value comes from the registry's existing **read** tools, invoked with a principal derived from
the caller's key — or, with `AUTH=none`, a fixed internal one — always forced to `access="read"`
(`ledger_page.read_only_principal`). Write and admin tools
are refused by the same scope model that guards `/mcp`, so "this page cannot act" is a property of the
seam, not of the buttons. There is no approve, start, stop, retry or config path anywhere in it.

| Section | Tools |
|---|---|
| Gateway state, version, active agents, every platform and stale writers | `status_gateway`, `hermes_status` |
| Sessions per reachable profile: profile, platform, derived status, last activity, session id, model, link | `profiles_list`, `sessions_list` |
| Cron ledger of the last `HERMCP_LEDGER_WINDOW_HOURS` (default 24): job, status, start, duration, delivery outcome, session id, link for agent runs | `cron_runs` |
| Pending approvals (per profile), failed cron runs, failed agent runs — called out above the fold | `approvals_pending`, `cron_runs status=error`, `inbox_list kind=run_failed` |
| Cron job configuration (toggle on the page) | `cron_list include=all` |

`approvals_pending` needs the `approvals` scope; the read-only principal sets it because that scope
only *opens the listing* — answering needs write access and stays impossible.

## Session links: where they point, and why

The page links into the **Desktop app**, not the web dashboard, when it can:

| Target | URL | Source |
|---|---|---|
| Desktop app (webapp, hash router) | `{base}/?profile=<profile>#/<session-id>` | `HERMCP_LEDGER_BASE_URL`, else `webapp.public_url` from `config.yaml` |
| Web dashboard (fallback) | `{base}/chat?profile=<profile>&resume=<session-id>` | `dashboard.public_url` from `config.yaml` |
| none | — | nothing configured: the page says so and keeps the session id in the table to copy |

Why those shapes:

- The Desktop renderer uses a `HashRouter` and its routes are `'/' + sessionId`
  (`apps/desktop/src/app/routes.ts`, `sessionRoute()`); the webapp reads the profile from
  `?profile=` in `window.location.search` (`use-gateway-boot-webapp-profile`). Hence
  `/?profile=…#/<id>`.
- The web dashboard takes the session from `/chat?resume=<id>` and the profile from `?profile=`;
  a profile-less `/chat?resume=` inherits the dashboard's launch profile, so the profile is always
  explicit.
- Neither address is guessed: `HERMCP_LEDGER_BASE_URL` wins, then the two config keys, then the
  request's own origin. A session id that is not `[A-Za-z0-9_.:-]` never goes into a URL — the cell
  shows `—` and the id stays readable next to it.
- Cron runs without an agent session (`no_agent` scripts) have nothing to open and carry no link.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `HERMCP_LEDGER` | off | mount `/ledger` at all |
| `HERMCP_LEDGER_AUTH` | `key` | `key` (cookie handshake) or `none` (no key, private clients only) |
| `HERMCP_LEDGER_BASE_URL` | empty | where session links point (desktop app); empty = from the Hermes config |
| `HERMCP_LEDGER_POLL_SECONDS` | 15 | page polling interval (min 5) |
| `HERMCP_LEDGER_WINDOW_HOURS` | 24 | cron ledger / failed-run window |
| `HERMCP_LEDGER_CACHE_TTL` | 8 | server-side cache per payload, so several open tabs cost one read |

## Privacy

- No `user_id`, no `chat_context`, no session title, no message preview, no approval `request_id`
  or `session_key`. The page renders every value with `textContent`, never `innerHTML`.
- Free text (an approval's command, an error message, an inbox summary) passes through
  `redact_text` and is truncated; if that scrubbing is unavailable the text is dropped rather than
  shown raw.
- The payload is redacted once more by the registry on its way out, exactly like a tool result.
- The page is `noindex, nofollow` and is only reachable where hermcp itself is reachable — it adds
  no port, no proxy rule and no public route.

## Cost

One `/ledger/overview` is a handful of read calls (`status_gateway` and `hermes_status` are the
slow ones at ~0.6 s each; `sessions_list` is ~0.05–0.5 s per profile). With 9 profiles a cold build
is ~4 s, and the 8 s cache means a 15 s poll costs nothing extra.
