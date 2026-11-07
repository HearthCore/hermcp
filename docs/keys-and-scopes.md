# Keys and scopes

What a key may do, how a scope is spelled, and what an admin key can unlock.

[← README](../README.md) · [docs index](../README.md#documentation)

---

Keys are `hmcp_<id>_<secret>`, shown **once**; only an HMAC-SHA256 hash with a per-key salt
is stored (`~/.hermes/plugin-data/hermcp/keys.json`, mode 0600).

| Scope | Values | Meaning |
|---|---|---|
| `access` | `read` / `write` / `admin` | Coarse capability. Every tool carries one level. |
| `toolsets` | list or `*` | Which toolsets the key may see at all. Hidden tools never appear in `tools/list`. |
| `profiles` | list or `*` | Which Hermes profiles the key may touch. A key pinned to one profile cannot mutate global state. |
| `deny_tools` | list | Explicit per-tool blocklist, wins over everything. |
| `approvals` | bool | Remote approval tools. **Never implied by `*`** — must be set explicitly. |
| `secrets_write` | bool | Write-only secret tools (`secret_set`, …). Also never implied. |
| `dangerous` | bool | Exfiltration / irreversible tools (`memory_reset`, `profiles_delete`). Denied by default, **not even `admin` + `*` unlocks them** — set it explicitly (`--allow-dangerous`). |
| `inbox_push` | bool | Deliver events into the inbox (`inbox_push`, `POST /inbox`). **Never implied** by `*` or `admin` — set it explicitly (`--inbox-push`). |
| `approvals_always` | bool | Answer an approval with `always` (permanent rule rewrite for that pattern). **Never implied** by `*` or `admin`: needs `access=admin` **and** this flag (`--approvals-always`); every grant is audited. |

Key examples:

```bash
# Alice: may talk to the `researcher` agent and use its kanban/sessions, nothing else.
# Pinned to one profile -> every profile-aware call defaults to `researcher`, other profiles are
# refused, and global writes (config of other profiles, plugins, …) are impossible.
hermes hermcp keys create --name alice-researcher --owner alice --access write \
  --toolsets chat,kanban,sessions,status --profiles researcher

# Read-only overview key (e.g. for a dashboard or a voice assistant), expires in 30 days
hermes hermcp keys create --name overview --access read --toolsets status,sessions,cron,kanban --expires 30d

# Your own admin key. `approvals` and `secrets_write` are opt-in flags, `--allow-dangerous`
# additionally unlocks memory_reset / profiles_delete — leave it off unless you need it.
hermes hermcp keys create --name admin-key --access admin --approvals --secrets-write

hermes hermcp keys list
hermes hermcp keys rotate alice-researcher      # new secret, same id; OAuth tokens minted from it die
hermes hermcp keys rename alice-researcher alice-r2   # metadata only; the token keeps working
hermes hermcp keys scopes alice-r2 --copy-from admin-key   # take over another key's scopes, token unchanged
hermes hermcp keys scopes alice-r2 --no-approvals --toolsets status,sessions   # switch single scopes on/off
hermes hermcp keys revoke <id> --delete
```

Every call is appended to `~/.hermes/plugin-data/hermcp/audit.jsonl` with key id, tool name,
profile, duration and outcome. Secret-flagged tools are redacted entirely. Values never
enter the log.

### Admin token and scopes at a glance

There is no default account: **the first admin key is created locally by whoever has shell
access** to the Hermes host, and it is shown exactly once.

```bash
hermes hermcp keys create --name admin-key --access admin      # prints hmcp_<id>_<secret>
```

| You want | Create the key with |
|---|---|
| Full control (treat like an SSH root key) | `--access admin` (add `--approvals`, `--secrets-write`, `--allow-dangerous` only if you need them) |
| Day-to-day operation without admin tools | `--access write --toolsets ...` |
| Monitoring / voice assistant | `--access read --toolsets status,sessions,cron,kanban --expires 30d` |
| One agent only (per user) | `--access write --profiles <profile> --toolsets chat,kanban,sessions,status` |

Rules to remember: `access` is a ceiling (`admin ⊃ write ⊃ read`); `approvals`, `secrets_write`,
`dangerous`, `inbox_push` and `approvals_always` are **never** implied by `*` or `admin`; tools
outside a key's scope are hidden from `tools/list` and refused when called anyway. The Claude.ai
connector inherits the scopes of the `hmcp_` key pasted on its consent page (see below).
`hermes hermcp keys list` shows scopes and last use; `keys rotate` / `keys revoke` take effect immediately.
`keys rename` and `keys scopes` only touch metadata — a running key keeps its token (the name and scopes are
not part of the hash), and the server picks the change up at the next request.

