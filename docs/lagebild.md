# Lagebild — read-only situation page (opt-in)

One page that answers "what is going on right now" for this Hermes install, served by hermcp
itself on the same port. It is **opt-in and off by default**: without `HERMCP_LAGEBILD=1` the routes
do not exist (they 404).

```bash
# in the hermcp runtime env (mode 0600), or the unit's Environment=
HERMCP_LAGEBILD=1
systemctl restart hermcp
```

Then open `https://<your hermcp host>/lagebild?key=hmcp_…` once: the key is traded for an HttpOnly
cookie and never stays in the URL or the history. After that the page lives at `/lagebild` and needs
no key in the address.

## Read-only, by construction

Every value comes from the registry's existing **read** tools, invoked with a principal derived from
the caller's key but forced to `access="read"` (`lagebild.read_only_principal`). Write and admin tools
are refused by the same scope model that guards `/mcp`, so "this page cannot act" is a property of the
seam, not of the buttons. There is no approve, start, stop, retry or config path anywhere in it.

| Section | Tools |
|---|---|
| Gateway state, version, active agents, every platform and stale writers | `status_gateway`, `hermes_status` |
| Sessions per reachable profile: profile, platform, derived status, last activity, session id, model, link | `profiles_list`, `sessions_list` |
| Cron ledger of the last `HERMCP_LAGEBILD_WINDOW_HOURS` (default 24): job, status, start, duration, delivery outcome, session id, link for agent runs | `cron_runs` |
| Pending approvals (per profile), failed cron runs, failed agent runs — called out above the fold | `approvals_pending`, `cron_runs status=error`, `inbox_list kind=run_failed` |
| Cron job configuration (toggle on the page) | `cron_list include=all` |

`approvals_pending` needs the `approvals` scope; the read-only principal sets it because that scope
only *opens the listing* — answering needs write access and stays impossible.

## Session links: where they point, and why

The page links into the **Desktop app**, not the web dashboard, when it can:

| Target | URL | Source |
|---|---|---|
| Desktop app (webapp, hash router) | `{base}/?profile=<profile>#/<session-id>` | `HERMCP_LAGEBILD_BASE_URL`, else `webapp.public_url` from `config.yaml` |
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
- Neither address is guessed: `HERMCP_LAGEBILD_BASE_URL` wins, then the two config keys, then the
  request's own origin. A session id that is not `[A-Za-z0-9_.:-]` never goes into a URL — the cell
  shows `—` and the id stays readable next to it.
- Cron runs without an agent session (`no_agent` scripts) have nothing to open and carry no link.

## Settings

| Env | Default | Meaning |
|---|---|---|
| `HERMCP_LAGEBILD` | off | mount `/lagebild` at all |
| `HERMCP_LAGEBILD_BASE_URL` | empty | where session links point (desktop app); empty = from the Hermes config |
| `HERMCP_LAGEBILD_POLL_SECONDS` | 15 | page polling interval (min 5) |
| `HERMCP_LAGEBILD_WINDOW_HOURS` | 24 | cron ledger / failed-run window |
| `HERMCP_LAGEBILD_CACHE_TTL` | 8 | server-side cache per payload, so several open tabs cost one read |

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

One `/lagebild/overview` is a handful of read calls (`status_gateway` and `hermes_status` are the
slow ones at ~0.6 s each; `sessions_list` is ~0.05–0.5 s per profile). With 9 profiles a cold build
is ~4 s, and the 8 s cache means a 15 s poll costs nothing extra.
