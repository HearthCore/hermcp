# Per-profile api_server keys

hermcp's chat tools talk to Hermes' api_server. The api_server authorizes `/p/<profile>/...` against
**that profile's own** `API_SERVER_KEY` (`gateway/platforms/api_server.py::_expected_api_key`), read
live from `$HERMES_HOME/profiles/<profile>/.env` — the `default` profile reads `$HERMES_HOME/.env`.
The value must be at least 16 characters. The file is re-read per request, so **no gateway restart is
needed** after an edit.

A single shared key is therefore only accepted for the one profile whose `.env` carries it. Every
other profile answers `401 {"error":{"message":"Invalid gateway API key (API_SERVER_KEY)"}}`, which
hermcp surfaces as `api_auth` — the classic "`chat_send` works for `default` and fails for everybody
else" symptom.

`profile-keys` (this module's CLI) is the supported way to give every profile its own key, to see
which key a profile resolves right now, and to rotate one.

## Resolution order

`profile_keys.resolve()` — used by `adapters/hapi.py` for every request and by the CLI — tries
exactly these three places, highest first:

| # | Source | Where it lives | Notes |
|---|---|---|---|
| 1 | `override` | env var `HERMCP_API_KEY_<PROFILE>` — process environment first, then hermcp's own `.env` | profile name upper-cased, every non-alphanumeric character becomes `_`: `media-agent` → `HERMCP_API_KEY_MEDIA_AGENT` |
| 2 | `profile .env` | `API_SERVER_KEY` in the profile's `.env` | mtime-cached, so an edit applies on the next call without restarting hermcp |
| 3 | `global` | `HERMES_API_KEY` (hermcp's config/env, else `HERMES_API_KEY` in `$HERMES_HOME/.env`) | legacy single-key setups |

There is no fourth fallback: a key belonging to profile A is never sent for profile B. An override is
the escape hatch for setups where the profile `.env` must stay untouched (containers, read-only
mounts); it wins over the file.

Keys never leave the module as text. `status`, `setup` and `rotate` print a **fingerprint** (first 8
hex characters of the SHA-256 of the key), the resolved source and the probe result — never a key
value.

## CLI

```bash
PY=/usr/local/lib/hermes-agent/venv/bin/python     # the Hermes venv
cd ~/.hermes/plugins && env -u PYTHONPATH "$PY" -m hermcp profile-keys <cmd>
```

| Command | What it does |
|---|---|
| `profile-keys status [--profiles a,b] [--no-probe] [--json]` | one row per profile: key present?, source, fingerprint, `SHARED with <profiles>` flag, live probe (`GET /p/<p>/v1/models` → `200`/`401`/`unreachable`). Exit 1 when a profile has no working key. `--json` emits the same rows as one JSON array (nothing after it). |
| `profile-keys setup [--profiles a,b] [--dry-run]` | interactive: walks the profiles that have **no key or a shared one** and offers, per profile, *generate* (`secrets.token_hex(32)`), *copy from an existing source* or *skip*. |
| `profile-keys setup --yes --generate [--profiles a,b] [--no-probe]` | non-interactive: generate a fresh key for every target. `--copy-from <global\|profile>` copies instead. Without `--generate`/`--copy-from` a non-interactive run refuses (exit 2). |
| `profile-keys rotate <profile> \| --all --yes [--dry-run]` | same write path as `setup`: new key, timestamped backup first, re-probe, fingerprints only. Without `--yes` (and without `--dry-run`) it refuses. |

Writing rules shared by `setup` and `rotate`:

- exactly one `API_SERVER_KEY=` line is written: the existing line is replaced, otherwise appended,
  duplicates are dropped. Comments, quotes and every other line stay untouched; the file keeps mode
  `0600` (the write goes through a fresh `0600` `os.open`).
- the original file is copied to
  `$HERMES_HOME/backups/hermcp-profile-keys-<YYYYmmdd-HHMMSS>/<profile>.env` (the `default` profile is
  `default.env`), mode `0600`, before the new text is written. Creating a file that did not exist
  writes no backup.
- `--dry-run` writes nothing at all (no file, no backup) and prints `(simulated)` fingerprints.

## Workflows

### Preflight (read-only, safe to run any time)

```bash
env -u PYTHONPATH "$PY" -m hermcp profile-keys status
```

Every row `yes … 200` with no `SHARED` note means the chat tools can reach every profile. `MISSING`
or `rejected by the api_server` rows are what `setup` exists for. Probing needs the api_server
running; `--no-probe` gives the table offline.

### First install (needs owner approval)

```bash
env -u PYTHONPATH "$PY" -m hermcp profile-keys setup --dry-run     # preview, writes nothing
env -u PYTHONPATH "$PY" -m hermcp profile-keys setup               # interactive, per profile
```

`setup` writes `API_SERVER_KEY` **only** into profile `.env` files that are missing a key or share
one with another profile. After each write it re-probes that profile, so the last line tells you
whether the api_server accepted the new key. No gateway restart, no `systemctl` call, no hermcp
restart.

### Verify

```bash
env -u PYTHONPATH "$PY" -m hermcp doctor                  # per-profile rows: missing/shared/401
env -u PYTHONPATH "$PY" -m hermcp profile-keys status     # fingerprints + live probe
```

Then the real end-to-end check: a `chat_send` to a **non-default** profile from a connected client
(e.g. `hermes_call` with `chat_send`, `profile: "researcher"`). It must return a `run_id`; the old
failure mode was `api_auth` with HTTP 401.

### Change / rotate a key

```bash
env -u PYTHONPATH "$PY" -m hermcp profile-keys rotate researcher --dry-run
env -u PYTHONPATH "$PY" -m hermcp profile-keys rotate researcher --yes
env -u PYTHONPATH "$PY" -m hermcp profile-keys rotate --all --yes
```

The old key keeps working until the file is replaced; the api_server picks up the new one on the next
request. Rotate when a key leaked, when a profile moved to another owner, or when the interim
"copy one key into every profile" state has to go.

### Rollback

Restore the file from the backup directory named in the `setup`/`rotate` output:

```bash
cp -p ~/.hermes/backups/hermcp-profile-keys-<ts>/researcher.env ~/.hermes/profiles/researcher/.env
chmod 600 ~/.hermes/profiles/researcher/.env
env -u PYTHONPATH "$PY" -m hermcp profile-keys status --profiles researcher
```

The api_server re-reads the file on the next request, so the restored key is live immediately.

## Agent-guided rules

For an agent working from `AGENTS.md`:

- **Read-only, no approval needed:** `profile-keys status` (with or without `--no-probe`),
  `profile-keys setup --dry-run`, `profile-keys rotate <p> --dry-run`, `doctor`.
- **Owner approval required:** anything that actually writes a profile `.env` —
  `profile-keys setup` (including `--yes --generate`) and `profile-keys rotate`. Show the `--dry-run`
  output first and wait.
- **Never:** print, echo or paste a key or an `API_SERVER_KEY=` line; keep the fingerprint.
  Restarting the gateway or a service is not part of this workflow and must not be done.
- `~/.hermes/*` is the owner's machine state. Develop and test against a temporary `HERMES_HOME`
  (`HERMES_HOME=/tmp/pk-demo python -m hermcp profile-keys setup --dry-run`).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `api_auth` on a non-default profile, `status` shows `SHARED with …` | one key is installed in several profiles. `profile-keys setup` gives each of them its own. |
| `status` shows `MISSING key` | no `API_SERVER_KEY` in that profile's `.env`; the global `HERMES_API_KEY` is not accepted for named profiles. `profile-keys setup`. |
| `status` probe `401` although the key is present | the api_server compares against the profile `.env` you edited — check `HERMES_HOME` (rows print the exact path), and that the value is ≥ 16 characters. |
| Everything 401, `probe unreachable` | the api_server is not running/`HERMES_API_URL` is wrong. `doctor` reports reachability separately. |
| A profile needs a key but its `.env` must not be touched | set `HERMCP_API_KEY_<PROFILE>` in hermcp's `.env` (source `override`). An override counts as "present", so `setup` will not target that profile — confirm with `profile-keys status` that the probe returns 200. |
| `setup` exits 2 | non-interactive without `--generate`/`--copy-from`. |
| `rotate` exits 2 | missing `--yes` (a rotation rewrites a profile `.env`). |
