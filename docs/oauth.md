# OAuth 2.1 for hermcp — concrete spec

This is the authoritative implementation spec for `hermcp`'s OAuth 2.1 authorization
server + protected-resource layer. Every claim below is grounded in one of three sources
(see §8): the MCP Python SDK's auth models and route builders installed under
`/usr/local/lib/hermes-agent/venv/.../mcp/server/auth/`, Hermes' own OAuth client
(`/usr/local/lib/hermes-agent/tools/mcp_oauth*.py` + `mcp/shared/auth.py`), and the
reference implementation `mlennie/hermes-mcp` (the "authorize page where the user pastes
an API key / client credentials" pattern), read in full.

`hermcp` exposes Streamable HTTP MCP at path `/mcp` on a public HTTPS host. Two auth paths
coexist, exactly as in the reference implementation:

1. **OAuth 2.1 confidential client + PKCE** — for Claude.ai custom connectors.
2. **Static bearer token** — for Codex / Cursor / scripts.

---

## 1. Endpoints hermcp must expose

| Method | Path | Purpose | Request fields | Response |
|---|---|---|---|---|
| GET | `/.well-known/oauth-authorization-server` | AS metadata (RFC 8414) | — | JSON, see §2.1 |
| GET | `/.well-known/oauth-protected-resource` | PR metadata, host-scoped (RFC 9728 §3.1) | — | JSON, see §2.2 |
| GET | `/.well-known/oauth-protected-resource/mcp` | PR metadata, path-scoped for the `/mcp` resource | — | JSON, see §2.2 (with `resource` = `<issuer>/mcp`) |
| GET | `/authorize` | Authorization endpoint | `response_type=code`, `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method=S256`, `state`, `resource` (RFC 8707), optional `scope` | 302 redirect to `redirect_uri?code=...&state=...` (and `iss` if advertised) |
| POST | `/token` | Token endpoint (auth-code + refresh grants) | form-encoded, see below | JSON token response (§2.3) |
| POST/GET/DELETE | `/mcp` | MCP Streamable HTTP endpoint | MCP JSON-RPC messages | SSE / JSON-RPC |

**`/token` request shapes** (both `application/x-www-form-urlencoded`):

Authorization-code grant:
```
grant_type=authorization_code
code=<code from /authorize redirect>
redirect_uri=<exact same URI as /authorize>
client_id=<static client_id>
client_secret=<static client_secret>          # client_secret_post
code_verifier=<the PKCE verifier>
resource=<issuer>/mcp                         # if required at /authorize, repeat here
```

Refresh grant:
```
grant_type=refresh_token
refresh_token=<stored refresh token>
client_id=<static client_id>
client_secret=<static client_secret>          # also enforced on refresh
```

**401 on `/mcp`** must carry the discovery hint header (RFC 9728 §4):
```
WWW-Authenticate: Bearer resource_metadata="https://<host>/.well-known/oauth-protected-resource/mcp"
```
Not every client parses it, so the well-known paths are still served directly.

**Endpoints deliberately absent** (matching the reference implementation):
- `/register` — Dynamic Client Registration is **disabled**. A static `client_id` /
  `client_secret` pair is pre-shared instead. Anyone POSTing to it gets `unsupported`.
- `/revoke` — revocation endpoint disabled.

---

## 2. Metadata documents: exact JSON bodies to serve

### 2.1 `/.well-known/oauth-authorization-server` (RFC 8414)

Mandatory fields are `issuer`, `authorization_endpoint`, `token_endpoint`. Serve with
`Cache-Control: public, max-age=3600`.

```json
{
  "issuer": "https://<host>",
  "authorization_endpoint": "https://<host>/authorize",
  "token_endpoint": "https://<host>/token",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["client_secret_post"],
  "scopes_supported": ["hermes"]
}
```

Critical detail (reference impl `server.py:build_http_app`): the SDK's default metadata
advertises `["client_secret_post", "client_secret_basic"]`, but the static confidential
client only accepts `client_secret_post`. Advertise **only** `client_secret_post`, otherwise
a compliant client that picks `client_secret_basic` will fail login. `scopes_supported`
may be `[]`; see §4.

### 2.2 `/.well-known/oauth-protected-resource[/mcp]` (RFC 9728)

Mandatory fields are `resource` and `authorization_servers` (min 1 entry). Two routes:
host-scoped (`resource` = `<issuer>/`) and path-scoped (`resource` = `<issuer>/mcp`).
`bearer_methods_supported` defaults to `["header"]`.

```json
{
  "resource": "https://<host>/mcp",
  "authorization_servers": ["https://<host>"],
  "scopes_supported": ["hermes"],
  "bearer_methods_supported": ["header"]
}
```

### 2.3 Token response (`/token`, RFC 6749 §5.1)

```json
{
  "access_token": "<opaque 256-bit urlsafe>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "<opaque 256-bit urlsafe>",
  "scope": "hermes"
}
```
`token_type` is title-cased `Bearer`. On error return RFC 6749 §5.2 shape, e.g.
`{"error":"invalid_grant"}` / `{"error":"invalid_client"}` (401) / `{"error":"invalid_scope"}`.

---

## 3. Token model

### Format
Opaque random strings, `secrets.token_urlsafe(32)` (~256 bits). Never JWTs — the reference
impl and the SDK's storage both use opaque tokens.

### Storage location (server-side, hermcp)
Two defensible options, pick one and document it:

- **In-memory (reference default).** Dicts keyed by token value. Survives nothing; a restart
  forces every client to re-auth. This is the reference impl's explicit trade-off: tokens
  evaporate, clients re-auth in seconds (Claude re-runs the flow because `client_id`/
  `client_secret` are saved in the connector UI). Simplest, no secret-at-rest problem.
- **Durable (recommended for hermcp if unattended).** Persist `{token_hash, client_id,
  scopes, resource, expires_at, refresh_hash}` in a 0600 SQLite/file. Store **SHA-256
  hashes** of access and refresh tokens, not the raw values — a DB read should not let an
  attacker mint auth. This survives restart so Codex/Cursor/Claude connections don't drop.

If durable is chosen, make refresh-token **rotation** mandatory (RFC 6819 §5.2.2.3): pop the
old refresh token atomically and mint a fresh pair on every refresh; a replayed refresh token
is rejected as `invalid_grant`.

### Client-credentials storage (the "hmcp key" backing each connector)
`hermcp` controls a Hermes install, so each OAuth client is minted from an underlying
Hermes API key. Persist the mapping `client_id -> {client_secret_hash, hmcp_key_id, scopes}`.
The `client_secret` must be stored only as a hash (or keep it only in the env/config and
compare constant-time via `hmac.compare_digest` against the live value).

### Expiry
- access token: **1 h** (reference `DEFAULT_ACCESS_TOKEN_TTL = 3600`)
- refresh token: **30 d** (reference `DEFAULT_REFRESH_TOKEN_TTL = 30*24*3600`), rolling on rotation
- authorization code: **60 s** (RFC 6749 §4.1.2 recommends short; reference uses 60 s)

### What Hermes itself writes (client-side schema, for completeness)
When Hermes is the *client* of a remote OAuth MCP server, `HermesTokenStorage`
(`tools/mcp_oauth.py:412`) writes two files under `$HERMES_HOME/mcp-tokens/` (dir 0700,
files 0600):

- `<server>.json` — `OAuthToken`: `{access_token, token_type:"Bearer", expires_in,
  refresh_token, scope}` plus a `hermes_issuer` bookkeeping field.
- `<server>.client.json` — `OAuthClientInformationFull`: `{client_id, redirect_uris,
  grant_types, response_types, token_endpoint_auth_method, scope, client_name}`.

hermcp doesn't have to reproduce these files; this is only relevant if we want hermcp's
output to be directly consumable by a Hermes instance acting as a client.

---

## 4. Scope model: how an OAuth token inherits the hmcp key's scopes

The clean model is **one OAuth access token = one bound hmcp key**, not a separate
scope lattice:

1. Every OAuth client is created against a specific Hermes API key ("hmcp key") that
   already encodes its own permission ceiling.
2. The access token minted for that client is **bound** to `(client_id, resource)` — the
   `resource` parameter (RFC 8707) is `https://<host>/mcp` and must be echoed back on every
   `/mcp` request's token lookup, so a token minted for `/mcp` can't be replayed elsewhere.
3. The token's `scope` field is a *sub-set-or-equal* projection of the hmcp key's
   permissions. Concretely: the AS stores, per `client_id`, the list of Hermes toolset/
   permission scopes that key is allowed to expose; the `/token` response echoes the granted
   (possibly narrowed) `scope`, and the `/mcp` middleware checks the token's stored scopes
   against the requested toolset before forwarding.

Two working patterns from the field:

- **Reference impl (single key).** `scopes=[]`, one static client, one `HERMES_API_KEY`. The
  token is a pure gate; the underlying key decides everything. Simplest, sufficient for a
  personal bridge.
- **hermcp (multi-key recommendation).** Keep a `client_id -> hmcp_key` table. At `/token`
  time, set the minted token's `scope` to exactly the scopes that hmcp key is authorized for
  (e.g. `hermes` or a finer list like `hermes:cron hermes:read`). On `/mcp`, resolve the
  access token → hmcp key → scopes, and reject tools outside that set. This gives
  least-privilege per connector without a public OAuth scope registry.

Rule that matters operationally: **write the scope the token endpoint actually granted**,
not what was requested (Hermes SKILL.md pitfall 14) — the AS may narrow it.

---

## 5. Claude.ai connector setup: exact steps + values

Evidence: reference impl `README.md` + `DEFAULT_ALLOWED_REDIRECT_URIS`.

1. Start hermcp with a static `client_id` and `client_secret` (≥32 chars; generate with a
   `mint-client` helper). DCR is off.
2. In Claude (web, mobile, or Desktop): **Settings → Connectors → Add custom connector**.
3. Paste the **server URL**: `https://<host>/mcp`.
4. Under **Advanced settings**, paste the **client_id** and **client_secret**. The secret
   must match the server's `OAUTH_CLIENT_SECRET` **exactly** — it is enforced at `/token`
   (`client_secret_post`). A mismatch surfaces as `POST /token ... 401`.
5. Claude runs the authorization-code + PKCE flow. The redirect lands on:
   `https://claude.ai/api/mcp/auth_callback` (also accept `https://claude.com/api/mcp/auth_callback`).

**The two redirect URIs to allowlist** (exact match, no wildcard):
```
https://claude.ai/api/mcp/auth_callback
https://claude.com/api/mcp/auth_callback
```

Key requirements for the Claude flow to succeed:

- **Confidential client required in practice.** The reference impl moved OFF PKCE-only
  public clients precisely because an auto-approving `/authorize` + public client lets anyone
  who knows the tunnel URL and the non-secret `client_id` mint a token. With a 256-bit
  `client_secret` enforced at `/token`, PKCE-S256 is still required *on top* — the secret
  authenticates the client, PKCE binds the code to the flow.
- **`client_secret` is genuinely needed for Claude.ai.** Claude's connector UI accepts both
  `client_id` and `client_secret`, so a static confidential client works. Do NOT rely on
  PKCE-only for the Claude path.
- **Exact redirect-URI allowlist is acceptable and recommended.** Claude uses the fixed
  callbacks above; pin them exactly (any other `redirect_uri` → 400 with no redirect). This
  also stops `/authorize` being an open redirector.
- **`resource` parameter (RFC 8707).** Send `resource=https://<host>/mcp` in both
  `/authorize` and `/token`. Some clients/servers require it to bind the token to the MCP
  resource; copy it verbatim from the protected-resource metadata to avoid trailing-slash
  mismatches.

---

## 6. Codex / Cursor bearer setup

These clients have no OAuth flow (or only public-PKCE OAuth, which a confidential-only
server rejects). Give them a static bearer token instead (`MCP_BEARER_TOKEN`, ≥32 chars):

**Codex desktop:** Settings → MCP → Connect to a custom MCP → Streamable HTTP → URL =
`https://<host>/mcp`, Bearer token env var = name of an OS env var holding the
`MCP_BEARER_TOKEN` value (restart Codex desktop so it inherits the var).

**Cursor** (`~/.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "hermcp": {
      "url": "https://<host>/mcp",
      "headers": { "Authorization": "Bearer <MCP_BEARER_TOKEN>" }
    }
  }
}
```

The `/mcp` middleware must check **both** paths per request: (a) OAuth-issued access tokens,
then (b) the configured bearer token via `hmac.compare_digest`. The bearer token has no
expiry — it is operator-rotated, not time-rotated. First accepted use logs a single
INFO audit line.

---

## 7. Open questions / risks

1. **In-memory vs durable tokens.** In-memory (reference default) = restart forces every
   client to re-auth; Claude re-auths in seconds because creds are saved in the connector.
   Durable = better for an unattended gateway but stores secret material at rest.
   *Recommendation:* durable with hashed tokens + rotating refresh tokens for hermcp, since
   hermcp's whole point is unattended remote control.
2. **Scope granularity.** `scopes=[]` + one key is simplest but gives every connector full
   key power. A `client_id -> hmcp_key -> scopes` table gives least-privilege at modest cost.
   *Recommendation:* start with one key + `hermes` scope; add the table only when a second
   connector with narrower rights appears.
3. **Auto-approving `/authorize`.** No interactive consent page = the `client_secret` is the
   *only* gate against drive-by token minting. This is acceptable *only* if the secret is
   ≥256 bits and never logged. *Recommendation:* keep auto-approve (Claude.ai can't drive a
   custom consent page), but make the secret mandatory and rotate on any suspected leak.
4. **Session revocation / refresh deadlock.** If a provider revokes the grant server-side,
   `invalid_grant` on refresh is terminal — re-auth, don't loop. This is a provider
   behaviour; for hermcp (self-hosted) it won't happen, but the *client* side (Claude) can
   still drop its session. *Recommendation:* keep the static bearer path as the durable
   fallback for the user's own automation.
5. **`iss` parameter (RFC 9207).** Advertise
   `authorization_response_iss_parameter_supported: true` and include `iss` in the
   `/authorize` redirect; the SDK client validates it. Hermes already tolerates servers that
   advertise-then-omit for specific issuers. *Recommendation:* include `iss`, it costs
   nothing and hardens against mix-up.
6. **DNS rebinding / host allowlist.** The SDK enforces a DNS-rebinding host check. hermcp
   must pass its public hostname(s) into `TransportSecuritySettings(allowed_hosts=...)` or
   requests from the tunnel domain are rejected. *Recommendation:* add `MCP_ALLOWED_HOSTS`
   env config (reference impl does this).

---

## 8. Sources

MCP SDK (authoritative model shapes + route builders), under
`/usr/local/lib/hermes-agent/venv/lib/python3.11/site-packages/`:

- `mcp/shared/auth.py` — `OAuthMetadata` (L201-233), `ProtectedResourceMetadata` (L236-258),
  `OAuthToken` (L26-42), `OAuthClientInformationFull` (L130-198), `OAuthClientMetadata`
  (L111-127), `TokenEndpointAuthMethod` (L11).
- `mcp/server/auth/routes.py` — `build_metadata` (L152-199, advertises
  `client_secret_post`+`client_secret_basic` and `S256`), `build_resource_metadata_url`
  (L202-217, `/.well-known/oauth-protected-resource<path>`), `create_protected_resource_routes`
  (L220-262).
- `mcp/server/auth/handlers/metadata.py` — metadata handlers serve JSON with
  `Cache-Control: public, max-age=3600`.
- `mcp/server/auth/handlers/token.py` — `/token` request models (auth-code L22, refresh L36).

Hermes' own OAuth client (how HERMES consumes remote OAuth MCP servers), under
`/usr/local/lib/hermes-agent/`:

- `tools/mcp_oauth.py` — `HermesTokenStorage` (L412-660): `$HERMES_HOME/mcp-tokens/`
  `<server>.json` + `<server>.client.json` schema, `hermes_issuer` binding, `_safe_filename`
  (L236), `_get_token_dir` (L229).
- `tools/mcp_oauth_provider.py` — ASM discovery paths (L25), WAF/User-Agent fixes (L29-52,
  L193-200), issuer-binding and refresh-token protection.
- `tools/mcp_oauth_manager.py` — PRM + ASM well-known fetch (L102-128).
- `optional-skills/mcp/mcp-oauth-remote-gateway/SKILL.md` — full manual OAuth dance; pitfalls
  (resource param §5 L206-212, exact token schema §8 L243-274, refresh/invalid_grant L339,
  session revocation L341).
- `optional-skills/mcp/mcp-oauth-remote-gateway/references/stripe-mcp-oauth-revocation.md` —
  worked example of revocation + static-key fallback.

Reference implementation `mlennie/hermes-mcp` :

- `src/hermes_mcp/oauth.py` — `StaticClientProvider`, `DEFAULT_ALLOWED_REDIRECT_URIS` (L83-88,
  Claude's two callbacks), confidential `client_secret_post` + mandatory PKCE-S256 (module
  docstring + L167-177), in-memory token stores, `_mint_token_pair` (L334-375), TTLs (L65-67).
- `src/hermes_mcp/server.py` — `build_app`/`build_http_app` (L253-374): FastMCP wiring, DCR
  disabled, metadata override to advertise only `client_secret_post`.
- `src/hermes_mcp/config.py` — env knobs: `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET` (≥32),
  `OAUTH_ISSUER_URL`, `OAUTH_ALLOWED_REDIRECT_URIS`, `MCP_BEARER_TOKEN` (≥32), `MCP_ALLOWED_HOSTS`.
- `README.md` — per-client setup (Claude L103, Codex L104, Cursor L105-122), security model
  (L383-392), reboot/revocation behaviour (L407).

Specs: RFC 8414 (AS metadata), RFC 9728 (protected-resource metadata), RFC 7591 (DCR),
RFC 8707 (resource indicators), RFC 6749 (token endpoint), RFC 7636 (PKCE), RFC 9207 (iss).
