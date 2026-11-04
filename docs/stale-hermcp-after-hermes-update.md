# hermcp after a Hermes update: stale process

hermcp keeps its Python modules in memory. A Hermes update replaces the code on disk
(`/usr/local/lib/hermes-agent`), but a running hermcp keeps answering from the old modules until it
restarts. Subprocess-backed tools (for example `hermes -p X ...` calls) already see the new code;
in-process answers do not.

## Detection

`meta.build()` compares the process start time with `built_at` in
`/usr/local/lib/hermes-agent/install-stamp.json`. When the process is older:

- `_meta.hermcp_older_than_hermes_build` is `true`
- `_meta.warnings` carries the restart hint

Every tool returns `_meta`, so the warning shows up in `hermes_health` and `orchestration_overview`.

## Two ways to restart hermcp

1. **Automatic (after a Hermes update):** `hermcp-restart.path` watches `install-stamp.json`.
   Hermes writes that file atomically when an update finishes. The path unit starts
   `hermcp-restart.service`, which waits 60 s and restarts `hermcp.service` only when the stamp
   is a new build. `hermes hermcp install-service` installs these units by default (`--no-auto-restart` opts out) and
   detects the Hermes checkout instead of assuming `/usr/local/lib/hermes-agent`. Manual install and test: see
   `deploy/systemd/README.md`.
2. **On demand:** the admin tool `hermcp_restart` (needs admin scope):
   - without `confirm=true`, or with `dry_run=true`, it returns the plan only (`applied: false`)
     with the exact command and side effects;
   - with `confirm=true` it queues
     `systemd-run --on-active=3s --timer-property=AccuracySec=100ms systemctl restart hermcp.service`
     and answers first. hermcp exits and comes back about 3 s later;
   - `applied: true` is reported only after the exit code of `systemd-run` was 0 **and** the queued
     timer was confirmed `active`. A failed or vanished timer answers with `restart_failed`,
     `applied: false` and the exit code / timer state;
   - rate limit: one real restart per 60 s — consumed only when a restart was actually queued;
   - the answer contains `old_pid`, `old_started_at` and `timer_unit`. Verify success with `hermes_health`:
     `_meta.hermcp.started_at` must be greater than `old_started_at`, and
     `_meta.hermcp_older_than_hermes_build` must be `false`.

**Why `AccuracySec` is pinned.** Transient timers from `systemd-run` inherit
`DefaultTimerAccuracySec=1min`, so a plain `--on-active=3s` timer fires anywhere in a one-minute
window (measured on this host: 39 s and 59 s late). The restart then happens up to a minute after
the tool answered, which looks exactly like "the restart never happened" if you check after 45 s.
`AccuracySec=100ms` makes the announced 3 s true.

Only `hermcp.service` is ever touched. Gateway and dashboard are not restarted by either path.

## What survives a restart

- Watches and the event inbox live in SQLite and survive the restart. The run watcher re-reads
  active watches on every tick.
- In-memory state resets: the watcher's backoff timers and 404 counters. After the restart it polls each
  active run again on the next tick.
- No duplicate events: every run event has a `dedupe_key` (`<run_id>:<key>`) with a UNIQUE index in SQLite,
  so a re-check after the restart cannot push the same event twice.

## Manual fallback

Outside the agent session, from a root shell:

    systemctl restart hermcp.service
    systemctl is-active hermcp.service
