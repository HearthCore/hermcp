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

**Requests through a trusted proxy are served**: with `HERMCP_TRUSTED_PROXIES` set, the client
address comes from the proxy's forwarding chain (right to left, first untrusted address), and a
request that arrives through such a proxy is treated as authenticated by it — that is where Pangolin
SSO sits. Direct requests from public addresses stay refused.

The trade is explicit: **whoever turns off the proxy's auth for the `/ledger` route publishes it.**
hermcp cannot distinguish an authenticated from an unauthenticated proxy request; it trusts the
proxy for that decision. Keep the trusted list to exact proxy addresses (not subnets), keep the
route behind the proxy's auth, and see
[deployment.md](deployment.md#trusted-proxies--the-only-place-forwarding-headers-are-believed).

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

## Aufräumen in der Ansicht (nur im Browser)

Fehler verschwinden nicht sofort, nur weil sie erledigt sind. Die Seite bietet deshalb einen rein
lokalen Aufräum-Weg — **ohne Schreibpfad**:

* **Ausblenden** pro Eintrag, plus „Alle ausblenden“ im Block. Der Zustand liegt in
  `localStorage` (`hermcp.ledger.dismissed.v1`, Schlüssel = Run-ID), also **pro Browser** und nicht
  serverseitig. Ist der Speicher nicht verfügbar (privater Modus, Richtlinie), sagt die Seite das und
  das Ausblenden gilt nur für die aktuelle Ansicht. Beim Laden werden IDs, die das Zeitfenster
  verlassen haben, aus dem Speicher entfernt (kein Wildwuchs). Eine Zeile „N ausgeblendet“ bietet
  „anzeigen“/„wieder einblenden“ und „Zurücksetzen“. Ausblenden zählt nichts doppelt und holt keinen
  serverseitig erledigten Eintrag als Fehler zurück: die Kopfzähler kommen aus dem Digest des Servers,
  der Browser zieht nur ab, was er selbst ausgeblendet hat.

**Offene Freigaben lassen sich nicht ausblenden** — sie sind actionable; das ist Absicht.

### Was die Seite nicht mehr zeigt

Die Seite ist eine Sicht auf **offene Arbeit**, kein Archiv. Deshalb sind entfernt:

* die eingeklappte Gruppe „Erledigt“ (gesettlete Fehlschläge mit `settled_reason`, überholte
  Cron-Fehler, `operator_backfill`-Zeilen),
* die „Freigabe-Historie“ (entschiedene Freigaben mit Oberfläche, Zeitpunkt und verlorenen
  Zweitversuchen).

Die Daten bleiben unverändert in der Inbox-Datenbank und sind über die MCP-Tools erreichbar:
`inbox_list detail=true`, `inbox_attention` (liefert `settled` und `history` weiter) und
`approval_detail`. `run_interrupted` behält seinen ruhigen Block nur, solange der Lauf der offene
Zustand seiner Session ist — hat dieselbe Session danach einen terminalen Lauf (beendet, fehlgeschlagen
oder erneut unterbrochen), ist die Unterbrechung Historie und verschwindet von der Seite; in der Inbox
bleibt sie lesbar.

Die Zähler oben („Fehlschläge 24 h“, „Braucht Aufmerksamkeit“) rechnen ausgeblendete Einträge heraus.
Es gibt dafür keinen Endpunkt: die Seite bleibt strukturell read-only, der Principal bleibt
`access=read`, nichts an Hermes-Daten (Cron-Ledger, Sessions, `executions.db`) wird geändert.

## Listen: Fenster statt Vollausgabe

Alle Listen, die wachsen können, sind **serverseitig** begrenzt:

| Liste | Einstellung | Default |
|---|---|---|
| Cron-Läufe | `HERMCP_LEDGER_CRON_LIMIT` | `HERMCP_LEDGER_LIST_LIMIT` |
| Sessions | `HERMCP_LEDGER_SESSIONS_LIMIT` | `HERMCP_LEDGER_LIST_LIMIT` |
| Run-Listen (offene Fehlschläge, Unterbrechungen) | `HERMCP_LEDGER_RUNS_LIMIT` | `HERMCP_LEDGER_LIST_LIMIT` |

`HERMCP_LEDGER_LIST_LIMIT` (Default 10) ist der gemeinsame Default; ein per-Liste gesetzter Wert
gewinnt. Erlaubt sind 3 bis 100 — ein Wert außerhalb dieses Bereichs oder etwas, das keine Zahl ist,
fällt auf den Default zurück und wird im Log genannt (`hermcp.settings`). Der Server schickt nur das
Fenster plus `total`, `has_more` und `more_count`; „Mehr anzeigen (N weitere)“ lädt den Rest über
`/ledger/overview?more=cron,sessions,runs` nach — die Liste wird also beim Ausklappen erneut
serverseitig gelesen und nicht im Browser abgeschnitten. Mehr als die Abrufgrenze (100 Zeilen je
Liste, 200 Fehlschläge im Attention-Teil) liefert auch das Ausklappen nicht; das steht als
`capped`/`truncated` im Payload.

**Ausblick:** Sobald es eine Authentifizierung pro User gibt (OIDC, siehe oben), kann ein
serverseitiges Quittieren pro User und Eintrag dazukommen — dann würde das Ledger selbst aufräumen und
der Zustand wäre geräteübergreifend. Nicht Teil dieser Version.

## Eine Wahrheit für Seite und Tools

Der Ledger entscheidet **nicht selbst**, was Aufmerksamkeit ist. Diese Entscheidung liegt an genau
einer Stelle — `adapters/attention.py` (Einstufung, Text, Zähler) und `Inbox.attention_view()` (die
Abfrage) — und wird von beiden Seiten benutzt:

* die MCP-Tools `hermes_status` (`inbox.failed_runs_24h`, `inbox.approvals_waiting`), `inbox_poll`,
  `inbox_list` und das neue `inbox_attention`;
* die Ledger-Seite, die für ihren Attention-Teil `inbox_attention` aufruft.

Gleiche Einstufung, gleiche Zähler, gleiche Filter, gleiche Profil-/Scope-Sicht wie ein Read-Key,
gleiche Texte. Ein Test (`tests/test_attention_parity.py`) hält das fest: für dieselben Daten müssen
Seite und Digest dieselben Zahlen liefern.

| Einstufung | Was hineinfällt | Wo es erscheint |
|---|---|---|
| `waiting` | Freigabe, die noch auf einen Menschen wartet | Attention-Bereich (nicht ausblendbar) |
| `attention` | `run_failed` / `run_lost` ohne `settled_reason`, vom Consumer nicht quittiert | Attention-Bereich |
| `interrupted` | `run_interrupted` — ein Neustart hat den Lauf nach vorhandener Ausgabe beendet | eigener ruhiger Block „Durch Neustart unterbrochen“, kein Fehler |
| `settled` | Fehlschlag mit `settled_reason` (`superseded_by_completed`, `superseded_by_failure`, `operator_backfill_*`) | **nicht auf der Seite**; nur über `inbox_attention` / `inbox_list detail=true` |
| `history` | Freigabe mit Entscheidung (`approved`, `denied`, `expired`) | **nicht auf der Seite**; nur über `inbox_attention` / `approval_detail` |
| `noise` | `run_finished`, `run_cancelled`, rohe Hook-Paare | nur über `inbox_list` / `detail=true` |

Die Seite zeigt nur, was noch Arbeit ist: ein erledigter Fehler verlässt Attention-Bereich und Zähler;
auf der Seite erscheint er nicht mehr, in den Tools bleibt er mit dem Vermerk lesbar, was ihn erledigt hat.

## Die Sicht eines Keys (`HERMCP_LEDGER_VIEW_KEY`)

Im Single-User-Betrieb soll die Seite dasselbe zeigen wie der Agent, der sie liest. Dafür gibt es
`HERMCP_LEDGER_VIEW_KEY`: der **Wert ist die Key-ID, nie das Token**. Ist sie gesetzt, rendert der
Ledger exakt die Sicht dieses Keys — Scopes, Profil- und Toolset-Filter, `deny_tools` und den
Gelesen-Stand (Consumer) dieses Keys — über dieselbe gemeinsame Abfrageschicht wie die MCP-Tools.
Ohne die Option bleibt es beim bisherigen Verhalten (read-only-Ansichts-Principal ohne Admin-Rechte).

Ist die ID unbekannt, der Key deaktiviert oder abgelaufen, zeigt die Seite eine klare Fehlermeldung
(HTTP 503, `view_key_unusable`) und **fällt nicht** auf eine breitere Sicht zurück. Ein Key-*Name*
wird bewusst nicht aufgelöst — nur die ID.

> **Warnung — bewusste Entscheidung im LAN, vor jeder Freigabe neu bewerten.**
> Mit einem **Admin-Key** als View-Key zeigt `/ledger` jedem, der die Seite erreicht, **alles, was
> dieser Key sieht** — inklusive aller Profile. Das ist im Single-User-LAN-Betrieb absichtlich so.
> Vor dem Freigeben über Pangolin oder eine andere Route muss das zusammen mit
> `HERMCP_LEDGER_AUTH` neu bewertet werden: die Seite erbt dann die Reichweite des Keys, und wer die
> Route erreicht, erbt sie mit. Der Ledger bleibt dabei strikt read-only (keine Aktionen, kein
> Schreiben von Gelesen-Stand durch die reine Anzeige).

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
| Clients and sessions: sessions grouped by client with kind, purpose, state, last seen and open runs | hermcp's own session cards and run watches (same data as `clients_overview`) |
| Cron ledger of the last `HERMCP_LEDGER_WINDOW_HOURS` (default 24): job, status, start, duration, delivery outcome, session id, link for agent runs | `cron_runs` |
| Attention: waiting approvals, open failures, open interruptions (settled failures and decided approvals stay in the tools only) | `inbox_attention` (the shared classification), `cron_runs status=error` |
| Cron job configuration (toggle on the page) | `cron_list include=all` |

`approvals_pending` needs the `approvals` scope; the read-only principal sets it because that scope
only *opens the listing* — answering needs write access and stays impossible. The page itself reads
approvals through `inbox_attention`, so a waiting approval is counted exactly once.

## Clients and sessions

The block "Clients und Sessions" answers *which client serves which session, and what for*: one row per
session, grouped by client (Cowork, ClaudeAI, Claude Code, ...) with kind, purpose, a state (**aktiv** = seen within
15 minutes or has open runs, otherwise **ruhig**), when it was last seen, its open runs by profile and the
handle. Runs started without a session are only counted ("offene Runs ohne Session"); sessions that were
taken over are counted, not listed. It is built from hermcp's own session cards and run watches, never from
Hermes state, and cards stay labels, not permissions.

Whose sessions are listed: with a key, an admin key sees every key's sessions and any other key its own; with
`HERMCP_LEDGER_AUTH=none` (the private-network page) all of them. The same data, speakable, is the tool
`clients_overview` (see [client-sessions.md](client-sessions.md)).

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
| `HERMCP_LEDGER_KANBAN_URL` | empty | where the "Kanban-Board" link in the Clients block points. Empty = derived from the session-link target: in desktop mode `{base}/#/kanban`; in dashboard mode `{base}/kanban?profile=default`. Only `http(s)` addresses are used; it is a board link, not a per-task link |
| `HERMCP_LEDGER_POLL_SECONDS` | 15 | page polling interval (min 5) |
| `HERMCP_LEDGER_WINDOW_HOURS` | 24 | cron ledger / failed-run window |
| `HERMCP_LEDGER_CACHE_TTL` | 8 | server-side cache per payload, so several open tabs cost one read |
| `HERMCP_LEDGER_LIST_LIMIT` | 10 | window of every growing list (cron runs, sessions, run lists) before “Mehr anzeigen”; 3..100, an invalid value falls back to the default and is logged |
| `HERMCP_LEDGER_CRON_LIMIT` | value of `HERMCP_LEDGER_LIST_LIMIT` | window of the cron-runs table |
| `HERMCP_LEDGER_SESSIONS_LIMIT` | value of `HERMCP_LEDGER_LIST_LIMIT` | window of the sessions table |
| `HERMCP_LEDGER_RUNS_LIMIT` | value of `HERMCP_LEDGER_LIST_LIMIT` | window of the run lists (open failures, interruptions) |
| `HERMCP_LEDGER_VIEW_KEY` | empty | key **ID** (never the token) whose view the page renders — scopes, profile/toolset filters, `deny_tools`, read state. Empty = the read-only page principal. An unknown/disabled/expired ID shows an error instead of widening the view; see the warning above |

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
