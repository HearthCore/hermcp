# Changelog

All notable changes to hermcp are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). hermcp has no tagged releases yet, so
entries are grouped by the day they landed on `main`; every line links the commit (and the pull
request, where there is one). `CONTRIBUTING.md` explains how to keep this file current.

## [Unreleased]

### Added
- `inbox_attention`: one read tool for the whole inbox classification — waiting approvals, `run_failed`/`run_lost` that nothing settled, `run_interrupted`, settled failures (with `settled_reason`, `settled_at`, `superseded_by`) and decided approvals with their decision protocol. Same rows, same counters, same profile scope a read key would see
- `HERMCP_LEDGER_VIEW_KEY`: the Ledger page renders one key's view (scopes, profile/toolset filters, `deny_tools`, read state) instead of its own read-only principal. The value is a key **ID**, never a token; an unknown, disabled or expired key is shown as an error (`view_key_unusable`, HTTP 503) and never widens to another view. Documented trade: an admin key as view key shows everyone who reaches `/ledger` everything that key sees — intentional in single-user LAN use, re-evaluate before publishing the route
- `run_interrupted`: a run the gateway stopped by a gateway/process shutdown **after it had produced output** is its own class (warning, out of the attention view and out of `failed_runs_24h`) instead of a `run_failed`
- Failed runs carry `settled_reason`, `settled_at` and `superseded_by`: a `run_failed`/`run_lost` leaves the attention view once a later run of the same session completed (`superseded_by_completed`) or also failed (`superseded_by_failure`, only the newest counts); the history keeps the row with the mark
- `debug` option on `hermes_health`, `status_gateway` and `orchestration_overview`: ignored profiles are left out of the gateway's served/parked lists by default (`hidden_profiles` counts them), `debug: true` shows the raw lists ([`ca90b0d`](https://github.com/HearthCore/hermcp/commit/ca90b0d), [#45](https://github.com/HearthCore/hermcp/pull/45))
- `HERMCP_PROFILES_IGNORE`: a host-wide list of profiles hermcp leaves out of `doctor`, `profile-keys`, `profiles_list` and the status tools; profile-targeted tools reject them with `profile_ignored`. It is not a key scope, so keys stay unrestricted ([`acd5774`](https://github.com/HearthCore/hermcp/commit/acd5774), [#44](https://github.com/HearthCore/hermcp/pull/44))
- The Ledger has a real navigation with four sub-pages: `/ledger/sessions`, `/ledger/runs`, `/ledger/cron` and `/ledger/gateway`, each a dense read-only ledger table with paging, filters (status, profile, surface, time range, free text) and sorting by activity. The history lives here: settled and superseded failures, restart interruptions, decided approvals with their decision protocol and surface — all from the shared classification (`inbox_attention` / `attention.classify`), never a second opinion
- `/ledger/session/<id>`: the session full view — breadcrumb, stat strip (state, messages, tool calls, runtime, open approvals), the history with tool calls as their own rows (results truncated, expandable), metadata, tool usage as bars and signals. Works for sessions, runs (`session_id`) and cron runs (their session)
- A detail pop-up on every row of the attention area and the lists: a centred `<dialog>` (Esc, backdrop click, focus trap, focus returns to the row) with symbol, title, type chip, four facts, the last three actions and the session/app links. Data comes from the new read-only `GET /ledger/api/peek?kind=&id=`, truncated and masked server-side
- `GET /ledger/api/list?kind=sessions|runs|approvals|cron`, `GET /ledger/api/session/<id>` and `GET /ledger/api/gateway`: the read-only payloads behind the sub-pages, same auth and view-key seam as `/ledger/overview`
- `HERMCP_LEDGER_DESKTOP_URL` / `HERMCP_LEDGER_WEB_URL`: templates with `{session_id}` and `{profile}` for the “In Desktop-App” / “In Web-App” buttons; only `http(s)` is accepted and a button without a URL is not rendered
- The attention panel disappears when nothing is open: no frame, no empty lists, no "Clear errors" — one dim line `✓ zero active interruptions` instead, and the lists get the room. The interruption block is its own `panel-interrupted`. The KPI strip and the headline numbers stay exactly the counts `hermes_status` reports; acking stays out of the page (read-only)
- The Ledger keeps its view in the URL the way the portfolio does: `?peek=<kind>:<id>` for the pop-up, the filters, `offset`, `more=…` for expanded lists and `jobs=1`; back/forward rebuild the view, closing the dialog drops `peek`, a shared `?peek=…` link opens it after load (an unknown id shows the dialog's error state), and a "⧉ Link kopieren" button in the pop-up and the session view copies the address (clipboard API, selected read-only field as the LAN/http fallback). Only ids and filter values reach the URL
- "✦ Ask Claude" in the pop-up footer and the session view: a plain link (`rel=noopener`, new tab) that opens Claude with a German question built server-side per entry kind (waiting approval, `run_failed`, `run_interrupted`, cron, session). The question carries ids, kind, state, times, profile/surface and at most a 120-char masked command prefix, and points Claude at the hermcp tools for the details — no message bodies, no tool output, no secrets. `HERMCP_LEDGER_ASK_CLAUDE_URL` (default `https://claude.ai/new?q={prompt}`) and the optional `HERMCP_LEDGER_ASK_CLAUDE_DESKTOP_URL` (no default, scheme never guessed); only `http(s)`/`claude`, URL capped at ~1800 chars, button hidden when unset. Read-only: the page never sends anything
- The page ships its own stylesheet and two font subsets (JetBrains Mono, Sora; OFL, latin `woff2`, ~65 KB) from `/ledger/assets/…` — no external request, no build chain, no new runtime dependency

### Changed
- `inbox_attention` cuts its two closed lists (`settled`, `history`) to the newest `history_limit` rows (default 5, 0–200; `settled_total`, `history_more` and `more` say when there is more). Open work (`waiting`, `attention`, `interrupted`) is never cut. Before, a call for "what needs attention" returned every decided approval of the window with its full command text; the Ledger page is unaffected (it draws open work only) ([#60](https://github.com/HearthCore/hermcp/pull/60))
- Agent-facing wording, reviewed from the client side: the `initialize` instructions gain one line for the orchestration path (who can do work, `chat_send` vs `kanban_create`, how to track, other sessions' runs are not yours to fix) and define "client session" and "ledger"; the kanban tools say when they start work and how to follow a task, with parameter descriptions on `kanban_create`; `orchestration_overview` names the dispatch config and points to `clients_overview`; `hermcp_restart` says what a caller sees instead of the systemd-timer internals; the "(changed: it used to …)" notes are gone from five list tools; `session_overview.next` of `runs_active` only mentions recover/claim when there is something to recover or claim ([`222116b`](https://github.com/HearthCore/hermcp/commit/222116b), [#60](https://github.com/HearthCore/hermcp/pull/60))
- `hermes_search_tools` finds tools by concept: search-only keywords (`SEARCH_KEYWORDS`) make "ledger", "orchestration", "orchestrator", "delegate" and "worker" lead to the session cards, `clients_overview`, `orchestration_overview`, `chat_send` and the kanban tools; an empty result or a cut list carries a `hint` ([`222116b`](https://github.com/HearthCore/hermcp/commit/222116b), [#60](https://github.com/HearthCore/hermcp/pull/60))
- The Ledger page draws **open work only**: the collapsed group “Erledigt” (settled failures, superseded cron runs, `operator_backfill` rows) and the “Freigabe-Historie” (decided approvals with surface, time and lost second attempts) are gone from the page. Nothing is deleted — `inbox_attention`, `inbox_list detail=true` and `approval_detail` still return the rows with their `settled_reason` / decision protocol
- `run_interrupted` keeps its calm block only while it is the open state of its session; an interruption whose session later finished, failed or was interrupted again is history and leaves the page (read-only derivation in `Inbox.attention_view`, nothing written)
- Every growing list (cron runs, sessions, open runs) is cut **server-side** to a window of 10 rows (`HERMCP_LEDGER_LIST_LIMIT`, per-list `HERMCP_LEDGER_CRON_LIMIT` / `_SESSIONS_LIMIT` / `_RUNS_LIMIT`, 3..100, invalid values fall back to the default and are logged). “Mehr anzeigen (N weitere)” re-reads the rest through `/ledger/overview?more=…` instead of shipping it to the browser up front
- Ledger sections are stable panels (`panel-kpis`, `panel-attention`, `panel-interrupted`, `panel-gateway`, `panel-clients`, `panel-sessions`, `panel-cron`, `panel-jobs`) so the layout can be rearranged without touching the render logic; no visual change

### Fixed
- Ledger, from the live acceptance (four defects the unit tests could not see, each with a regression test): the sessions panel passed a plain object where the row builder expects a DOM node, which killed the whole start page on `appendChild`; `route_of` returned `session` but `PAGES` did not contain it, so `/ledger/session/<id>` silently rendered the attention page; a list total was the page size, so a filtered list claimed `1–25 von 25` with hundreds behind it; and the pop-up's "letzte Aktivität" was empty because `sessions_get` names the field `last_activity_at` while `sessions_list` calls it `last_active`
- A test that builds its settings with a temp `HERMES_HOME` can no longer delete the live key store: `Settings.data` resolves inside the instance, `tests/test_settings_data_dir.py` pins that (including "the live dir is never an ancestor of a test's data dir"), and an autouse fixture compares the live key **ids** before and after every test
- `Settings.data` resolves inside the instance's own `HERMES_HOME` instead of re-reading `os.environ` on every access. A test that built settings with a temp home and cleaned up with `shutil.rmtree(settings.data)` therefore deleted the live key store of a running server; `tests/test_settings_data_dir.py` and an autouse fixture that compares the live key ids before and after every test hold that down ([#52](https://github.com/HearthCore/hermcp/pull/52))
- The dry-run of `kanban_create` (and every other argv echo) no longer shows `--idempotency-key` as `[redacted]`: the flag list treated `idempotency-key` as a secret flag (the bare `key` word plus a swallow-all flag prefix), and the key-name layer masked `idempotency_key`, `request_key`, `approval_key` and `session_key` under the strict profile. These names are ids, not secrets — they and `client_session` now stay readable, real secrets (`--api-key`, `token=`, `API_SERVER_KEY`, ...) keep the masking

### Tests
- Make the Ledger fixtures relative to the current time instead of fixed dates, and keep `HERMCP_DEBUG_CAPTURE` from a local `.env` out of the default-behaviour tests

_Add your entry here, in the same pull request that makes the change._

### Added
- One inbox row per approval instead of a pending/resolved pair: the state moves from `waiting` to `approved`, `denied` or `expired`, on the surface that decided it and with the time
- `approval_detail` — one approval plus its decision protocol: every later attempt that lost is kept as `too_late_already_decided` with its own surface and time
- `approval_user_id` / `approval_chat` on approvals, reserved for a future OIDC/user/group model; filled only when Hermes actually supplies an id
- `hermes_status` lists the open approvals themselves (with their `approval_key`), not just a count

### Changed
- The Ledger page takes its attention section from `inbox_attention`, the same classification `hermes_status` and `inbox_poll` use, instead of reading `inbox_list kind=run_failed` on its own. A settled failure now leaves "Braucht Aufmerksamkeit" and the header counter, and stays readable in one collapsed group "Erledigt" together with superseded cron runs — with what settled it and a link to the run that took over
- Decided approvals (`approved`, `denied`, `expired`) are a collapsed "Freigabe-Historie" on the page with surface, time and the second attempts that lost; waiting approvals stay the only approvals in the attention area
- `run_interrupted` gets its own calm block on the page ("Durch Neustart unterbrochen"), not an error row
- The page's header numbers (`Fehlschläge 24 h`, `offene Freigaben`) come from `attention.digest`, so they match `hermes_status` for the same data; browser-side dismiss only subtracts what that browser hid and never re-adds a settled row
- `hermes_status` and `inbox_poll` default to what needs attention: open approvals, failed/lost runs, runs waiting for approval. Resolved approvals, the decision protocol and the old hook pairs leave the digest; `detail=true` and the new `approval_key` / `run_id` / `session_key` filters reach them
- `hermes_status.inbox.failed_runs_24h` counts only failures that are still open **and** unread for the asking consumer, so an acknowledged or superseded failure no longer shows up there
- The run watcher emits no `needs_approval` events any more — an approval is a row, and the watcher closes it when the run ends

### Fixed
- Ledger, from the live acceptance (four defects the unit tests could not see, each with a regression test): the sessions panel passed a plain object where the row builder expects a DOM node, which killed the whole start page on `appendChild`; `route_of` returned `session` but `PAGES` did not contain it, so `/ledger/session/<id>` silently rendered the attention page; a list total was the page size, so a filtered list claimed `1–25 von 25` with hundreds behind it; and the pop-up's "letzte Aktivität" was empty because `sessions_get` names the field `last_activity_at` while `sessions_list` calls it `last_active`
- A test that builds its settings with a temp `HERMES_HOME` can no longer delete the live key store: `Settings.data` resolves inside the instance, `tests/test_settings_data_dir.py` pins that (including "the live dir is never an ancestor of a test's data dir"), and an autouse fixture compares the live key **ids** before and after every test
- `Settings.data` resolves inside the `HERMES_HOME` the instance was built with, not inside whatever the process has set at the moment it is read. An instance created with an explicit env (tests, `doctor` runs, an embedded server) used to point at the live `~/.hermes/plugin-data/hermcp` anyway, so a caller that cleaned up `settings.data` deleted the running server's key store and every request answered 401 until it was restored. `load_settings(env)` also stopped dropping the process environment whenever a dict was passed, which is what the precedence in its docstring always said
- The Ledger showed `run_failed` events that were already settled (`superseded_by_*`, `operator_backfill_*`) under "Braucht Aufmerksamkeit", because it read the raw event list while the tools applied the attention predicate. Page and tools now answer from one classification, and a parity test asserts equal numbers for the same data
- A failed run no longer stays in the attention view forever: a later run of the same session that completed (or also failed) settles it, and one-time backfills settle the rows that were open when this landed (idempotent, nothing deleted, the reason stays on the row)
- An approval that was never answered no longer hangs forever: a run that reaches `completed`, `failed`, `interrupted`, `cancelled` or `run_lost` closes its still-waiting approvals as `expired`
- Legacy `needs_approval` rows whose run was already over, and legacy pending/resolved pairs, are folded into single approval rows by a one-time backfill on start

### Documentation
- The approval docs spell out how the two hook signals are correlated (a derived key, because the hook payload carries no `request_id`) and where a second decision attempt is *not* visible to hermcp (a second click inside a Hermes UI)

### Added
- This changelog, backfilled from the first 44 commits, and the rule to update it in every pull request ([#41](https://github.com/HearthCore/hermcp/issues/41))

## 2026-11-10

### Added
- Opt-in read-only situation page on hermcp's own port ([`5ad2805`](https://github.com/HearthCore/hermcp/commit/5ad2805), [#29](https://github.com/HearthCore/hermcp/pull/29))
- Key-less mode for private networks, and rename Lagebild -> Ledger ([`6445c8a`](https://github.com/HearthCore/hermcp/commit/6445c8a), [#31](https://github.com/HearthCore/hermcp/pull/31))
- `HERMCP_TRUSTED_PROXIES` — one place that decides who the client is ([`9008161`](https://github.com/HearthCore/hermcp/commit/9008161), [#33](https://github.com/HearthCore/hermcp/pull/33))
- Pass model, provider and model_options per Hermes run ([`b67d257`](https://github.com/HearthCore/hermcp/commit/b67d257), [#32](https://github.com/HearthCore/hermcp/pull/32))
- Dismiss failures in the view, and mark superseded cron runs ([`f896491`](https://github.com/HearthCore/hermcp/commit/f896491), [#34](https://github.com/HearthCore/hermcp/pull/34))
- Runs and run events name their client and purpose in plain words ([`c982b56`](https://github.com/HearthCore/hermcp/commit/c982b56), [#37](https://github.com/HearthCore/hermcp/pull/37))
- `clients_overview` tool and a Clients block on the Ledger page ([`9ad50d5`](https://github.com/HearthCore/hermcp/commit/9ad50d5), [#39](https://github.com/HearthCore/hermcp/pull/39))
- Kanban link for dashboard mode ({base}/kanban?profile=default) ([`c8db19d`](https://github.com/HearthCore/hermcp/commit/c8db19d), [#40](https://github.com/HearthCore/hermcp/pull/40))

### Fixed
- `hermes_status` counts the served profile; overview lists the default board without board.json ([`809165e`](https://github.com/HearthCore/hermcp/commit/809165e), [#27](https://github.com/HearthCore/hermcp/pull/27))
- Absolute endpoint paths and drop the key from the address ([`6aef7aa`](https://github.com/HearthCore/hermcp/commit/6aef7aa), [#30](https://github.com/HearthCore/hermcp/pull/30))
- Run without PyYAML — one yaml surface over PyYAML or ruamel.yaml ([`3314467`](https://github.com/HearthCore/hermcp/commit/3314467))

## 2026-11-09

### Added
- Run ledger and full output by run id ([`d491896`](https://github.com/HearthCore/hermcp/commit/d491896), [#28](https://github.com/HearthCore/hermcp/pull/28))

## 2026-11-07

### Added
- Opt-in request capture to discover client session identifiers ([`1ca0574`](https://github.com/HearthCore/hermcp/commit/1ca0574), [#13](https://github.com/HearthCore/hermcp/pull/13))
- Track read state per consumer instead of globally ([`851858c`](https://github.com/HearthCore/hermcp/commit/851858c), [#14](https://github.com/HearthCore/hermcp/pull/14))
- `hermes_status` level-0 digest, slim hermes_health default, count-only ack all ([`3ab4bf0`](https://github.com/HearthCore/hermcp/commit/3ab4bf0), [#15](https://github.com/HearthCore/hermcp/pull/15))
- Bearer-token fingerprint + report grouped by x-anthropic-client ([`5f79145`](https://github.com/HearthCore/hermcp/commit/5f79145), [#16](https://github.com/HearthCore/hermcp/pull/16))
- Run progress from the gateway; own vs other work in hermes_status ([`2693f55`](https://github.com/HearthCore/hermcp/commit/2693f55), [#17](https://github.com/HearthCore/hermcp/pull/17))
- Attribute runs to a client and a client-chosen session handle ([`f33ffd2`](https://github.com/HearthCore/hermcp/commit/f33ffd2), [#18](https://github.com/HearthCore/hermcp/pull/18))
- Ledger of client sessions (cards, handle shaping, foreign-run guard, recover, limit) ([`6a4a481`](https://github.com/HearthCore/hermcp/commit/6a4a481), [#19](https://github.com/HearthCore/hermcp/pull/19))
- Session situation report, claim of unclaimed runs, notices to the former owner ([`a0e4143`](https://github.com/HearthCore/hermcp/commit/a0e4143), [#20](https://github.com/HearthCore/hermcp/pull/20))
- Keys rename and keys scopes ([`0dde080`](https://github.com/HearthCore/hermcp/commit/0dde080), [#21](https://github.com/HearthCore/hermcp/pull/21))

### Fixed
- Park on invalid config, keep doctor output to one line ([`70876f0`](https://github.com/HearthCore/hermcp/commit/70876f0), [#12](https://github.com/HearthCore/hermcp/pull/12))

### Documentation
- Rewrite install flow, document the `hermes hermcp` CLI, refresh features ([`12a9ca1`](https://github.com/HearthCore/hermcp/commit/12a9ca1), [#22](https://github.com/HearthCore/hermcp/pull/22))
- Split the README into docs/, add the architecture diagram ([`521cf58`](https://github.com/HearthCore/hermcp/commit/521cf58), [#24](https://github.com/HearthCore/hermcp/pull/24))

## 2026-11-06

### Added
- Configurable systemd managers for the gateway and for hermcp itself ([`0a11fc0`](https://github.com/HearthCore/hermcp/commit/0a11fc0))

### Fixed
- Start the approvals bridge once the gateway is up, not on the first message ([`6bda391`](https://github.com/HearthCore/hermcp/commit/6bda391))
- An empty served_profiles roster means the gateway serves its own home ([`dc2d61e`](https://github.com/HearthCore/hermcp/commit/dc2d61e))
- Report an invalid systemd scope readably instead of a traceback ([`b291536`](https://github.com/HearthCore/hermcp/commit/b291536))

### Documentation
- Document scripts/run_tests.sh and fix the stale test command ([`084ca43`](https://github.com/HearthCore/hermcp/commit/084ca43))
- Document HERMCP_SYSTEMD_SCOPE and the approvals bridge autostart ([`620d5e5`](https://github.com/HearthCore/hermcp/commit/620d5e5))

### Tests
- Add scripts/run_tests.sh for a reproducible test environment ([`2eddb27`](https://github.com/HearthCore/hermcp/commit/2eddb27))

## 2026-11-05

### Added
- Agents_live tool and running-agents preview for restarts ([`ee27296`](https://github.com/HearthCore/hermcp/commit/ee27296), [#10](https://github.com/HearthCore/hermcp/pull/10))

## 2026-11-04

### Added
- Install the auto-restart units by default and detect the Hermes checkout ([`d64fb1b`](https://github.com/HearthCore/hermcp/commit/d64fb1b), [#5](https://github.com/HearthCore/hermcp/pull/5))
- Resolve the api_server key per profile and add `profile-keys` ([`47d5012`](https://github.com/HearthCore/hermcp/commit/47d5012))
- Approvals bridge for a multiplex gateway (mode detection + profile filter) ([`381b117`](https://github.com/HearthCore/hermcp/commit/381b117))

### Fixed
- Count per-profile approvals on the shared multiplex socket ([`074e0af`](https://github.com/HearthCore/hermcp/commit/074e0af))

### Documentation
- Note AGENTS.md setup flow tested with GitHub Copilot CLI ([`93f6244`](https://github.com/HearthCore/hermcp/commit/93f6244))
- Fix stale tool counts and git clone placeholder in README ([`09e7a8a`](https://github.com/HearthCore/hermcp/commit/09e7a8a), [#4](https://github.com/HearthCore/hermcp/pull/4))

### Tests
- Assert the api_server key rows, not doctor's global exit code ([`fb199be`](https://github.com/HearthCore/hermcp/commit/fb199be))
- Cover the refusal over the real bridge socket (integration) ([`bfb5a7f`](https://github.com/HearthCore/hermcp/commit/bfb5a7f))

## 2026-11-03

### Changed
- Initial public release of hermcp ([`6d30221`](https://github.com/HearthCore/hermcp/commit/6d30221))

### Documentation
- Replace hard-coded home paths with generic wording ([`5a9974c`](https://github.com/HearthCore/hermcp/commit/5a9974c))
- Add AGENTS.md setup guide for coding agents ([`5c5eff7`](https://github.com/HearthCore/hermcp/commit/5c5eff7))
