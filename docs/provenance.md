# Provenance, freshness and the selftest

What every answer carries in `_meta`, where the answer was really read from, and how `selftest` proves it against live references.

[← README](../README.md) · [docs index](../README.md#documentation)

---

hermcp keeps no result cache. Every dict result carries an extra `_meta` field (list-shaped results carry it beside
`result` as `meta`):

| field | meaning |
|---|---|
| `source` / `sources` | `live-inproc`, `subprocess`, `file`, `gateway-state`, `socket`, `computed` (what the answer was read from) |
| `generated_at`, `duration_ms` | unix time and cost of this call |
| `profile`, `profile_home` | the profile the call really ran against |
| `gateway_pid`, `gateway_alive`, `gateway_state`, `gateway_code_version` | the running gateway, read from `gateway_state.json` on every call |
| `hermes_version`, `hermes_commit` | the Hermes checkout on disk (install stamp) |
| `hermcp_older_than_hermes_build` | true when this server started before the Hermes build on disk: restart hermcp. `_meta.warnings` carries the hint. See `docs/stale-hermcp-after-hermes-update.md` |
| `files` | `[{path, mtime, age_s}]` for every file the answer came from |

Where the answer is computed matters. `skills_list` and `skills_view` run in a fresh interpreter per call (Hermes caches
its skill catalog for 30 s and reads `HERMES_PLATFORM` from the process environment). Config-like reads run in-process
inside `hermes_scope`, which clears Hermes' config and skills caches on entry and exit. `effective_enabled_count` per
platform is what a session on that platform sees; the global `enabled` flag is not.

`hermes hermcp selftest [--profiles a,b] [--json] [--skip-slow]` compares hermcp's answers with references from
fresh subprocesses (probes, the `hermes` CLI, files on disk) for every profile, hammers the in-process tools with
interleaved and concurrent profile switches, checks the `_meta` contract, the redaction echo and the path whitelists,
and proves in a throw-away HERMES_HOME that edits on disk are visible on the next call. Read-only; exit code 1 on any
FAIL.

### List tools: relevant by default

`skills_list`, `plugins_list`, `mcp_list`, `cron_list` and `kanban_list` share one pattern. The default answer is compact and
holds only active / relevant entries (cron also keeps jobs whose last run failed; kanban keeps open tasks); `_meta.list`
reports total, active, disabled and how many entries the default hid. `include=all` returns every entry with an explicit
status, `view=summary` only counters per category / source / state, `query`, `category`, `source`, `limit` and `offset`
filter and page (the answer always states `total`, `returned`, `truncated`), `detail=true` restores the old long form.
`profiles=all` (or a comma list) returns a matrix entry x profile (`on | off | absent`; skills add `platform_off` and
`active_in`, and `platform=api_server` makes "active" mean active on that platform). Every profile of a matrix is read
live in its own pass, never from a shared cache. Not offered on purpose: `sessions_list` (activity log, already
paged), `models_list`, `profiles_list`, `kanban_boards`, `kanban_assignees`, `secret_list`, `tools_list`.

### Profile-bound keys

`hermcp keys create --name agent-x --profile developer --access write` creates a key limited to ONE profile: read and (with
`--access write`) write inside that profile, nothing outside it. Profile-aware tools default to that profile; any other
`profile` argument is refused (`forbidden`, naming the pinned and the requested profile, no data). Global and admin tools
are refused, and the indirect paths are closed too: kanban (own tasks only, `create` forced to the profile, foreign task
ids answer like unknown ones), sessions and chat history (own `state.db`), `logs_tail` (own profile logs, worker logs of
own tasks, no shared gateway log, no audit log), cron (own store, no host scripts / no delivery into another profile's
chat), skills/profile files/plugin config/hooks/secrets (own profile only), status views (own adapters only), approvals
(own profile's socket), runs and inbox events (own profile). `hermcp keys policy` prints the full table; it also lives in
`docs/profile-bound-keys.md`. Tests: `tests/test_bound_keys.py`; the selftest checks every profile for isolation.

### Per-profile api_server keys

The chat tools reach Hermes' api_server, which authorizes `/p/<profile>/...` against that profile's **own**
`API_SERVER_KEY` (from `~/.hermes/profiles/<p>/.env`; `default` uses `~/.hermes/.env`, re-read per request, no gateway
restart needed). hermcp therefore resolves the key per profile — override `HERMCP_API_KEY_<PROFILE>` → the profile's own
`.env` → legacy global `HERMES_API_KEY` — and reports a 401 as `api_auth` naming the profile and the sources tried, never
a key value. One key copied into every profile works for exactly one of them:

```bash
hermes hermcp profile-keys status                # per profile: source, fingerprint, SHARED flag, live probe (200/401)
hermes hermcp profile-keys setup --dry-run       # preview; writes nothing
hermes hermcp profile-keys setup                 # give the missing/shared profiles their own key (interactive)
hermes hermcp profile-keys rotate --all --yes    # replace every key (timestamped backup first, re-probe)
```

`setup`/`rotate` rewrite only the single `API_SERVER_KEY=` line (mode 0600 kept, comments untouched) after backing the
file up to `~/.hermes/backups/hermcp-profile-keys-<ts>/`; `--dry-run` writes nothing, and no key is ever printed — only a
fingerprint. `doctor` reports missing/shared/rejected keys per profile with the same hint. Full reference:
[`docs/profile-keys.md`](profile-keys.md).

