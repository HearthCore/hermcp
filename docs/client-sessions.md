# Client sessions

The ledger of `client_session` handles: attribution, the foreign-run guard, recovery and claiming unclaimed runs.

[← README](../README.md) · [docs index](../README.md#documentation)

---

hermcp keeps the ledger of valid `client_session` handles. A session registers once with `session_card_set`, then
passes the handle to `chat_send`, `chat_run_steer` and `chat_run_cancel`. What follows from that:

- Runs are attributed: the handle is stored with the run, shown by `runs_active` and written to the audit log, next to
  the `client` label hermcp derives from the request headers. A handle that hermcp did not issue to this key is
  refused (`unknown_client_session`, `forbidden`). Without a handle `chat_send` still works and answers with a `hint`.
- Accident protection, not security: steering, cancelling or interrupting a run that a *different* registered session
  started is refused with `foreign_session_run` unless `force=true`; the override is echoed as `forced`. Runs with no
  recorded session are not guarded. Reading is never restricted.
- Recover: a new chat that continues earlier work calls `session_card_set recover_from=<old handle>`. Without
  `confirm=true` it only previews (card, open runs, a warning if the old session was active in the last 10 minutes).
  With it the new session gets a **new** handle that points back (`continues`), the old session's open runs move to it,
  and the old handle is retired (`taken_over_by`; using it answers `session_handed_over`). Same key only, once per
  handle; finished runs keep the old handle, so history stays attributable.
- Situation report: `runs_active` without `client_session` adds `session_overview` — the sessions of this key, the runs
  nobody claimed and what to do next (are you one of these? `recover_from`; new? register; your own unclaimed runs?
  `claim_runs`). With your handle the overview is gone and your runs are marked `yours`. Reading stays open to everyone.
- In plain words: a run started under a registered session carries `session` with `client`, `kind`, `purpose` and one
  readable `label` ("Cowork · code · Ledger bauen") — in `runs_active` and on the run events of `inbox_poll` /
  `inbox_list`, and on failed agent runs of the Ledger page. It is read from the session card when asked, nothing is
  stored twice. A key sees this for its own sessions; admin keys see it for every key. Runs without a session carry none.
- Overview for humans: `clients_overview` answers "which client serves which session" in one call — sessions grouped by
  client with kind, purpose, role, `state` (active: seen within 15 minutes or has open runs), last seen and the open
  runs, plus `unclaimed_runs` and a short speakable `summary`. `scope=own` (default) covers this key's sessions,
  `scope=all` every key's (admin). Profile-restricted keys see only runs of their profiles. The Ledger page shows the
  same view ([ledger.md](ledger.md#clients-and-sessions)).
- Claim: `session_card_set claim_runs=[run_ids]` (or `claim_unclaimed=true`) attaches open runs that were started
  without a handle to your session (`client_session=<handle>`, or a new card if you pass none). Preview first,
  `confirm=true` applies. Runs another session owns are never claimable (`not_claimable`); that goes through recover.
- The previous owner is told: a recover writes a `session_taken_over` event, and a forced steer/cancel/interrupt of a
  foreign run writes `foreign_run_forced` (not for dry runs). Both are plugin-written inbox events (callers cannot
  push these kinds) that the session finds with its next `inbox_poll`.
- Limit: `HERMCP_SESSION_MAX_RUNS=8` open runs per session (`session_run_limit`, 0 = off).
- Cards expire after 30 days without use; at most 100 per key. Purpose and role are free labels and never change what a
  key may do.
- Which clients exist, how their handles are shaped and where a client's own session id could come from (header or
  `native_id`) is one table, `client_profiles.py`. Today no client sends a session id; when one does, naming its
  header there makes it the alias of the card with no change on the client side.

Why these scopes: none of the four can change Hermes, only the mailbox, so they carry `access=read` —
a read-only voice key can poll and ack. Producing events is the only part that lets a key write into what
another agent will read, so it needs its own opt-in flag that `*`/`admin` do not imply. A producer key is
tiny: `access=read, toolsets=[inbox], inbox_push=true`.

```bash
hermes hermcp keys create --name voice --access read --toolsets inbox
hermes hermcp keys create --name cron-events --access read --toolsets inbox --inbox-push
```

HTTP delivery for non-MCP sources (same Bearer key, same scope check, same audit log):

```bash
curl -fsS -X POST http://127.0.0.1:9230/inbox -H "Authorization: Bearer $HERMCP_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"source":"cron","kind":"info","summary":"nightly backup finished","ref":"job-42"}'
# 201 {"ok":true,"result":{"stored":true,"event":{...}}}   401 no/bad key   403 missing scope
# 400 invalid fields   413 body > 8 KB   429 rate limit (Retry-After)
```

Expose `/inbox` through the reverse proxy only if outside sources need it; a script on the same host uses
`127.0.0.1:9230`.

**Read state is per consumer, not global.** A consumer is the calling key, optionally narrowed by a
`consumer` label (`[A-Za-z0-9_.:@/-]`, max 48) so several parallel sessions on one key stay independent;
keys never share state. An ack only affects the acking consumer. A consumer's **first** poll delivers only
events younger than `HERMCP_INBOX_JOIN_WINDOW_MIN=30` minutes (`0` = only what arrives afterwards); the rest
is not loaded into the session but reported as `older_unread` (`inbox_poll include_older=true` fetches it,
`inbox_ack all=true` clears it). A consumer idle longer than the retention period starts fresh. Rows acked
by older versions stay read for everybody. Upgrade note: existing consumers get their window on their first
poll after the update, so a long backlog is summarised once instead of replayed.

Storage and limits (env, defaults): SQLite `inbox.db` (0600) in the hermcp data dir, nothing in
`gateway_state.json` or Hermes files. `HERMCP_INBOX_MAX_EVENTS=2000` (events somebody has read are dropped first, then
the oldest), `HERMCP_INBOX_RETENTION_DAYS=14` (events older than that are dropped, read or not),
`HERMCP_INBOX_RATE_PER_MIN=60` pushes per key. Rotation runs on every push.

