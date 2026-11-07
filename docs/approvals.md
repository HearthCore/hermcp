# Approvals out-of-process for hermcp

Research artifact for the `hermcp` MCP server. Question under test: can a **separate process**
list pending approval requests of running Hermes sessions and answer them, without patching
Hermes core? Short answer: only for runs that process itself created via the API server. There
is no out-of-process read path for an arbitrary chat-gateway session's pending approvals.

---

## 1. How approvals work inside Hermes

Approvals are held **in-process memory only**. There is no sqlite table, file, or durable
record that stores a pending approval request. Two complementary in-memory structures, plus one
durable ledger that deliberately does *not* contain approvals.

### 1a. Legacy blocking queue — `tools/approval.py`

The core registry used by chat platforms (Telegram etc.):

- `_gateway_queues: dict[str, list]` — `session_key -> [_ApprovalEntry, ...]` (`tools/approval.py:122`)
- `_gateway_notify_cbs: dict[str, object]` — `session_key -> callable` (`tools/approval.py:123`)

An `_ApprovalEntry` carries:

- `.data` — the request dict (the key fields below)
- `.event` — a `threading.Event` the waiting agent thread blocks on
- `.result` / `.reason` — the resolved choice / deny reason
- `.cancelled` / `.acknowledged` / `.settle`

Key fields of a request (source: `ApprovalRequest` dataclass,
`hermes_cli/approval_transport.py:41`):

| field | meaning |
|---|---|
| `request_id` | `uuid4().hex`, correlates answer → request |
| `digest` | sha256 over canonical fields (anti-stale binding) |
| `command` | the flagged shell command (redacted on egress) |
| `description` | human summary |
| `pattern_key` / `pattern_keys` | which detection pattern flagged it |
| `surface` | originating surface |
| `timeout_seconds` | default 300 (`approvals.timeout`) |
| `allowed_choices` | `["once"]` + optional `session`, `always` + `deny` |

Registry functions (all in `tools/approval.py`):

- `register_gateway_notify(session_key, cb)` / `unregister_gateway_notify` (`:126`, `:133`)
- `resolve_gateway_approval(session_key, choice, resolve_all, reason, request_id)` (`:142`) — pops the FIFO entry (or the one matching `request_id`, or all), sets `.result`, sets `.event`
- `list_gateway_approvals(session_key)` (`:196`) — snapshot of unresolved entries for one session
- `submit_pending(session_key, approval)` (`:247`), `pending_gateway_approval_count()` (`:229`),
  `get_pending_gateway_approval(session_key)` (`:235`), `has_blocking_approval` (`:223`)

### 1b. Generation-bound projection — `gateway/session_pending_controls.py`

A newer projection over the same queue, used by the session-authority model:

- `PendingControls.pending: dict[prompt_id -> (route, prompt)]` (`:15`)
- prompt shape: `{kind, prompt_id, execution_generation, command, description, choices, [edit], [timeout_s]}` (`:27`)
- `register(...)` publishes `approval.request`; `register_clarify(...)` publishes `clarify.request` (`:38`, `:51`)
- `snapshot(session_id, generation)` prunes stale entries and returns live prompts (`:53`)
- `respond(session_id, generation, prompt_id, response, kind)` → calls `resolve_gateway_approval` / `resolve_gateway_clarify` (`:66`)

### 1c. Durable ledger — `state.db` (sqlite), table `session_admissions`

The only durable store in play is the **admission ledger**, not approvals. File:
`~/.hermes/state.db` (one per profile home — `profiles/<name>/state.db`). Table schema
(verified via `sqlite3 .schema`):

```
session_admissions(
  seq INTEGER PK AUTOINCREMENT,
  admission_id TEXT UNIQUE, request_id TEXT, principal_id TEXT,
  target_session_id TEXT REFERENCES sessions(id),
  lineage_json, payload_json, payload_digest,
  intent CHECK ('queue'|'steer'|'redirect'),
  status CHECK ('queued'|'started'|'unknown'|'terminal'),
  outcome, owner_epoch, generation)
```

- `payload_json` holds the **submitted turn payload** (prompt/steer text), **not** pending approvals.
- `.tables` confirms there is **no** approval/prompt table anywhere in `state.db`
  (`sessions`, `session_admissions`, `session_model_usage`, `hosted_room_*`, `messages*`, ...).

### 1d. SessionAuthority — `gateway/session_authority.py`

- `SessionAuthority.db` → the sqlite store (`:77`); `authority.sessions: dict[session_id -> LiveSession]` (in-memory, `:79`)
- `LiveSession.controls` is a `PendingControls` (in-memory)
- `authority.respond(actor, ref, generation, prompt_id, response, kind)` (`:789`) resolves via `live.controls.respond(...)`
- `authority.attach/detach` (`:226`) implement the subscription model; identity + permissions are a `Principal`
  carrying scopes like `session:read`, `session:approve`, `session:respond`
  (`gateway/session_authorities.py`, `gateway/session_contract.py`)

### 1e. Profile-scoped vs gateway-level

- Everything is keyed by a **session key** (`approval_session_key`). `_gateway_queues` and
  `_gateway_notify_cbs` are dicts keyed by it, so approvals are naturally per-session/per-profile.
- For `/v1/runs`, the approval session key **is the run_id** (`api_server_runs.py:791`).
- For chat platforms, the session key is the chat/topic binding (via the `approval_session_key`
  contextvar / `HERMES_SESSION_KEY`, see `tools/approval_context.py:24`).
- Important: the api_server platform is classified **unattended**
  (`_UNATTENDED_APPROVAL_PLATFORMS = {"webhook","msgraph_webhook","api_server"}`,
  `tools/approval_context.py:135`). An api_server-launched run does **not** get a human-answerable
  approval round-trip; its flagged commands fall to `approvals.unattended_mode` (default **deny**).

---

## 2. The api_server run + approval contract

Route table (`gateway/platforms/api_server.py:80-103`), all Bearer-authenticated except `/health`:

| method + path | purpose |
|---|---|
| `POST /v1/runs` | start an agent run |
| `GET /v1/runs/{run_id}` | pollable status |
| `GET /v1/runs/{run_id}/events` | SSE lifecycle stream |
| `POST /v1/runs/{run_id}/approval` | answer a pending approval |
| `POST /v1/runs/{run_id}/steer` | inject guidance |
| `POST /v1/runs/{run_id}/resolve-unknown` | resolve unknown admission |
| `POST /v1/runs/{run_id}/stop` | stop |
| `GET /v1/models`, `GET /v1/skills`, `GET /v1/toolsets`, `GET /api/sessions` | discovery |

### Auth

- `Authorization: Bearer <API_SERVER_KEY>` (`_check_auth`, `api_server.py:1486`;
  `_expected_api_key`, `:1462`).
- Configured in `~/.hermes/.env` — key **names only** (values masked):
  `API_SERVER_ENABLED`, `API_SERVER_KEY` (line 427), `API_SERVER_HOST` (lines 426-428).
  Startup guard requires ≥16 chars (`_api_key_passes_startup_guard`, `:4407`).
- `/health` is open (live probe returned `{"status":"ok","platform":"hermes-agent","version":"0.22.1"}`);
  `/v1/models` without key → **401** (live probe confirmed).
- Profiles addressed via the `/p/<profile>/` route prefix (`_handle_profile_ingress`, `:4478`);
  the active profile is carried in `_api_request_profile`.

### `POST /v1/runs` request body (`_handle_runs`, `api_server_runs.py:704`)

- `input` — required, a string or messages list (`_run_user_message`, `:654`)
- `model` — optional route
- `session_id` — optional; resumes that session, else run_id becomes the session (`:742`, `:777`)
- `instructions` — optional ephemeral system prompt (`:808`)
- `conversation_history` / `previous_response_id` — optional history (`:738`, `:741`)
- `Idempotency-Key` header — optional dedup (`:722`)
- `X-Hermes-Session-Key` header — long-term-memory scope
- returns `run_id` (`run_<uuid4 hex>`, `:763`) with status `queued` (`:792`)

### How an approval is signalled

1. The turn registers `register_gateway_notify(run.approval_session_key, approval_notify)` (`:906`).
2. On a blocking approval, `_approval_notify` (`:936`) builds the event via
   `_approval_request_event(run_id, approval_data)` (`api_server.py:114`) and calls
   `_set_run_status(run_id, "waiting_for_approval", last_event="approval.request", approval=event)` (`:939`),
   then enqueues it into the run's SSE stream.
3. Event type on the stream is **`approval.request`**, carrying a `choices` list the client may
   echo back. The raw `command` is redacted before egress (`_redact_approval_command`, #48456).
4. Client detects it either by polling `GET /v1/runs/{run_id}` for `status == "waiting_for_approval"`
   (with an `approval` payload), or by reading the SSE stream.

### `POST /v1/runs/{run_id}/approval` request body (`_handle_run_approval`, `:1291`)

```json
{ "choice": "once" | "session" | "always" | "deny", "request_id": "<optional>", "resolve_all": true }
```

- `choice` aliases: `approve`/`approved`/`allow` → `once` (`_APPROVAL_CHOICE_ALIASES`, `:1288`).
- Room-scoped grants: only `once`/`deny`, exact `request_id` required, `resolve_all` forbidden (`:1303`, `:1312`).
- Internally calls `resolve_gateway_approval(approval_session_key, choice, resolve_all, request_id)` (`:1330`).
- Response: `{"object":"hermes.run.approval_response","run_id":...,"choice":...,"request_id":...,"resolved":N}`
  (`:1341`); `409 approval_not_pending` when nothing is pending (`:1336`).
- The authority path (`_respond_authority_run`, `:1346` → `api_server_authority_runs.respond_run`) uses the
  exact shape `{request_id, execution_generation, choice}` and enforces
  `session:approve`/`session:respond` permissions + generation match (`respond_run`, `:96`).

### Run ownership

`_request_owns_run` (`:1142`): a run is controlled only by the request whose idempotency scope
(profile + API key) matches `_run_owners[run_id]` (`:1143-1146`). Ownership is therefore
profile- and key-scoped, not global.

---

## 3. Answer to question C — can a separate process do this?

### (a) Via the api_server run/approval HTTP contract — **YES, but only for runs it owns**

Full list/answer lifecycle works for a run that the hermcp process (or its client) **started
itself** via `POST /v1/runs`: poll status → `waiting_for_approval` + `approval` payload, or SSE
`approval.request`, then `POST /v1/runs/{id}/approval` with `choice` (+ `request_id`).

**Limitation:** the contract has no "list all pending approvals across arbitrary gateway
sessions" endpoint. A Telegram/chat gateway session is **not** a `/v1/runs` run — it has no
entry in `_run_approval_sessions`, and its approval session key (chat/topic id) is invisible to
the api_server. So a pre-existing chat session's pending approval cannot be listed or answered
through this HTTP surface.

### (b) By reading/writing the same sqlite state store — **NO**

`state.db` contains `session_admissions` (the run/admission ledger) and `sessions` (metadata),
but the pending-approval queue lives only in `_gateway_queues` / `PendingControls.pending`
(in-process Python dicts). There is no table, file, or durable record holding pending approval
content. An external sqlite write would not be noticed: the gateway resolves an approval by
setting an in-process `threading.Event` (`resolve_gateway_approval`, `tools/approval.py:171-175`),
not by polling a file/table.

### (c) Not possible out-of-process for arbitrary gateway sessions — closest workarounds

1. **hermcp as run owner (recommended, no patch).** hermcp submits work through `POST /v1/runs`
   and answers via `POST /v1/runs/{id}/approval`. Clean, supported, profile-scoped via
   `/p/<profile>/` + that profile's `API_SERVER_KEY`.
2. **Answer in the chat itself.** The Telegram adapter already exposes `/approve` / `/deny`
   (slash commands) that call the same `resolve_gateway_approval`. Alice steering "his own
   profile's agent" from a phone can approve/deny directly in the chat — no hermcp, no patch.
3. **In-process bridge (an extension, not core).** To let a separate process answer an
   *existing* non-run session, the gateway would need an in-process surface that wraps
   `list_gateway_approvals` / `resolve_gateway_approval` (or `PendingControls.snapshot`/`respond`)
   and exposes it over HTTP/RPC. That is a Hermes **plugin**, not a core patch. The sanctioned
   cross-principal machinery already exists (session-authority + room grants,
   `session:approve`/`session:respond`, `_room_grant_token`) but is scoped to hosted rooms /
   API runs and is still in-process.

**Bottom line:** a separate process can list/answer approvals it *owns* (option a) with zero
patching. It cannot reach an arbitrary already-running chat session out-of-process; that needs
either the chat's own `/approve` surface or a small in-gateway plugin.

---

## 4. Recommended toolset design

Backend = **(a)** (run owner) everywhere; anything touching a non-run chat session is
**(c) unsupported** and must be documented as such.

| tool | args | backend | notes |
|---|---|---|---|
| `approvals_run` | `input`, `model?`, `session_id?`, `instructions?`, `profile?` | (a) `POST /v1/runs` | returns `run_id` |
| `approvals_list` | `run_id` | (a) `GET /v1/runs/{run_id}` | surfaces `waiting_for_approval` + `approval` payload |
| `approvals_events` | `run_id` | (a) `GET /v1/runs/{run_id}/events` (SSE) | filter `approval.request` |
| `approvals_answer` | `run_id`, `choice`, `request_id?` | (a) `POST /v1/runs/{run_id}/approval` | `choice ∈ {once, session, always, deny}` |
| `approvals_pending` | `profile?` | (a) iterate hermcp's own tracked `run_id`s and poll status | cross-session list is impossible out-of-process |

### Safety rules (remote approval == remote shell execution)

1. **Default `once`, never escalate silently.** A remote "approve" must map to `once` by
   default. `session`/`always` require explicit, separate opt-in — they widen the trust window.
2. **Per-key scope flag.** Each hermcp API key carries a capability scope; the `approvals`
   toolset exists only for keys granted an `approvals` scope. No wildcard scope.
3. **Profile pinning.** A key is bound to exactly one profile (via `/p/<profile>/` + that
   profile's own `API_SERVER_KEY`). The api_server already enforces run ownership by
   idempotency scope — hermcp must not weaken it with a shared key across profiles.
4. **Audit log.** Persist every approve/deny: `run_id`, `request_id`, `choice`, `profile`,
   actor (key identity), timestamp. Mirror the api_server's own `approval.responded` event /
   `_mark_run_event` trail.
5. **No remote `resolve_all` by default.** `resolve_all`/`all` is a footgun; disable it or gate
   it behind an elevated scope.

**The one rule that must not be bypassed:** profile pinning + per-key scope. A remote key must
never answer approvals for a profile it is not explicitly bound to — answering an approval
executes a command inside that profile's agent context.

---

## 5. Open questions / risks

- **unattended_mode default-deny:** api_server runs are "unattended", so their flagged commands
  default to **deny** (`approvals.unattended_mode`). For hermcp runs to actually raise
  answerable approval prompts, the target profile needs `approvals.unattended_mode: approve` (or
  an equivalent gate config). This is a config decision, not a code change — but without it
  `approvals_list` will rarely see a prompt.
- **Shared process, separate queues:** the api_server adapter and the Telegram adapter run in the
  same gateway process but hold separate approval queues keyed by different session keys. An
  approval raised in Telegram is in `_gateway_queues[<chat_key>]`, unreachable by `run_id`.
- **Clarify vs command approvals:** clarify prompts share the `approve` permission and the
  `PendingControls`/`session_admissions` machinery. Decide explicitly whether hermcp should
  expose `clarify` answering too.
- **Room grants** are the cross-principal answer path but only for hosted rooms / API runs.

---

## 6. Multiplex vs separate gateways (the shipped bridge)

The bridge (`bridge.py`) supports both gateway layouts; nothing about them is configurable in
hermcp, they are detected per call (`bridge.resolve()`).

| | separate gateway | multiplex gateway |
|---|---|---|
| processes / homes | one gateway process per profile home | ONE process, launched from the default home (`gateway.multiplex_profiles: true`) |
| sockets | `$HERMES_HOME/plugin-data/hermcp/approvals.sock` once per profile home | exactly one, in the launch home; it lists **every** served profile's approvals |
| detection | the profile's own socket answers | the launch home's `gateway_state.json` records `served_profiles` containing the profile, the root socket answers, and no live per-profile socket exists (`gateway.multiplex_profiles: true` is only a hint when the record predates `served_profiles`) |
| profile boundary | structural: a key pinned to `researcher` can only reach researcher's socket | the session key's namespace |

The profile boundary is the session key's namespace
(`gateway/session.py::_session_key_namespace` → `agent:main:…` for the default profile,
`agent:main~:…` for a profile literally named `main`, `agent:<name>:…` otherwise;
`bridge.profile_of_session_key()` is the dependency-free mirror of Hermes'
`hermes_state_keys.profile_from_session_key`). On the shared socket:

- `approvals_pending` lists only entries whose `session_key` maps to the key's profile,
  `hidden_other_profiles` counts what was filtered out;
- `approvals_answer` refuses a `session_key` of another profile **before** calling the bridge
  (for profile-pinned keys and for an admin key with an explicit `profile`), and refuses a key
  that cannot be attributed at all;
- a key restricted to the default profile is no longer refused in multiplex mode (the filter
  narrows the socket to `agent:main:*`); in per-home mode or when the mode cannot be established
  the old refusal stands, because nothing then guarantees the socket is default-only.

Cheap/unattributable formats fail closed: `cli:…`, a bare `tg:…`, an empty namespace or a
namespace carrying `~` (other than `main~`) map to "no profile" and are never shown to a
profile-restricted key. For the same reason `approvals_hook.py` derives an event's profile from
the session key when the process home is the default one (a multiplex gateway's home says
`default` for every event, which would otherwise put every profile's approvals into the default
profile's events); a named-profile standalone gateway keeps its own home as the answer, since such
a gateway builds `agent:main:…` keys.

What an operator needs: the agent half loaded **once** in the process that runs the gateway
(`hermes plugins enable hermcp` in the default profile) — no per-profile `plugins.enabled` entry
in multiplex mode, and a gateway restart only if the plugin was not loaded yet. Verification:
`hermes hermcp doctor` prints one line in multiplex mode
(`[ok] approvals bridge: shared multiplex socket ok (gateway pid N, serves M profiles)`) or
per-profile rows otherwise, and `approvals_bridge_status` reports `mode`, `socket`, `reachable`,
`gateway_pid` and `served_profiles`. A stale per-profile socket file (e.g. from an older
separate-gateway setup) is reported as `[warn] … stale socket file`; it is never deleted by
doctor — deleting the file by hand is safe.

---

## 7. Sources

Code (read-only, `/usr/local/lib/hermes-agent`):
- `tools/approval.py` — `_gateway_queues` `:122`, `_gateway_notify_cbs` `:123`,
  `resolve_gateway_approval` `:142`, `list_gateway_approvals` `:196`, `submit_pending` `:247`,
  `pending_gateway_approval_count` `:229`
- `hermes_cli/approval_transport.py` — `ApprovalRequest` `:41`, `ApprovalDecision` `:31`,
  `ApprovalChoice` `:27`
- `tools/approval_context.py` — `_UNATTENDED_APPROVAL_PLATFORMS` `:135`,
  `_is_gateway_approval_context` `:183`, `_get_unattended_approval_mode` `:327`
- `gateway/session_pending_controls.py` — `PendingControls` `:12`, `register` `:18`,
  `snapshot` `:53`, `respond` `:66`
- `gateway/session_authority.py` — `SessionAuthority` `:72`, `self.sessions` `:79`,
  `attach` `:226`, `respond` `:789`
- `gateway/session_authorities.py` — `SessionAuthorities` `:20`
- `gateway/platforms/api_server.py` — route table `:80-103`, `_approval_request_event` `:114`,
  `_api_key` `:1123`, `_expected_api_key` `:1462`, `_check_auth` `:1486`,
  `_api_key_passes_startup_guard` `:4407`, `_handle_profile_ingress` `:4478`
- `gateway/platforms/api_server_runs.py` — `_handle_runs` `:704`,
  `_run_approval_sessions[run_id]=run_id` `:791`, `_make_approval_notify` `:932`,
  `_load_owned_run` `:1158`, `_request_owns_run` `:1142`, `_handle_run_events` `:1186`,
  `_APPROVAL_CHOICE_ALIASES` `:1288`, `_handle_run_approval` `:1291`,
  `_respond_authority_run` `:1346`
- `gateway/platforms/api_server_authority_runs.py` — `run_admission` `:35`,
  `run_projection` `:51`, `respond_run` `:96`

Live probes (read-only):
- `GET http://127.0.0.1:8642/health` → `200 {"status":"ok","platform":"hermes-agent","version":"0.22.1"}`
- `GET http://127.0.0.1:8642/v1/models` (no key) → `401`
- `sqlite3 -readonly ~/.hermes/state.db ".tables"` / `.schema` — confirms no approval table
- `~/.hermes/.env` lines 426-428 — `API_SERVER_ENABLED`, `API_SERVER_KEY`, `API_SERVER_HOST`
  (names only, values masked)

---

## Run-scoped and cross-session approvals

A remote "approve" executes a command inside an agent. Everything here needs the key's explicit
`approvals` flag (never implied by `*`), is profile-pinned and audited. Over MCP **`once`, `session`,
`deny` and `always`** can be given; `session` needs `access=admin` and, for a run, covers that run's
approval session only
(Hermes keys it by the run id and keeps it in gateway memory: a new run asks again, a gateway restart clears it;
it covers every later command of the same pattern within that run, not just the one shown). **`always`**
(permanent, rewrites the protection rules across sessions) is released **only for keys that carry both
`access=admin` and the explicit `approvals_always` scope** — `*` and `admin` never imply it, so every other key
(including an admin key without the flag) gets `choice_not_allowed` with a plain message, and even an entitled key
only gets `always` when the request itself offers it (`choice_not_available` otherwise). Because `always` widens the
protection for good, every grant lands in the audit log with the key name, the request id and the choice —
`audit_tail(key=..., tool="approvals_answer")` shows them. `resolve_all` does not exist.

- **Run-scoped** (works out of the box): `chat_send` starts a run via the Hermes api_server
  (`POST /v1/runs`); when it reports `waiting_for_approval`, answer with `chat_run_approval`.
  `request_id` is mandatory and hermcp first reads the run: only the request the RUN reports as
  pending is accepted (anything else, e.g. an id copied from an inbox text, is refused with
  `approval_not_pending`; a 409 from the gateway maps to the same code).
  Only runs hermcp itself started can be answered this way.
- **Cross-session** (Telegram chats, other API runs): Hermes keeps pending approvals only in
  the gateway process memory, so a separate process cannot see them. hermcp therefore also ships
  an **agent half**: `register(ctx)` runs inside the gateway and publishes the pending queue on a
  `0600` AF_UNIX socket (`$HERMES_HOME/plugin-data/hermcp/approvals.sock`, peer uid checked).
  In separate-gateway mode there is one socket per profile home, so profile pinning is structural;
  a multiplex gateway (one process, all profiles) publishes a single shared socket, and there the
  session key's profile namespace is the boundary: `approvals_pending` lists only the caller's
  profile and `approvals_answer` refuses a `session_key` of another profile (see AGENTS.md,
  "Multiplex vs separate gateways"). `approvals_pending` lists,
  `approvals_answer` answers by exact `session_key` + `request_id` (only choices the request
  allows). Every list/answer is audited both by the tool layer and by the bridge.

  Enabling it: `hermes plugins enable hermcp` (adds `hermcp` to `plugins.enabled`) and **restart
  the gateway** — the owner performs that restart; hermcp never restarts it for you. The bridge
  starts on its own once the gateway reports `running` in `gateway_state.json` (a short watcher in the gateway
  process, at most 10 minutes), and also with the first inbound message. It never opens a socket in a CLI or TUI
  process. `approvals_bridge_status` tells you if it is up.

