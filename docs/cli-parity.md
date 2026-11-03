# hermcp CLI → MCP tool parity

Generated from `hermes --help` (snapshot of October 2026; command names may differ in newer Hermes releases).

Every leaf command of the Hermes CLI maps to exactly one row. The rest of the Hermes surface
(dashboard API, Python internals) is NOT part of this table.

## 1. Summary

- Leaf commands total: **383**
- access read: **119** · write: **162** · admin: **102**
- per toolset: kanban 64, skills 46, ops 39, memory 33, config 26, plugins 26, profiles 26, sessions 23, cron 20, tools 20, mcp 16, providers 15, models 8, status 7, admin 5, approvals 4, chat 4, meta 1
- flagged dangerous: **104**
- without `--json` (text parsing needed): **245**

## 2. Full mapping

| hermes command | Short description | MCP tool | Toolset | access | dry_run | Notizen |
|---|---|---|---|---|---|---|
| `hermes import` | Extract a previously created Hermes backup into your Hermes home directory, restoring conf | `hermes_import` | admin | admin | yes | restores a backup INTO ~/.hermes (overwrites) |
| `hermes migrate relay` | The NeMo Relay cutover stopped reading the legacy exporter variables. Generate <hermes hom | `hermes_migrate_relay` | admin | admin | yes | rewrites exporter env vars |
| `hermes migrate xai` | Scan config.yaml for references to xAI models retiring on May 15, 2026 and, with --apply,  | `hermes_migrate_xai` | admin | admin | yes | rewrites retiring xAi model refs in config.yaml |
| `hermes uninstall` | Remove Hermes Agent from your system. Can keep configs/data for reinstall. | `hermes_uninstall` | admin | admin | yes | removes the install; keep configs via flags; has --dry-run |
| `hermes update` | Pull the latest changes from git and reinstall dependencies | `hermes_update` | admin | admin | yes | git pull + reinstall deps; patches may be lost — plan first; has --plan |
| `hermes approvals` | Tools for the dangerous-command approval system. `hermes approvals suggest` mines past app | `hermes_approvals` | approvals | read | no | no --json → parse text |
| `hermes hooks doctor` | Check each configured hook: exec bit, allowlist, mtime drift, JSON validity, and synthetic | `hermes_hooks_doctor` | approvals | read | no | no --json → parse text |
| `hermes hooks list` | List configured hooks with matcher, timeout, and consent status | `hermes_hooks_list` | approvals | read | no | no --json → parse text |
| `hermes hooks test` | Fire every hook matching <event> against a synthetic payload revoke (remove, rm) Remove a  | `hermes_hooks_test` | approvals | write | yes | executes hook scripts |
| `hermes acp` | Start Hermes Agent in ACP mode for editor integration (VS Code, Zed, JetBrains) | `hermes_acp` | chat | read | no | long-running stdio server |
| `hermes chat` | Start an interactive chat session with Hermes Agent | `hermes_chat` | chat | write | no | interactive REPL; use -q for one-shot (--format stream-json available), never remotely |
| `hermes console` | Open a curated Hermes command REPL. This is not a raw shell and does not expose the full H | `hermes_console` | chat | read | no | curated command REPL |
| `hermes send` | Pipe text from any shell script to any messaging platform Hermes is already configured for | `hermes_send` | chat | write | yes | posts to a platform — external side effect; --json |
| `hermes claw cleanup` | Scan for and archive leftover OpenClaw directories to prevent state fragmentation | `hermes_claw_cleanup` | config | admin | yes | archives leftover OpenClaw dirs; has --dry-run |
| `hermes claw migrate` | Import settings, memories, skills, and API keys from an OpenClaw installation. Always show | `hermes_claw_migrate` | config | admin | yes | imports OpenClaw settings/keys into Hermes; has --dry-run |
| `hermes codex-runtime migrate` | Idempotent: replaces the managed block, keeps user text verbatim, skips Hermes servers who | `hermes_codex_runtime_migrate` | config | admin | yes | rewrites MCP config managed block; --json; has --dry-run |
| `hermes config check` | Check for missing/outdated config | `hermes_config_check` | config | read | no | read-only drift report |
| `hermes config edit` | Open config file in editor | `hermes_config_edit` | config | write | yes | opens $EDITOR — impossible remotely; use config set; take effect: restart gateway/dashboard |
| `hermes config env-path` | Print .env file path | `hermes_config_env_path` | config | read | no | no --json → parse text |
| `hermes config get` | Print a resolved configuration value | `hermes_config_get` | config | read | no | resolved value; --json available; --json |
| `hermes config migrate` | Update config with new options | `hermes_config_migrate` | config | admin | yes | rewrites config.yaml with new options; take effect: restart gateway/dashboard |
| `hermes config path` | Print config file path | `hermes_config_path` | config | read | no | no --json → parse text |
| `hermes config set` | Set a configuration value | `hermes_config_set` | config | write | yes | touches ~/.hermes/config.yaml; validate YAML after write; take effect: restart gateway/dashboard |
| `hermes config show` | Show current configuration | `hermes_config_show` | config | read | no | no --json → parse text |
| `hermes config unset` | Remove a configuration value | `hermes_config_unset` | config | write | yes | touches ~/.hermes/config.yaml; take effect: restart gateway/dashboard |
| `hermes import-agent` | One-command import of another coding agent's setup into Hermes. Maps CLAUDE.md/AGENTS.md i | `hermes_import_agent` | config | admin | yes | rewrites CLAUDE.md/AGENTS.md mapping; writes into ~/.hermes; has --dry-run |
| `hermes setup agent` | Run only the agent section of the interactive setup wizard | `hermes_setup_agent` | config | write | no | wizard section; writes config.yaml |
| `hermes setup gateway` | Run only the gateway section of the interactive setup wizard | `hermes_setup_gateway` | config | write | no | wizard section; writes config.yaml |
| `hermes setup model` | Run only the model section of the interactive setup wizard | `hermes_setup_model` | config | write | no | wizard section; writes config.yaml |
| `hermes setup telemetry` | Run only the telemetry section of the interactive setup wizard | `hermes_setup_telemetry` | config | write | no | wizard section; writes config.yaml |
| `hermes setup terminal` | Run only the terminal section of the interactive setup wizard | `hermes_setup_terminal` | config | write | no | wizard section; writes config.yaml |
| `hermes setup tools` | Run only the tools section of the interactive setup wizard | `hermes_setup_tools` | config | write | no | wizard section; writes config.yaml |
| `hermes setup tts` | Run only the tts section of the interactive setup wizard | `hermes_setup_tts` | config | write | no | wizard section; writes config.yaml |
| `hermes skin list` | List available skins | `hermes_skin_list` | config | read | no | no --json → parse text |
| `hermes skin set` | Set one color of the active skin (e.g. `skin set ui_tool '#00FFFF'`) | `hermes_skin_set` | config | write | yes | writes a colour into config |
| `hermes skin use` | Switch the active skin | `hermes_skin_use` | config | write | yes | writes display.skin |
| `hermes slack manifest` | Generate a Slack app manifest that registers every gateway command in COMMAND_REGISTRY as  | `hermes_slack_manifest` | config | read | no | prints a manifest — pure read |
| `hermes whatsapp` | Configure WhatsApp and pair via QR code | `hermes_whatsapp` | config | write | no | QR-code pairing, needs a terminal |
| `hermes whatsapp-cloud` | Configure the official Meta WhatsApp Business Cloud API adapter (Business account required | `hermes_whatsapp_cloud` | config | write | no | no --json → parse text |
| `hermes cron create` | Create a scheduled job | `hermes_cron_create` | cron | write | yes | interactive if args omitted; needs schedule |
| `hermes cron doctor` | Check scheduled jobs for common health issues | `hermes_cron_doctor` | cron | read | no | no --json → parse text |
| `hermes cron edit` | Edit an existing scheduled job | `hermes_cron_edit` | cron | write | yes | no --json → parse text |
| `hermes cron incidents` | List or acknowledge durable cron failure incidents | `hermes_cron_incidents` | cron | write | yes | list or acknowledge failure incidents |
| `hermes cron list` | List scheduled jobs | `hermes_cron_list` | cron | read | no | no --json → parse text |
| `hermes cron notepad delete` | Delete one notepad key of a cron job | `hermes_cron_notepad_delete` | cron | write | yes | no --json → parse text |
| `hermes cron notepad get` | Read one notepad key of a cron job | `hermes_cron_notepad_get` | cron | read | no | no --json → parse text |
| `hermes cron notepad list` | List a cron job's notepad keys | `hermes_cron_notepad_list` | cron | read | no | no --json → parse text |
| `hermes cron notepad set` | Write one notepad key of a cron job | `hermes_cron_notepad_set` | cron | write | yes | no --json → parse text |
| `hermes cron pause` | Pause a scheduled job | `hermes_cron_pause` | cron | write | yes | no --json → parse text |
| `hermes cron remove` | Remove a scheduled job | `hermes_cron_remove` | cron | admin | yes | deletes the job + its runs |
| `hermes cron resume` | Resume a paused job | `hermes_cron_resume` | cron | write | yes | no --json → parse text |
| `hermes cron run` | Run a job on the next scheduler tick remove (rm, delete) Remove a scheduled job | `hermes_cron_run` | cron | write | yes | runs on the next scheduler tick, not immediately |
| `hermes cron runs` | Show durable execution attempts | `hermes_cron_runs` | cron | read | no | no --json → parse text |
| `hermes cron status` | Check if cron scheduler is running | `hermes_cron_status` | cron | read | no | read-only |
| `hermes cron tick` | Run due jobs once and exit | `hermes_cron_tick` | cron | write | no | runs due jobs once and exits (scheduler steal) |
| `hermes webhook list` | List all dynamic subscriptions | `hermes_webhook_list` | cron | read | no | no --json → parse text |
| `hermes webhook remove` | Remove a subscription | `hermes_webhook_remove` | cron | admin | yes | no --json → parse text |
| `hermes webhook subscribe` | Create a webhook subscription | `hermes_webhook_subscribe` | cron | write | yes | no --json → parse text |
| `hermes webhook test` | Send a test POST to a webhook route | `hermes_webhook_test` | cron | write | yes | no --json → parse text |
| `hermes kanban archive` | Archive one or more tasks | `hermes_kanban_archive` | kanban | write | yes | no --json → parse text |
| `hermes kanban assign` | Assign or reassign a task | `hermes_kanban_assign` | kanban | write | yes | no --json → parse text |
| `hermes kanban assignees` | List known profiles + per-profile task counts (union of ~/.hermes/profiles/ and current as | `hermes_kanban_assignees` | kanban | read | no | --json |
| `hermes kanban attach` | Attach a local file to a task | `hermes_kanban_attach` | kanban | write | yes | no --json → parse text |
| `hermes kanban attach-rm` | Delete an attachment by id | `hermes_kanban_attach_rm` | kanban | write | yes | no --json → parse text |
| `hermes kanban attachments` | List a task's attachments | `hermes_kanban_attachments` | kanban | read | no | --json |
| `hermes kanban block` | Mark one or more tasks blocked | `hermes_kanban_block` | kanban | write | yes | no --json → parse text |
| `hermes kanban boards create` | Create a board (name, description, icon, color) | `hermes_kanban_boards_create` | kanban | write | yes | no --json → parse text |
| `hermes kanban boards export` | Package a board's tasks, comments, links, history, and file attachments into one archive t | `hermes_kanban_boards_export` | kanban | write | yes | writes a .tar.gz incl. attachments; --json |
| `hermes kanban boards import` | Import a .tar.gz produced by `hermes kanban boards export`. The board always lands as a NE | `hermes_kanban_boards_import` | kanban | admin | yes | always lands as a new board; --json |
| `hermes kanban boards list` | List boards on this host | `hermes_kanban_boards_list` | kanban | read | no | --json |
| `hermes kanban boards rename` | Rename a board | `hermes_kanban_boards_rename` | kanban | write | yes | no --json → parse text |
| `hermes kanban boards rm` | Remove a board, or delete it with --delete | `hermes_kanban_boards_rm` | kanban | admin | yes | --delete drops the DB and tasks permanently |
| `hermes kanban boards set-default-workdir` | Set a board's default workspace path | `hermes_kanban_boards_set_default_workdir` | kanban | write | yes | no --json → parse text |
| `hermes kanban boards show` | Show the current board | `hermes_kanban_boards_show` | kanban | read | no | no --json → parse text |
| `hermes kanban boards switch` | Switch the active board | `hermes_kanban_boards_switch` | kanban | write | yes | no --json → parse text |
| `hermes kanban claim` | Atomically claim a ready task (prints resolved workspace path) | `hermes_kanban_claim` | kanban | write | yes | worker-only; returns workspace path |
| `hermes kanban comment` | Append a comment | `hermes_kanban_comment` | kanban | write | yes | no --json → parse text |
| `hermes kanban complete` | Mark one or more tasks done | `hermes_kanban_complete` | kanban | write | yes | can be applied to many task ids at once |
| `hermes kanban context` | Print the full context a worker sees for a task (title + body + parent results + comments) | `hermes_kanban_context` | kanban | read | no | no --json → parse text |
| `hermes kanban create` | Create a new task | `hermes_kanban_create` | kanban | write | yes | --json |
| `hermes kanban daemon` | DEPRECATED — dispatcher now runs in the gateway. Use `hermes gateway start`. | `hermes_kanban_daemon` | kanban | admin | yes | DEPRECATED — dispatcher now lives in the gateway; take effect: restart gateway/dashboard |
| `hermes kanban decompose` | Decompose a triage-column task into a graph of child tasks routed to specialist profiles b | `hermes_kanban_decompose` | kanban | write | yes | --json |
| `hermes kanban diagnostics` | List active diagnostics on the current board | `hermes_kanban_diagnostics` | kanban | read | no | --json |
| `hermes kanban dispatch` | One dispatcher pass: reclaim stale, promote ready, spawn workers | `hermes_kanban_dispatch` | kanban | admin | yes | spawns worker processes; one pass; --json; has --dry-run |
| `hermes kanban edit` | Edit task fields or recovery fields on an already- completed task | `hermes_kanban_edit` | kanban | write | yes | no --json → parse text |
| `hermes kanban gc` | Garbage-collect archived-task workspaces, old events, and old logs | `hermes_kanban_gc` | kanban | admin | yes | deletes archived workspaces/events/logs |
| `hermes kanban heartbeat` | Emit a heartbeat event for a running task (worker liveness signal) | `hermes_kanban_heartbeat` | kanban | write | yes | worker liveness only |
| `hermes kanban init` | Create kanban.db if missing (idempotent) | `hermes_kanban_init` | kanban | write | yes | no --json → parse text |
| `hermes kanban link` | Add a parent->child dependency | `hermes_kanban_link` | kanban | write | yes | no --json → parse text |
| `hermes kanban list` | List tasks | `hermes_kanban_list` | kanban | read | no | --json |
| `hermes kanban log` | Print the worker log for a task (from <kanban- root>/kanban/logs/) | `hermes_kanban_log` | kanban | read | no | no --json → parse text |
| `hermes kanban notify-list` | List notification subscriptions (optionally for a single task) | `hermes_kanban_notify_list` | kanban | write | no | --json |
| `hermes kanban notify-subscribe` | Subscribe a gateway source to a task's terminal events (used by /kanban subscribe in the g | `hermes_kanban_notify_subscribe` | kanban | write | yes | binds a gateway source to a task |
| `hermes kanban notify-unsubscribe` | Remove a gateway subscription from a task | `hermes_kanban_notify_unsubscribe` | kanban | write | yes | no --json → parse text |
| `hermes kanban promote` | Manually move one or more todo/blocked tasks to ready (recovery path) | `hermes_kanban_promote` | kanban | write | yes | --json; has --dry-run |
| `hermes kanban reassign` | Reassign a task to a different profile, optionally reclaiming first | `hermes_kanban_reassign` | kanban | write | yes | no --json → parse text |
| `hermes kanban reclaim` | Release an active worker claim on a running task | `hermes_kanban_reclaim` | kanban | write | yes | no --json → parse text |
| `hermes kanban reopen-review` | Send one or more review tasks back for changes (review -> ready/todo) | `hermes_kanban_reopen_review` | kanban | write | yes | no --json → parse text |
| `hermes kanban repair` | Runs PRAGMA integrity_check on the board's DB and reports the result. When the failure con | `hermes_kanban_repair` | kanban | admin | yes | PRAGMA integrity_check (and optional fix); --json |
| `hermes kanban request-changes` | Reviewer verdict: return the active review run to its implementer | `hermes_kanban_request_changes` | kanban | write | yes | no --json → parse text |
| `hermes kanban request-review` | Move a task to 'review' (implementation done, awaiting review) — NOT a block | `hermes_kanban_request_review` | kanban | write | yes | no --json → parse text |
| `hermes kanban runs` | Show attempt history for a task (one row per run: profile, outcome, elapsed, summary) | `hermes_kanban_runs` | kanban | read | no | --json |
| `hermes kanban schedule` | Park one or more tasks in Scheduled (waiting on time, not human input) | `hermes_kanban_schedule` | kanban | write | yes | no --json → parse text |
| `hermes kanban set-model` | Set or clear a task's model/provider override (takes effect on the next dispatch) | `hermes_kanban_set_model` | kanban | write | yes | no --json → parse text |
| `hermes kanban show` | Show a task with comments + events | `hermes_kanban_show` | kanban | read | no | --json |
| `hermes kanban specify` | Flesh out a triage-column task into a concrete spec (title + body) and promote it to todo. | `hermes_kanban_specify` | kanban | write | yes | --json |
| `hermes kanban stats` | Per-status + per-assignee counts + oldest-ready age | `hermes_kanban_stats` | kanban | read | no | --json |
| `hermes kanban swarm` | Create a Kanban Swarm v1 graph (parallel workers → verifier → synthesizer) | `hermes_kanban_swarm` | kanban | write | yes | creates a whole worker→verifier→synthesizer graph; --json |
| `hermes kanban tail` | Follow a task's event stream | `hermes_kanban_tail` | kanban | read | no | no --json → parse text |
| `hermes kanban unblock` | Return blocked/scheduled tasks to ready, or todo while parents remain open | `hermes_kanban_unblock` | kanban | write | yes | no --json → parse text |
| `hermes kanban unlink` | Remove a parent->child dependency | `hermes_kanban_unlink` | kanban | write | yes | no --json → parse text |
| `hermes kanban watch` | Live-stream task_events to the terminal (Ctrl+C to exit) | `hermes_kanban_watch` | kanban | write | no | no --json → parse text |
| `hermes project add-folder` | Add a folder to a project | `hermes_project_add_folder` | kanban | write | no | no --json → parse text |
| `hermes project archive` | Archive a project | `hermes_project_archive` | kanban | admin | yes | no --json → parse text |
| `hermes project bind-board` | Bind a kanban board to a project | `hermes_project_bind_board` | kanban | write | no | no --json → parse text |
| `hermes project create` | Create a new project | `hermes_project_create` | kanban | write | yes | no --json → parse text |
| `hermes project list` | List projects | `hermes_project_list` | kanban | read | no | no --json → parse text |
| `hermes project remove-folder` | Remove a folder from a project | `hermes_project_remove_folder` | kanban | write | yes | no --json → parse text |
| `hermes project rename` | Rename a project | `hermes_project_rename` | kanban | write | no | no --json → parse text |
| `hermes project restore` | Restore an archived project | `hermes_project_restore` | kanban | admin | yes | no --json → parse text |
| `hermes project set-primary` | Set the primary folder | `hermes_project_set_primary` | kanban | write | no | no --json → parse text |
| `hermes project show` | Show a project's details | `hermes_project_show` | kanban | read | no | no --json → parse text |
| `hermes project use` | Set the active project | `hermes_project_use` | kanban | write | no | no --json → parse text |
| `hermes connectors accounts` | Show connected accounts | `hermes_connectors_accounts` | mcp | read | no | no --json → parse text |
| `hermes connectors connect` | Connect an app account in your browser | `hermes_connectors_connect` | mcp | write | no | no --json → parse text |
| `hermes connectors disconnect` | Remove an account | `hermes_connectors_disconnect` | mcp | admin | yes | no --json → parse text |
| `hermes connectors list` | Show every connector and whether it is connected | `hermes_connectors_list` | mcp | read | no | no --json → parse text |
| `hermes connectors rename` | Rename an account | `hermes_connectors_rename` | mcp | write | no | no --json → parse text |
| `hermes mcp add` | Add an MCP server (discovery-first install) | `hermes_mcp_add` | mcp | write | yes | writes MCP server config; discovery-first; take effect: restart gateway/dashboard |
| `hermes mcp catalog` | List Nous-approved MCPs available for one-click install | `hermes_mcp_catalog` | mcp | read | no | no --json → parse text |
| `hermes mcp configure` | Toggle tool selection | `hermes_mcp_configure` | mcp | write | yes | interactive/structured tool toggles |
| `hermes mcp install` | Install a catalog MCP by name (e.g. `hermes mcp install deepwiki`) | `hermes_mcp_install` | mcp | write | yes | take effect: restart gateway/dashboard |
| `hermes mcp list` | List configured MCP servers | `hermes_mcp_list` | mcp | read | no | no --json → parse text |
| `hermes mcp login` | Force re-authentication for an OAuth-based MCP server | `hermes_mcp_login` | mcp | write | yes | OAuth browser flow; take effect: restart gateway/dashboard |
| `hermes mcp picker` | Interactive catalog picker (also the default for `hermes mcp`) | `hermes_mcp_picker` | mcp | write | yes | interactive catalog picker |
| `hermes mcp reauth` | Re-authenticate one OAuth MCP server, or all of them (--all) | `hermes_mcp_reauth` | mcp | write | yes | OAuth browser flow; take effect: restart gateway/dashboard |
| `hermes mcp remove` | Remove an MCP server | `hermes_mcp_remove` | mcp | admin | yes | removes server config; take effect: restart gateway/dashboard |
| `hermes mcp serve` | Run Hermes as an MCP server (expose conversations to other agents) | `hermes_mcp_serve` | mcp | admin | no | long-running stdio server |
| `hermes mcp test` | Test MCP server connection | `hermes_mcp_test` | mcp | read | no | no --json → parse text |
| `hermes journey delete` | Delete a learned skill (archived) or memory by node id. | `hermes_journey_delete` | memory | admin | yes | no --json → parse text |
| `hermes journey edit` | Edit a learned skill or memory by node id in $EDITOR. | `hermes_journey_edit` | memory | write | yes | no --json → parse text |
| `hermes journey list` | List node ids (for delete/edit). | `hermes_journey_list` | memory | read | no | no --json → parse text |
| `hermes memory off` | Disable external provider (built-in only) | `hermes_memory_off` | memory | write | yes | disables external provider; take effect: restart gateway/dashboard |
| `hermes memory reset` | Erase all built-in memory (MEMORY.md and USER.md) | `hermes_memory_reset` | memory | admin | yes | erases MEMORY.md + USER.md; take effect: restart gateway/dashboard |
| `hermes memory setup` | Interactive provider selection and configuration | `hermes_memory_setup` | memory | write | yes | interactive provider selection; take effect: restart gateway/dashboard |
| `hermes memory status` | Show current memory provider config | `hermes_memory_status` | memory | read | no | no --json → parse text |
| `hermes secrets 1password disable` | Disable the 1Password source | `hermes_secrets_1password_disable` | memory | admin | no | no --json → parse text |
| `hermes secrets 1password remove` | Remove an env-var mapping | `hermes_secrets_1password_remove` | memory | write | no | no --json → parse text |
| `hermes secrets 1password set` | Map an env var to a 1Password reference | `hermes_secrets_1password_set` | memory | write | no | no --json → parse text |
| `hermes secrets 1password setup` | Configure the 1Password service-account source | `hermes_secrets_1password_setup` | memory | admin | no | take effect: restart gateway/dashboard |
| `hermes secrets 1password status` | Show 1Password source status | `hermes_secrets_1password_status` | memory | read | no | no --json → parse text |
| `hermes secrets 1password sync` | Sync mapped secrets into .env (--apply to write) | `hermes_secrets_1password_sync` | memory | write | yes | --apply writes .env |
| `hermes secrets 1password token` | Set/verify the 1Password service-account token | `hermes_secrets_1password_token` | memory | admin | no | writes a token |
| `hermes secrets bitwarden disable` | Disable the Bitwarden source | `hermes_secrets_bitwarden_disable` | memory | admin | no | no --json → parse text |
| `hermes secrets bitwarden install` | Install the Bitwarden CLI binary | `hermes_secrets_bitwarden_install` | memory | write | yes | no --json → parse text |
| `hermes secrets bitwarden setup` | Configure the Bitwarden Secrets Manager source | `hermes_secrets_bitwarden_setup` | memory | admin | no | take effect: restart gateway/dashboard |
| `hermes secrets bitwarden status` | Show Bitwarden source status | `hermes_secrets_bitwarden_status` | memory | read | no | no --json → parse text |
| `hermes secrets bitwarden sync` | Sync mapped secrets into .env (--apply to write) | `hermes_secrets_bitwarden_sync` | memory | write | yes | --apply writes .env |
| `hermes secrets bitwarden token` | Set/verify the Bitwarden access token | `hermes_secrets_bitwarden_token` | memory | admin | no | writes a token into .env / secret store |
| `hermes secrets bw` | Bitwarden secret source (alias of `secrets bitwarden`) | `hermes_secrets_bw` | memory | write | no | no --json → parse text |
| `hermes secrets onepassword disable` | Disable the 1Password source | `hermes_secrets_onepassword_disable` | memory | admin | no | no --json → parse text |
| `hermes secrets onepassword remove` | Remove an env-var mapping | `hermes_secrets_onepassword_remove` | memory | write | no | no --json → parse text |
| `hermes secrets onepassword set` | Map an env var to a 1Password reference | `hermes_secrets_onepassword_set` | memory | write | no | no --json → parse text |
| `hermes secrets onepassword setup` | Configure the 1Password service-account source | `hermes_secrets_onepassword_setup` | memory | admin | no | take effect: restart gateway/dashboard |
| `hermes secrets onepassword status` | Show 1Password source status | `hermes_secrets_onepassword_status` | memory | read | no | no --json → parse text |
| `hermes secrets onepassword sync` | Sync mapped secrets into .env (--apply to write) | `hermes_secrets_onepassword_sync` | memory | write | yes | --apply writes .env |
| `hermes secrets onepassword token` | Set/verify the 1Password service-account token | `hermes_secrets_onepassword_token` | memory | admin | no | writes a token |
| `hermes secrets op` | 1Password secret source (alias of `secrets onepassword`) | `hermes_secrets_op` | memory | write | no | no --json → parse text |
| `hermes vault add` | Save a login, card or address ahead of time (optional: the agent asks you on the page when | `hermes_vault_add` | memory | write | yes | stores a credential in the encrypted vault |
| `hermes vault list` | List vault items (metadata only, never values) | `hermes_vault_list` | memory | read | no | metadata only, never values |
| `hermes vault rm` | Remove a vault item by handle | `hermes_vault_rm` | memory | admin | yes | deletes a vault item |
| `hermes vault sources` | Show detected password managers (1Password, Bitwarden); they are on automatically | `hermes_vault_sources` | memory | read | yes | no --json → parse text |
| `hermes completion` | Print a shell completion script (bash, zsh or fish) | `hermes_completion` | meta | read | no | pure stdout, TTY-agnostic |
| `hermes fallback add` | Pick a provider + model (same picker as `hermes model`) and append to the chain | `hermes_fallback_add` | models | write | yes | interactive picker (same as model); take effect: restart gateway/dashboard |
| `hermes fallback clear` | Remove all fallback entries | `hermes_fallback_clear` | models | admin | yes | take effect: restart gateway/dashboard |
| `hermes fallback list` | Show the current fallback chain (default when no subcommand) | `hermes_fallback_list` | models | read | no | default when no subcommand |
| `hermes fallback remove` | Pick an entry to delete from the chain | `hermes_fallback_remove` | models | write | yes | interactive picker; take effect: restart gateway/dashboard |
| `hermes moa configure` | Interactively pick MoA models | `hermes_moa_configure` | models | write | no | interactive picker |
| `hermes moa delete` | Delete a MoA preset | `hermes_moa_delete` | models | admin | no | no --json → parse text |
| `hermes moa list` | Show current MoA model slots | `hermes_moa_list` | models | read | no | no --json → parse text |
| `hermes model` | Interactively select your inference provider and default model | `hermes_model` | models | write | yes | interactive provider/model picker — not scriptable; take effect: restart gateway/dashboard |
| `hermes backup` | Create a zip archive of your entire Hermes configuration, skills, sessions, and data (excl | `hermes_backup` | ops | admin | yes | reads everything; writes a zip |
| `hermes checkpoints` | Manage the filesystem checkpoint store — the shadow git repo hermes uses to snapshot worki | `hermes_checkpoints` | ops | read | no | read checkpoint store; prune/clear are destructive |
| `hermes dashboard register` | Register this install as a self-hosted dashboard with your Nous Portal account. Creates an | `hermes_dashboard_register` | ops | write | yes | registers the install with Nous Portal |
| `hermes debug delete` | Delete a paste uploaded by 'hermes debug share' | `hermes_debug_delete` | ops | admin | yes | no --json → parse text |
| `hermes debug share` | Upload debug report to a paste service and print a shareable URL | `hermes_debug_share` | ops | admin | yes | uploads logs to a paste service (data leaves host!) |
| `hermes desktop` | Launch the Hermes Electron desktop app. By default this installs workspace Node dependenci | `hermes_desktop` | ops | write | no | launches Electron |
| `hermes egress config` | Print the generated proxy.yaml path | `hermes_egress_config` | ops | read | no | no --json → parse text |
| `hermes egress disable` | Turn off the proxy integration | `hermes_egress_disable` | ops | admin | yes | disables the egress firewall; take effect: restart gateway/dashboard |
| `hermes egress install` | Install the PM-pinned iron-proxy binary | `hermes_egress_install` | ops | admin | yes | installs a pinned binary; take effect: restart gateway/dashboard |
| `hermes egress reload` | Hot-reload the running daemon's ruleset from proxy.yaml (management API — no restart, no d | `hermes_egress_reload` | ops | write | yes | no --json → parse text |
| `hermes egress restart` | Restart the managed iron-proxy (stop if running, then start) | `hermes_egress_restart` | ops | admin | yes | brief outage; take effect: restart gateway/dashboard |
| `hermes egress setup` | Interactive wizard: install + CA + mint tokens + write config | `hermes_egress_setup` | ops | admin | yes | interactive wizard (CA, tokens, config); take effect: restart gateway/dashboard |
| `hermes egress start` | Start the managed iron-proxy | `hermes_egress_start` | ops | admin | yes | starts the managed iron-proxy daemon |
| `hermes egress status` | Show proxy state and mappings | `hermes_egress_status` | ops | read | no | no --json → parse text |
| `hermes egress stop` | Stop the managed iron-proxy | `hermes_egress_stop` | ops | admin | yes | stops the egress proxy — kills outbound traffic for dependents |
| `hermes gateway enroll` | Redeem a single-use enrollment token with a relay connector. Authenticates as your Nous Po | `hermes_gateway_enroll` | ops | write | no | take effect: restart gateway/dashboard |
| `hermes gateway ensure` | Ensure a local runtime without installing or replacing a service | `hermes_gateway_ensure` | ops | write | no | --json; take effect: restart gateway/dashboard |
| `hermes gateway install` | Install gateway as a systemd/launchd background service | `hermes_gateway_install` | ops | admin | yes | writes systemd/launchd unit; host-level change; take effect: restart gateway/dashboard |
| `hermes gateway list` | List all profiles and their gateway status | `hermes_gateway_list` | ops | read | no | no --json → parse text |
| `hermes gateway migrate` | Converge this host onto the one-gateway-per-host model: stop and uninstall each secondary  | `hermes_gateway_migrate` | ops | admin | yes | stops+uninstalls every other gateway on the host; has --dry-run; take effect: restart gateway/dashboard |
| `hermes gateway migrate-legacy` | Stop, disable, and remove legacy Hermes gateway unit files (e.g. hermes.service) left over | `hermes_gateway_migrate_legacy` | ops | admin | yes | removes legacy unit files; has --dry-run; take effect: restart gateway/dashboard |
| `hermes gateway restart` | Restart gateway service | `hermes_gateway_restart` | ops | admin | yes | REQUIRES restart from an EXTERNAL shell; in-gateway SIGTERM propagation |
| `hermes gateway run` | Run gateway in foreground (recommended for WSL and Docker) | `hermes_gateway_run` | ops | admin | no | foreground service — never via MCP; take effect: restart gateway/dashboard |
| `hermes gateway setup` | Configure messaging platforms | `hermes_gateway_setup` | ops | write | no | take effect: restart gateway/dashboard |
| `hermes gateway start` | Start the installed systemd/launchd background service | `hermes_gateway_start` | ops | admin | yes | take effect: restart gateway/dashboard |
| `hermes gateway status` | Show gateway status | `hermes_gateway_status` | ops | read | no | no --json → parse text |
| `hermes gateway stop` | Stop gateway service | `hermes_gateway_stop` | ops | admin | yes | kills in-flight turns and the calling gateway; take effect: restart gateway/dashboard |
| `hermes gateway uninstall` | Uninstall gateway service | `hermes_gateway_uninstall` | ops | admin | yes | removes the systemd/launchd unit; take effect: restart gateway/dashboard |
| `hermes monitoring status` | Show monitoring settings, export state, and redaction posture | `hermes_monitoring_status` | ops | read | no | no --json → parse text |
| `hermes pause` | Engage the global emergency stop. Halts NEW work only — cron dispatch, kanban dispatch, an | `hermes_pause` | ops | admin | no | global ESTOP: blocks ALL new cron/kanban/gateway work |
| `hermes proxy providers` | List available proxy upstream providers | `hermes_proxy_providers` | ops | read | no | no --json → parse text |
| `hermes proxy start` | Run the proxy in the foreground | `hermes_proxy_start` | ops | admin | no | foreground proxy |
| `hermes proxy status` | Show which proxy upstreams are ready | `hermes_proxy_status` | ops | read | no | no --json → parse text |
| `hermes resume` | Remove the ESTOP sentinel; dispatch resumes on the next tick. | `hermes_resume` | ops | admin | no | lifts the global ESTOP |
| `hermes security` | On-demand vulnerability scan against OSV.dev. Covers the Hermes venv (installed PyPI dists | `hermes_security` | ops | read | no | OSV.dev venv/plugin/MCP audit (network) |
| `hermes serve` | Run the Hermes backend server — the JSON-RPC/WebSocket gateway the desktop app and remote  | `hermes_serve` | ops | admin | no | long-running backend server; take effect: restart gateway/dashboard |
| `hermes verify` | Detect how the current project is built, tested, and started (or load the saved manifest a | `hermes_verify` | ops | read | no | detects run recipe + smoke test; can execute project commands; --json |
| `hermes worktree list` | Classify every tree: age, size, verdict, reason (default action) | `hermes_worktree_list` | ops | read | no | --json |
| `hermes worktree prune` | Remove safe trees and delete fully-merged local branches | `hermes_worktree_prune` | ops | admin | yes | deletes trees + merged branches; --json; has --dry-run |
| `hermes pets doctor` | Check pet setup + terminal graphics support | `hermes_pets_doctor` | plugins | read | no | no --json → parse text |
| `hermes pets install` | Install a pet from the gallery | `hermes_pets_install` | plugins | write | yes | downloads from petdex |
| `hermes pets list` | Browse the petdex gallery | `hermes_pets_list` | plugins | read | no | no --json → parse text |
| `hermes pets off` | Disable the pet display | `hermes_pets_off` | plugins | write | no | no --json → parse text |
| `hermes pets remove` | Delete an installed pet | `hermes_pets_remove` | plugins | write | yes | no --json → parse text |
| `hermes pets scale` | Resize the pet everywhere (display.pet.scale) | `hermes_pets_scale` | plugins | write | no | no --json → parse text |
| `hermes pets select` | Set the active pet (writes display.pet.*) | `hermes_pets_select` | plugins | write | no | no --json → parse text |
| `hermes pets show` | Animate the active pet in the terminal | `hermes_pets_show` | plugins | read | no | animates in the terminal (TTY only) |
| `hermes plugins adopt` | For plugin dirs you cloned yourself (no install record): read the git origin URL, write th | `hermes_plugins_adopt` | plugins | write | yes | take effect: restart gateway/dashboard |
| `hermes plugins browse` | List every curated plugin catalog entry | `hermes_plugins_browse` | plugins | read | no | no --json → parse text |
| `hermes plugins capabilities` | Show each plugin's declared capabilities (from plugin.yaml) against what the user has gran | `hermes_plugins_capabilities` | plugins | read | no | no --json → parse text |
| `hermes plugins check-updates` | Standard, read-only update check for every installed plugin: saved-tag update_url feeds (w | `hermes_plugins_check_updates` | plugins | read | no | --json |
| `hermes plugins disable` | Disable a plugin without removing it | `hermes_plugins_disable` | plugins | write | yes | take effect: restart gateway/dashboard |
| `hermes plugins doctor` | Validate a plugin with the real runtime contracts | `hermes_plugins_doctor` | plugins | read | no | read-only runtime contract check |
| `hermes plugins enable` | Enable a disabled plugin | `hermes_plugins_enable` | plugins | write | yes | take effect: restart gateway/dashboard |
| `hermes plugins install` | Install a plugin from the curated catalog, a Git URL, or owner/repo | `hermes_plugins_install` | plugins | write | yes | git fetch + write into plugins/; take effect: restart gateway/dashboard |
| `hermes plugins list` | List installed plugins | `hermes_plugins_list` | plugins | read | no | --json |
| `hermes plugins pack export` | Export installed plugins into a portable package | `hermes_plugins_pack_export` | plugins | read | yes | writes a portable package |
| `hermes plugins pack install` | Install a portable Agent Plugins v1 package (installs disabled) | `hermes_plugins_pack_install` | plugins | write | yes | take effect: restart gateway/dashboard |
| `hermes plugins pack show` | Inspect a portable plugin package without installing | `hermes_plugins_pack_show` | plugins | read | no | no --json → parse text |
| `hermes plugins remove` | Uninstall a plugin and drop its install record | `hermes_plugins_remove` | plugins | admin | yes | deletes the plugin dir + install record; take effect: restart gateway/dashboard |
| `hermes plugins search` | Search the curated Hermes plugin catalog | `hermes_plugins_search` | plugins | read | no | --json |
| `hermes plugins show` | Show details for a single plugin (including emits/listens) | `hermes_plugins_show` | plugins | read | no | no --json → parse text |
| `hermes plugins trust-update-url` | The ONLY path that moves a saved update_url tag. When a plugin's manifest changed its upda | `hermes_plugins_trust_update_url` | plugins | write | no | only path that moves a saved update_url tag |
| `hermes plugins update` | Pull latest changes for an installed plugin | `hermes_plugins_update` | plugins | write | yes | pulls latest; can change behaviour live; take effect: restart gateway/dashboard |
| `hermes plugins validate` | Validate a plugin directory for catalog admission (CI gate) | `hermes_plugins_validate` | plugins | read | no | CI gate, read-only; --json |
| `hermes pairing approve` | Approve a pairing request | `hermes_pairing_approve` | profiles | write | yes | authorizes a user on a messaging platform |
| `hermes pairing clear-pending` | Clear all pending codes | `hermes_pairing_clear_pending` | profiles | admin | yes | no --json → parse text |
| `hermes pairing list` | Show pending + approved users | `hermes_pairing_list` | profiles | read | no | no --json → parse text |
| `hermes pairing revoke` | Revoke user access | `hermes_pairing_revoke` | profiles | admin | yes | cuts a user's access |
| `hermes peer add` | Register (or update) a peer gateway | `hermes_peer_add` | profiles | write | yes | no --json → parse text |
| `hermes peer dm` | Message an agent on a peer gateway and print its reply | `hermes_peer_dm` | profiles | write | yes | sends a DM to another gateway (external); --json |
| `hermes peer list` | List registered peers | `hermes_peer_list` | profiles | read | no | no --json → parse text |
| `hermes peer remove` | Remove a peer | `hermes_peer_remove` | profiles | admin | yes | peer registration deleted |
| `hermes peer run` | Start a long peer turn asynchronously and return its run ID | `hermes_peer_run` | profiles | write | yes | starts an async turn on a peer gateway; --json |
| `hermes peer status` | Read the status and final output of an asynchronous peer run | `hermes_peer_status` | profiles | read | no | --json |
| `hermes peer stop` | Stop one asynchronous peer run without affecting another turn | `hermes_peer_stop` | profiles | write | no | stops a peer run; --json |
| `hermes profile alias` | Manage wrapper scripts | `hermes_profile_alias` | profiles | write | no | creates/removes wrapper scripts on PATH |
| `hermes profile create` | Create a new profile | `hermes_profile_create` | profiles | write | yes | no --json → parse text |
| `hermes profile delete` | Delete a profile | `hermes_profile_delete` | profiles | admin | yes | deletes profile home incl. sessions/state; irreversible |
| `hermes profile describe` | Read or set a profile's description (used by the kanban orchestrator) | `hermes_profile_describe` | profiles | write | no | no --json → parse text |
| `hermes profile export` | Export a profile to archive | `hermes_profile_export` | profiles | read | yes | no --json → parse text |
| `hermes profile import` | Import a profile from archive | `hermes_profile_import` | profiles | admin | yes | restores a profile archive over an existing home |
| `hermes profile info` | Show a profile's distribution manifest (version, requirements, source) | `hermes_profile_info` | profiles | read | no | no --json → parse text |
| `hermes profile install` | Install a Hermes profile distribution. SOURCE can be a git URL (github.com/user/repo, http | `hermes_profile_install` | profiles | admin | yes | installs a profile distribution from git |
| `hermes profile list` | List all profiles | `hermes_profile_list` | profiles | read | no | no --json → parse text |
| `hermes profile migrate-identity` | Re-run the session/routing identity migration that `hermes profile rename` performs automa | `hermes_profile_migrate_identity` | profiles | admin | yes | rewrites session+routing identity |
| `hermes profile purge-identity` | Re-run the session/routing identity purge that `hermes profile delete` performs automatica | `hermes_profile_purge_identity` | profiles | admin | yes | purges sessions/routing rows for a profile |
| `hermes profile rename` | Rename a profile ('default': sets a display name; id unchanged) | `hermes_profile_rename` | profiles | write | yes | no --json → parse text |
| `hermes profile show` | Show profile details | `hermes_profile_show` | profiles | read | no | no --json → parse text |
| `hermes profile update` | Fetch the distribution from its recorded source and overwrite distribution- owned files (S | `hermes_profile_update` | profiles | write | yes | overwrites distribution-owned files |
| `hermes profile use` | Set sticky default profile | `hermes_profile_use` | profiles | write | yes | changes the sticky default profile host-wide; take effect: restart gateway/dashboard |
| `hermes auth add` | Add a pooled credential | `hermes_auth_add` | providers | write | yes | may prompt for a key on stdin |
| `hermes auth list` | List pooled credentials | `hermes_auth_list` | providers | read | no | no --json → parse text |
| `hermes auth logout` | Log out a provider and clear stored auth state | `hermes_auth_logout` | providers | admin | no | clears stored provider auth |
| `hermes auth priority` | Move a pooled credential to a priority (0 = tried first under fill_first) | `hermes_auth_priority` | providers | write | yes | no --json → parse text |
| `hermes auth refresh` | Refresh a pooled OAuth credential's tokens and clear its cooldown | `hermes_auth_refresh` | providers | write | yes | no --json → parse text |
| `hermes auth remove` | Remove a pooled credential by index, id, or label | `hermes_auth_remove` | providers | admin | yes | removes a pooled credential |
| `hermes auth reset` | Clear exhaustion status for a provider's credentials (all, or one target) | `hermes_auth_reset` | providers | write | yes | clears credential exhaustion state |
| `hermes auth status` | Show auth status for a provider | `hermes_auth_status` | providers | read | no | no --json → parse text |
| `hermes auth upgrade` | Sign in with a Nous account, keeping your connectors | `hermes_auth_upgrade` | providers | write | yes | Nous sign-in, keeps connectors |
| `hermes logout` | Remove stored credentials and reset provider config | `hermes_logout` | providers | admin | yes | clears stored credentials + provider config |
| `hermes portal info` | Show Portal auth + Tool Gateway routing summary | `hermes_portal_info` | providers | read | no | no --json → parse text |
| `hermes portal login` | Log in to Nous Portal + set it up (default; one-shot onboarding) | `hermes_portal_login` | providers | write | yes | OAuth browser flow |
| `hermes portal open` | Open the Portal subscription page in your default browser | `hermes_portal_open` | providers | read | no | no --json → parse text |
| `hermes portal status` | Show Portal login/routing status | `hermes_portal_status` | providers | read | no | no --json → parse text |
| `hermes portal tools` | List Tool Gateway tools and which are routed via Nous | `hermes_portal_tools` | providers | write | no | no --json → parse text |
| `hermes sessions archive` | Bulk-archive (soft-hide) sessions matching filters — no deletion | `hermes_sessions_archive` | sessions | write | yes | soft-hide only (no deletion); has --dry-run |
| `hermes sessions browse` | Interactive session picker — browse, search, and resume sessions | `hermes_sessions_browse` | sessions | read | no | interactive picker |
| `hermes sessions clean-markers` | Before the #78148 fix, a local tool-call template could persist a bare bracketed marker (e | `hermes_sessions_clean_markers` | sessions | admin | yes | has --dry-run |
| `hermes sessions delete` | Delete a specific session | `hermes_sessions_delete` | sessions | admin | yes | deletes a session row + messages |
| `hermes sessions discard` | After a gateway crash mid-turn, the lost turn is fenced 'unknown' and blocks the session:  | `hermes_sessions_discard` | sessions | admin | yes | no --json → parse text |
| `hermes sessions export` | Export sessions to JSONL, Markdown, or QMD | `hermes_sessions_export` | sessions | read | no | JSONL/Markdown/QMD output; has --dry-run |
| `hermes sessions import` | Pull a conversation started in Claude Code (~/.claude/projects) or Codex CLI (~/.codex/ses | `hermes_sessions_import` | sessions | admin | yes | pulls Claude Code / Codex CLI conversations |
| `hermes sessions list` | List recent sessions | `hermes_sessions_list` | sessions | read | no | no --json → parse text |
| `hermes sessions optimize` | Reclaim disk space: merge FTS5 segments + VACUUM (no data change) | `hermes_sessions_optimize` | sessions | admin | yes | VACUUM + FTS merge; can lock the DB briefly |
| `hermes sessions optimize-storage` | Rebuild the full-text search index in the compact v23 external-content layout. On large da | `hermes_sessions_optimize_storage` | sessions | admin | yes | no --json → parse text |
| `hermes sessions pin` | Set the durable 'keep' flag on one or more sessions. Pinned sessions are exempt from the s | `hermes_sessions_pin` | sessions | write | yes | no --json → parse text |
| `hermes sessions pinned` | List pinned sessions | `hermes_sessions_pinned` | sessions | read | no | --json |
| `hermes sessions prune` | Delete old sessions (filterable by time window, source, title, ...) | `hermes_sessions_prune` | sessions | admin | yes | bulk delete by filter; irreversible; has --dry-run |
| `hermes sessions recover` | Offline, non-destructive recovery for a damaged state.db. The source database and its WAL/ | `hermes_sessions_recover` | sessions | admin | yes | offline recovery on a damaged state.db |
| `hermes sessions rename` | Set or change a session's title | `hermes_sessions_rename` | sessions | write | yes | no --json → parse text |
| `hermes sessions repair` | Recover a state.db whose schema is malformed (e.g. 'table messages_fts already exists'), w | `hermes_sessions_repair` | sessions | admin | yes | offline DB repair; back up state.db first |
| `hermes sessions repair-profiles` | Scan every profile's state.db (and the gateway's voice-mode / sessions.json files) for dur | `hermes_sessions_repair_profiles` | sessions | admin | yes | --json |
| `hermes sessions repair-prompts` | Repair session rows degraded by the pre-#122822 gateway hygiene/compress bug. Automatic re | `hermes_sessions_repair_prompts` | sessions | admin | yes | --json |
| `hermes sessions repair-routing` | Find gateway conversations stranded in session rows whose routing identity (session_key/ch | `hermes_sessions_repair_routing` | sessions | admin | yes | no --json → parse text |
| `hermes sessions retitle-skills` | Sessions opened with a /skill were auto-titled from the expanded message, which embeds the | `hermes_sessions_retitle_skills` | sessions | write | no | no --json → parse text |
| `hermes sessions set-journal-mode` | Switch the on-disk journal mode of the session store. Hermes never live- downgrades a WAL  | `hermes_sessions_set_journal_mode` | sessions | admin | yes | changes on-disk journal mode |
| `hermes sessions stats` | Show session store statistics | `hermes_sessions_stats` | sessions | read | no | no --json → parse text |
| `hermes sessions unpin` | Remove the pin (durable keep flag) from session(s) | `hermes_sessions_unpin` | sessions | write | yes | no --json → parse text |
| `hermes bundles create` | Create a new bundle. Skills can be passed via --skill (repeat for multiple) or entered int | `hermes_bundles_create` | skills | write | no | no --json → parse text |
| `hermes bundles delete` | Delete a skill bundle | `hermes_bundles_delete` | skills | admin | no | no --json → parse text |
| `hermes bundles list` | List installed skill bundles | `hermes_bundles_list` | skills | read | no | no --json → parse text |
| `hermes bundles reload` | Re-scan the bundles directory and report changes | `hermes_bundles_reload` | skills | write | no | no --json → parse text |
| `hermes bundles show` | Show one bundle's contents | `hermes_bundles_show` | skills | read | no | no --json → parse text |
| `hermes curator adopt` | Hand unmanaged skills to the curator (provenance is a user declaration) | `hermes_curator_adopt` | skills | write | no | has --dry-run |
| `hermes curator archive` | Manually archive a skill (move to .archive/, excluded from prompt) | `hermes_curator_archive` | skills | admin | no | no --json → parse text |
| `hermes curator backup` | Take a manual tar.gz snapshot of ~/.hermes/skills/ (curator also does this automatically b | `hermes_curator_backup` | skills | write | no | no --json → parse text |
| `hermes curator ledger` | List the per-mutation skill audit ledger (all actors: curator/agent/user) | `hermes_curator_ledger` | skills | write | no | no --json → parse text |
| `hermes curator list-archived` | List archived skills | `hermes_curator_list_archived` | skills | read | no | no --json → parse text |
| `hermes curator list-unmanaged` | List curation-eligible skills with no provenance marker | `hermes_curator_list_unmanaged` | skills | read | no | no --json → parse text |
| `hermes curator pause` | Pause the curator until resumed | `hermes_curator_pause` | skills | write | no | no --json → parse text |
| `hermes curator pin` | Pin a skill so the curator never auto-transitions it | `hermes_curator_pin` | skills | write | no | no --json → parse text |
| `hermes curator prune` | Bulk-archive curator-managed skills idle for >= N days (default: curator.archive_after_day | `hermes_curator_prune` | skills | admin | no | has --dry-run |
| `hermes curator purge` | Delete archived skills older than curator.archive_ttl_days (explicit only — never automati | `hermes_curator_purge` | skills | admin | no | has --dry-run |
| `hermes curator restore` | Restore an archived skill | `hermes_curator_restore` | skills | admin | no | no --json → parse text |
| `hermes curator resume` | Resume a paused curator | `hermes_curator_resume` | skills | write | no | no --json → parse text |
| `hermes curator rollback` | Restore ~/.hermes/skills/ from a curator snapshot, or a single mutation by ledger entry id | `hermes_curator_rollback` | skills | admin | no | no --json → parse text |
| `hermes curator run` | Trigger a curator review now | `hermes_curator_run` | skills | write | no | has --dry-run |
| `hermes curator status` | Show curator status and skill stats | `hermes_curator_status` | skills | read | no | no --json → parse text |
| `hermes curator unpin` | Unpin a skill | `hermes_curator_unpin` | skills | write | no | no --json → parse text |
| `hermes curator usage` | Show usage telemetry for ALL skills (built-in, hub, agent) with provenance | `hermes_curator_usage` | skills | read | no | --json |
| `hermes skills audit` | Re-scan installed hub skills | `hermes_skills_audit` | skills | read | no | read-only rescan |
| `hermes skills browse` | Browse all available skills (paginated) | `hermes_skills_browse` | skills | read | no | no --json → parse text |
| `hermes skills check` | Check installed hub skills for updates | `hermes_skills_check` | skills | read | no | no --json → parse text |
| `hermes skills config` | Interactive skill configuration — enable/disable individual skills | `hermes_skills_config` | skills | write | no | interactive toggles |
| `hermes skills diff` | Print a unified diff between your local copy of a bundled skill and the current bundled (s | `hermes_skills_diff` | skills | read | no | read-only unified diff |
| `hermes skills inspect` | Preview a skill without installing | `hermes_skills_inspect` | skills | read | no | no --json → parse text |
| `hermes skills install` | Install a skill | `hermes_skills_install` | skills | write | yes | fetches from a registry/git; writes skills/ |
| `hermes skills list` | List installed skills | `hermes_skills_list` | skills | read | no | no --json → parse text |
| `hermes skills list-modified` | Show the bundled skills whose local copy differs from the version last synced, i.e. the on | `hermes_skills_list_modified` | skills | read | no | --json |
| `hermes skills opt-in` | Remove the .no-bundled-skills marker so bundled skills are seeded again on the next `herme | `hermes_skills_opt_in` | skills | write | no | no --json → parse text |
| `hermes skills opt-out` | Write the .no-bundled-skills marker so the installer, `hermes update`, and any direct sync | `hermes_skills_opt_out` | skills | write | no | no --json → parse text |
| `hermes skills publish` | Publish a skill to a registry | `hermes_skills_publish` | skills | admin | yes | publishes to a registry (external side effect) |
| `hermes skills repair-official` | Repair official optional skill provenance. By default, only backfills hub metadata for exa | `hermes_skills_repair_official` | skills | admin | yes | no --json → parse text |
| `hermes skills reset` | Clear a bundled skill's entry from the sync manifest (~/.hermes/skills/.bundled_manifest)  | `hermes_skills_reset` | skills | admin | yes | clears a bundled skill's sync-manifest entry |
| `hermes skills search` | Search skill registries | `hermes_skills_search` | skills | read | no | --json |
| `hermes skills snapshot export` | Write a snapshot archive of installed skills to <output> | `hermes_skills_snapshot_export` | skills | write | no | no --json → parse text |
| `hermes skills snapshot import` | Restore skills from a snapshot archive | `hermes_skills_snapshot_import` | skills | admin | yes | no --json → parse text |
| `hermes skills tap add` | Add a skill tap repo | `hermes_skills_tap_add` | skills | write | yes | no --json → parse text |
| `hermes skills tap list` | List configured skill taps (registries) | `hermes_skills_tap_list` | skills | read | no | no --json → parse text |
| `hermes skills tap remove` | Remove a skill tap | `hermes_skills_tap_remove` | skills | admin | yes | no --json → parse text |
| `hermes skills trust` | Trust a project so its repo-local skills (./.hermes/skills, ./.agents/skills) load | `hermes_skills_trust` | skills | write | yes | grants repo-local skills prompt access |
| `hermes skills uninstall` | Remove a hub-installed skill | `hermes_skills_uninstall` | skills | admin | yes | removes a hub-installed skill |
| `hermes skills untrust` | Revoke project-skill trust for a repo | `hermes_skills_untrust` | skills | write | yes | no --json → parse text |
| `hermes skills update` | Update installed hub skills | `hermes_skills_update` | skills | admin | yes | no --json → parse text |
| `hermes doctor` | Diagnose issues with Hermes Agent setup | `hermes_doctor` | status | read | no | read-only diagnostics |
| `hermes dump` | Output a compact, plain-text summary of your Hermes setup that can be copy- pasted into Di | `hermes_dump` | status | read | no | redacted plain text for support |
| `hermes insights` | Analyze session history to show token usage, costs, tool patterns, and activity trends | `hermes_insights` | status | read | no | session analytics; text tables |
| `hermes logs` | View, tail, and filter agent.log / errors.log / gateway.log / gui.log / desktop.log / upda | `hermes_logs` | status | read | no | tail/filter log files; no JSON |
| `hermes prompt-size` | Report the fixed prompt budget for a fresh session: system prompt total, skills index, mem | `hermes_prompt_size` | status | read | no | byte breakdown; --json available; --json |
| `hermes status` | Display status of Hermes Agent components | `hermes_status` | status | read | no | read-only; --json absent, text parse |
| `hermes usage` | Fetch the configured provider's account limits (Codex 5h/weekly windows, plan, banked rese | `hermes_usage` | status | read | no | provider rate-limit windows; --json available; --json |
| `hermes browser close-profile` | Close the browser locking your real profile (asks nothing — run only with the user's expli | `hermes_browser_close_profile` | tools | admin | yes | kills whatever process locks the browser profile |
| `hermes computer-use doctor` | Drive cua-driver's stable `health_report` MCP tool and render its check matrix (TCC permis | `hermes_computer_use_doctor` | tools | read | no | --json |
| `hermes computer-use install` | Install or repair the cua-driver binary (macOS/Windows/Linux) | `hermes_computer_use_install` | tools | write | yes | installs a driver binary; take effect: restart gateway/dashboard |
| `hermes computer-use permissions grant` | Request OS permissions for the cua-driver | `hermes_computer_use_permissions_grant` | tools | admin | yes | OS permission prompts — needs a desktop session |
| `hermes computer-use permissions status` | Show screen-capture/accessibility permission state | `hermes_computer_use_permissions_status` | tools | read | no | --json |
| `hermes computer-use screen install` | Install the virtual-screen backend | `hermes_computer_use_screen_install` | tools | admin | yes | no --json → parse text |
| `hermes computer-use screen start` | Start the virtual screen backend | `hermes_computer_use_screen_start` | tools | admin | yes | no --json → parse text |
| `hermes computer-use screen status` | Show virtual-screen backend status | `hermes_computer_use_screen_status` | tools | read | no | --json |
| `hermes computer-use screen stop` | Stop the virtual screen backend | `hermes_computer_use_screen_stop` | tools | admin | yes | kills the virtual display |
| `hermes computer-use status` | Check the selected cua-driver and its runtime contract | `hermes_computer_use_status` | tools | read | no | no --json → parse text |
| `hermes lsp install` | Install a server binary | `hermes_lsp_install` | tools | write | yes | downloads a language-server binary; take effect: restart gateway/dashboard |
| `hermes lsp install-all` | Install every server with a known auto-install recipe | `hermes_lsp_install_all` | tools | write | no | no --json → parse text |
| `hermes lsp list` | List supported language servers | `hermes_lsp_list` | tools | read | no | no --json → parse text |
| `hermes lsp restart` | Tear down running LSP clients (next edit re-spawns) | `hermes_lsp_restart` | tools | write | no | tears down LSP clients; take effect: restart gateway/dashboard |
| `hermes lsp status` | Show LSP service status | `hermes_lsp_status` | tools | read | no | --json |
| `hermes lsp which` | Print binary path for a server | `hermes_lsp_which` | tools | read | no | no --json → parse text |
| `hermes tools disable` | Disable toolsets or MCP tools | `hermes_tools_disable` | tools | write | yes | can lock the agent out of its own tools; platform-scoped; take effect: restart gateway/dashboard |
| `hermes tools enable` | Enable toolsets or MCP tools | `hermes_tools_enable` | tools | write | yes | platform-scoped toolset matrix; take effect: restart gateway/dashboard |
| `hermes tools list` | Show all tools and their enabled/disabled status | `hermes_tools_list` | tools | read | no | no --json → parse text |
| `hermes tools post-setup` | Run the install/bootstrap hook a tool backend declares — the same step `hermes tools` runs | `hermes_tools_post_setup` | tools | write | no | runs a backend's install hook |

## 3. Dangerous commands (admin only)

| Command | Tool | Why dangerous |
|---|---|---|
| `hermes auth logout` | `hermes_auth_logout` | clears stored provider auth |
| `hermes auth remove` | `hermes_auth_remove` | removes a pooled credential |
| `hermes backup` | `hermes_backup` | reads everything; writes a zip |
| `hermes browser close-profile` | `hermes_browser_close_profile` | kills whatever process locks the browser profile |
| `hermes bundles delete` | `hermes_bundles_delete` | no --json → parse text |
| `hermes claw cleanup` | `hermes_claw_cleanup` | archives leftover OpenClaw dirs; has --dry-run |
| `hermes claw migrate` | `hermes_claw_migrate` | imports OpenClaw settings/keys into Hermes; has --dry-run |
| `hermes codex-runtime migrate` | `hermes_codex_runtime_migrate` | rewrites MCP config managed block; --json; has --dry-run |
| `hermes computer-use permissions grant` | `hermes_computer_use_permissions_grant` | OS permission prompts — needs a desktop session |
| `hermes computer-use screen install` | `hermes_computer_use_screen_install` | no --json → parse text |
| `hermes computer-use screen start` | `hermes_computer_use_screen_start` | no --json → parse text |
| `hermes computer-use screen stop` | `hermes_computer_use_screen_stop` | kills the virtual display |
| `hermes config migrate` | `hermes_config_migrate` | rewrites config.yaml with new options; take effect: restart gateway/dashboard |
| `hermes connectors disconnect` | `hermes_connectors_disconnect` | no --json → parse text |
| `hermes cron remove` | `hermes_cron_remove` | deletes the job + its runs |
| `hermes cron tick` | `hermes_cron_tick` | runs due jobs once and exits (scheduler steal) |
| `hermes curator archive` | `hermes_curator_archive` | no --json → parse text |
| `hermes curator prune` | `hermes_curator_prune` | has --dry-run |
| `hermes curator purge` | `hermes_curator_purge` | has --dry-run |
| `hermes curator restore` | `hermes_curator_restore` | no --json → parse text |
| `hermes curator rollback` | `hermes_curator_rollback` | no --json → parse text |
| `hermes dashboard register` | `hermes_dashboard_register` | registers the install with Nous Portal |
| `hermes debug delete` | `hermes_debug_delete` | no --json → parse text |
| `hermes debug share` | `hermes_debug_share` | uploads logs to a paste service (data leaves host!) |
| `hermes egress disable` | `hermes_egress_disable` | disables the egress firewall; take effect: restart gateway/dashboard |
| `hermes egress install` | `hermes_egress_install` | installs a pinned binary; take effect: restart gateway/dashboard |
| `hermes egress restart` | `hermes_egress_restart` | brief outage; take effect: restart gateway/dashboard |
| `hermes egress setup` | `hermes_egress_setup` | interactive wizard (CA, tokens, config); take effect: restart gateway/dashboard |
| `hermes egress start` | `hermes_egress_start` | starts the managed iron-proxy daemon |
| `hermes egress stop` | `hermes_egress_stop` | stops the egress proxy — kills outbound traffic for dependents |
| `hermes fallback clear` | `hermes_fallback_clear` | take effect: restart gateway/dashboard |
| `hermes gateway install` | `hermes_gateway_install` | writes systemd/launchd unit; host-level change; take effect: restart gateway/dashboard |
| `hermes gateway migrate` | `hermes_gateway_migrate` | stops+uninstalls every other gateway on the host; has --dry-run; take effect: restart gateway/dashboard |
| `hermes gateway migrate-legacy` | `hermes_gateway_migrate_legacy` | removes legacy unit files; has --dry-run; take effect: restart gateway/dashboard |
| `hermes gateway restart` | `hermes_gateway_restart` | REQUIRES restart from an EXTERNAL shell; in-gateway SIGTERM propagation |
| `hermes gateway run` | `hermes_gateway_run` | foreground service — never via MCP; take effect: restart gateway/dashboard |
| `hermes gateway start` | `hermes_gateway_start` | take effect: restart gateway/dashboard |
| `hermes gateway stop` | `hermes_gateway_stop` | kills in-flight turns and the calling gateway; take effect: restart gateway/dashboard |
| `hermes gateway uninstall` | `hermes_gateway_uninstall` | removes the systemd/launchd unit; take effect: restart gateway/dashboard |
| `hermes import` | `hermes_import` | restores a backup INTO ~/.hermes (overwrites) |
| `hermes import-agent` | `hermes_import_agent` | rewrites CLAUDE.md/AGENTS.md mapping; writes into ~/.hermes; has --dry-run |
| `hermes journey delete` | `hermes_journey_delete` | no --json → parse text |
| `hermes kanban boards import` | `hermes_kanban_boards_import` | always lands as a new board; --json |
| `hermes kanban boards rm` | `hermes_kanban_boards_rm` | --delete drops the DB and tasks permanently |
| `hermes kanban daemon` | `hermes_kanban_daemon` | DEPRECATED — dispatcher now lives in the gateway; take effect: restart gateway/dashboard |
| `hermes kanban dispatch` | `hermes_kanban_dispatch` | spawns worker processes; one pass; --json; has --dry-run |
| `hermes kanban gc` | `hermes_kanban_gc` | deletes archived workspaces/events/logs |
| `hermes kanban repair` | `hermes_kanban_repair` | PRAGMA integrity_check (and optional fix); --json |
| `hermes logout` | `hermes_logout` | clears stored credentials + provider config |
| `hermes mcp remove` | `hermes_mcp_remove` | removes server config; take effect: restart gateway/dashboard |
| `hermes mcp serve` | `hermes_mcp_serve` | long-running stdio server |
| `hermes memory reset` | `hermes_memory_reset` | erases MEMORY.md + USER.md; take effect: restart gateway/dashboard |
| `hermes migrate relay` | `hermes_migrate_relay` | rewrites exporter env vars |
| `hermes migrate xai` | `hermes_migrate_xai` | rewrites retiring xAi model refs in config.yaml |
| `hermes moa delete` | `hermes_moa_delete` | no --json → parse text |
| `hermes pairing clear-pending` | `hermes_pairing_clear_pending` | no --json → parse text |
| `hermes pairing revoke` | `hermes_pairing_revoke` | cuts a user's access |
| `hermes pause` | `hermes_pause` | global ESTOP: blocks ALL new cron/kanban/gateway work |
| `hermes peer remove` | `hermes_peer_remove` | peer registration deleted |
| `hermes plugins remove` | `hermes_plugins_remove` | deletes the plugin dir + install record; take effect: restart gateway/dashboard |
| `hermes profile delete` | `hermes_profile_delete` | deletes profile home incl. sessions/state; irreversible |
| `hermes profile import` | `hermes_profile_import` | restores a profile archive over an existing home |
| `hermes profile install` | `hermes_profile_install` | installs a profile distribution from git |
| `hermes profile migrate-identity` | `hermes_profile_migrate_identity` | rewrites session+routing identity |
| `hermes profile purge-identity` | `hermes_profile_purge_identity` | purges sessions/routing rows for a profile |
| `hermes project archive` | `hermes_project_archive` | no --json → parse text |
| `hermes project restore` | `hermes_project_restore` | no --json → parse text |
| `hermes proxy start` | `hermes_proxy_start` | foreground proxy |
| `hermes resume` | `hermes_resume` | lifts the global ESTOP |
| `hermes secrets 1password disable` | `hermes_secrets_1password_disable` | no --json → parse text |
| `hermes secrets 1password setup` | `hermes_secrets_1password_setup` | take effect: restart gateway/dashboard |
| `hermes secrets 1password token` | `hermes_secrets_1password_token` | writes a token |
| `hermes secrets bitwarden disable` | `hermes_secrets_bitwarden_disable` | no --json → parse text |
| `hermes secrets bitwarden setup` | `hermes_secrets_bitwarden_setup` | take effect: restart gateway/dashboard |
| `hermes secrets bitwarden token` | `hermes_secrets_bitwarden_token` | writes a token into .env / secret store |
| `hermes secrets onepassword disable` | `hermes_secrets_onepassword_disable` | no --json → parse text |
| `hermes secrets onepassword setup` | `hermes_secrets_onepassword_setup` | take effect: restart gateway/dashboard |
| `hermes secrets onepassword token` | `hermes_secrets_onepassword_token` | writes a token |
| `hermes serve` | `hermes_serve` | long-running backend server; take effect: restart gateway/dashboard |
| `hermes sessions clean-markers` | `hermes_sessions_clean_markers` | has --dry-run |
| `hermes sessions delete` | `hermes_sessions_delete` | deletes a session row + messages |
| `hermes sessions discard` | `hermes_sessions_discard` | no --json → parse text |
| `hermes sessions import` | `hermes_sessions_import` | pulls Claude Code / Codex CLI conversations |
| `hermes sessions optimize` | `hermes_sessions_optimize` | VACUUM + FTS merge; can lock the DB briefly |
| `hermes sessions optimize-storage` | `hermes_sessions_optimize_storage` | no --json → parse text |
| `hermes sessions prune` | `hermes_sessions_prune` | bulk delete by filter; irreversible; has --dry-run |
| `hermes sessions recover` | `hermes_sessions_recover` | offline recovery on a damaged state.db |
| `hermes sessions repair` | `hermes_sessions_repair` | offline DB repair; back up state.db first |
| `hermes sessions repair-profiles` | `hermes_sessions_repair_profiles` | --json |
| `hermes sessions repair-prompts` | `hermes_sessions_repair_prompts` | --json |
| `hermes sessions repair-routing` | `hermes_sessions_repair_routing` | no --json → parse text |
| `hermes sessions set-journal-mode` | `hermes_sessions_set_journal_mode` | changes on-disk journal mode |
| `hermes skills publish` | `hermes_skills_publish` | publishes to a registry (external side effect) |
| `hermes skills repair-official` | `hermes_skills_repair_official` | no --json → parse text |
| `hermes skills reset` | `hermes_skills_reset` | clears a bundled skill's sync-manifest entry |
| `hermes skills snapshot import` | `hermes_skills_snapshot_import` | no --json → parse text |
| `hermes skills tap remove` | `hermes_skills_tap_remove` | no --json → parse text |
| `hermes skills uninstall` | `hermes_skills_uninstall` | removes a hub-installed skill |
| `hermes skills update` | `hermes_skills_update` | no --json → parse text |
| `hermes uninstall` | `hermes_uninstall` | removes the install; keep configs via flags; has --dry-run |
| `hermes update` | `hermes_update` | git pull + reinstall deps; patches may be lost — plan first; has --plan |
| `hermes vault rm` | `hermes_vault_rm` | deletes a vault item |
| `hermes webhook remove` | `hermes_webhook_remove` | no --json → parse text |
| `hermes worktree prune` | `hermes_worktree_prune` | deletes trees + merged branches; --json; has --dry-run |

## 4. Without JSON output (text parsing needed)

| Command | access | Note |
|---|---|---|
| `hermes auth add` | write | may prompt for a key on stdin |
| `hermes auth logout` | admin | clears stored provider auth |
| `hermes auth priority` | write | no --json → parse text |
| `hermes auth refresh` | write | no --json → parse text |
| `hermes auth remove` | admin | removes a pooled credential |
| `hermes auth reset` | write | clears credential exhaustion state |
| `hermes auth upgrade` | write | Nous sign-in, keeps connectors |
| `hermes backup` | admin | reads everything; writes a zip |
| `hermes browser close-profile` | admin | kills whatever process locks the browser profile |
| `hermes bundles create` | write | no --json → parse text |
| `hermes bundles delete` | admin | no --json → parse text |
| `hermes bundles reload` | write | no --json → parse text |
| `hermes chat` | write | interactive REPL; use -q for one-shot (--format stream-json available), never remotely |
| `hermes claw cleanup` | admin | archives leftover OpenClaw dirs; has --dry-run |
| `hermes claw migrate` | admin | imports OpenClaw settings/keys into Hermes; has --dry-run |
| `hermes computer-use install` | write | installs a driver binary; take effect: restart gateway/dashboard |
| `hermes computer-use permissions grant` | admin | OS permission prompts — needs a desktop session |
| `hermes computer-use screen install` | admin | no --json → parse text |
| `hermes computer-use screen start` | admin | no --json → parse text |
| `hermes computer-use screen stop` | admin | kills the virtual display |
| `hermes config edit` | write | opens $EDITOR — impossible remotely; use config set; take effect: restart gateway/dashboard |
| `hermes config migrate` | admin | rewrites config.yaml with new options; take effect: restart gateway/dashboard |
| `hermes config set` | write | touches ~/.hermes/config.yaml; validate YAML after write; take effect: restart gateway/dashboard |
| `hermes config unset` | write | touches ~/.hermes/config.yaml; take effect: restart gateway/dashboard |
| `hermes connectors connect` | write | no --json → parse text |
| `hermes connectors disconnect` | admin | no --json → parse text |
| `hermes connectors rename` | write | no --json → parse text |
| `hermes cron create` | write | interactive if args omitted; needs schedule |
| `hermes cron edit` | write | no --json → parse text |
| `hermes cron incidents` | write | list or acknowledge failure incidents |
| `hermes cron notepad delete` | write | no --json → parse text |
| `hermes cron notepad set` | write | no --json → parse text |
| `hermes cron pause` | write | no --json → parse text |
| `hermes cron remove` | admin | deletes the job + its runs |
| `hermes cron resume` | write | no --json → parse text |
| `hermes cron run` | write | runs on the next scheduler tick, not immediately |
| `hermes cron tick` | write | runs due jobs once and exits (scheduler steal) |
| `hermes curator adopt` | write | has --dry-run |
| `hermes curator archive` | admin | no --json → parse text |
| `hermes curator backup` | write | no --json → parse text |
| `hermes curator ledger` | write | no --json → parse text |
| `hermes curator pause` | write | no --json → parse text |
| `hermes curator pin` | write | no --json → parse text |
| `hermes curator prune` | admin | has --dry-run |
| `hermes curator purge` | admin | has --dry-run |
| `hermes curator restore` | admin | no --json → parse text |
| `hermes curator resume` | write | no --json → parse text |
| `hermes curator rollback` | admin | no --json → parse text |
| `hermes curator run` | write | has --dry-run |
| `hermes curator unpin` | write | no --json → parse text |
| `hermes dashboard register` | write | registers the install with Nous Portal |
| `hermes debug delete` | admin | no --json → parse text |
| `hermes debug share` | admin | uploads logs to a paste service (data leaves host!) |
| `hermes desktop` | write | launches Electron |
| `hermes egress disable` | admin | disables the egress firewall; take effect: restart gateway/dashboard |
| `hermes egress install` | admin | installs a pinned binary; take effect: restart gateway/dashboard |
| `hermes egress reload` | write | no --json → parse text |
| `hermes egress restart` | admin | brief outage; take effect: restart gateway/dashboard |
| `hermes egress setup` | admin | interactive wizard (CA, tokens, config); take effect: restart gateway/dashboard |
| `hermes egress start` | admin | starts the managed iron-proxy daemon |
| `hermes egress stop` | admin | stops the egress proxy — kills outbound traffic for dependents |
| `hermes fallback add` | write | interactive picker (same as model); take effect: restart gateway/dashboard |
| `hermes fallback clear` | admin | take effect: restart gateway/dashboard |
| `hermes fallback remove` | write | interactive picker; take effect: restart gateway/dashboard |
| `hermes gateway enroll` | write | take effect: restart gateway/dashboard |
| `hermes gateway install` | admin | writes systemd/launchd unit; host-level change; take effect: restart gateway/dashboard |
| `hermes gateway migrate` | admin | stops+uninstalls every other gateway on the host; has --dry-run; take effect: restart gateway/dashboard |
| `hermes gateway migrate-legacy` | admin | removes legacy unit files; has --dry-run; take effect: restart gateway/dashboard |
| `hermes gateway restart` | admin | REQUIRES restart from an EXTERNAL shell; in-gateway SIGTERM propagation |
| `hermes gateway run` | admin | foreground service — never via MCP; take effect: restart gateway/dashboard |
| `hermes gateway setup` | write | take effect: restart gateway/dashboard |
| `hermes gateway start` | admin | take effect: restart gateway/dashboard |
| `hermes gateway stop` | admin | kills in-flight turns and the calling gateway; take effect: restart gateway/dashboard |
| `hermes gateway uninstall` | admin | removes the systemd/launchd unit; take effect: restart gateway/dashboard |
| `hermes hooks test` | write | executes hook scripts |
| `hermes import` | admin | restores a backup INTO ~/.hermes (overwrites) |
| `hermes import-agent` | admin | rewrites CLAUDE.md/AGENTS.md mapping; writes into ~/.hermes; has --dry-run |
| `hermes journey delete` | admin | no --json → parse text |
| `hermes journey edit` | write | no --json → parse text |
| `hermes kanban archive` | write | no --json → parse text |
| `hermes kanban assign` | write | no --json → parse text |
| `hermes kanban attach` | write | no --json → parse text |
| `hermes kanban attach-rm` | write | no --json → parse text |
| `hermes kanban block` | write | no --json → parse text |
| `hermes kanban boards create` | write | no --json → parse text |
| `hermes kanban boards rename` | write | no --json → parse text |
| `hermes kanban boards rm` | admin | --delete drops the DB and tasks permanently |
| `hermes kanban boards set-default-workdir` | write | no --json → parse text |
| `hermes kanban boards switch` | write | no --json → parse text |
| `hermes kanban claim` | write | worker-only; returns workspace path |
| `hermes kanban comment` | write | no --json → parse text |
| `hermes kanban complete` | write | can be applied to many task ids at once |
| `hermes kanban daemon` | admin | DEPRECATED — dispatcher now lives in the gateway; take effect: restart gateway/dashboard |
| `hermes kanban edit` | write | no --json → parse text |
| `hermes kanban gc` | admin | deletes archived workspaces/events/logs |
| `hermes kanban heartbeat` | write | worker liveness only |
| `hermes kanban init` | write | no --json → parse text |
| `hermes kanban link` | write | no --json → parse text |
| `hermes kanban notify-subscribe` | write | binds a gateway source to a task |
| `hermes kanban notify-unsubscribe` | write | no --json → parse text |
| `hermes kanban reassign` | write | no --json → parse text |
| `hermes kanban reclaim` | write | no --json → parse text |
| `hermes kanban reopen-review` | write | no --json → parse text |
| `hermes kanban request-changes` | write | no --json → parse text |
| `hermes kanban request-review` | write | no --json → parse text |
| `hermes kanban schedule` | write | no --json → parse text |
| `hermes kanban set-model` | write | no --json → parse text |
| `hermes kanban unblock` | write | no --json → parse text |
| `hermes kanban unlink` | write | no --json → parse text |
| `hermes kanban watch` | write | no --json → parse text |
| `hermes logout` | admin | clears stored credentials + provider config |
| `hermes lsp install` | write | downloads a language-server binary; take effect: restart gateway/dashboard |
| `hermes lsp install-all` | write | no --json → parse text |
| `hermes lsp restart` | write | tears down LSP clients; take effect: restart gateway/dashboard |
| `hermes mcp add` | write | writes MCP server config; discovery-first; take effect: restart gateway/dashboard |
| `hermes mcp configure` | write | interactive/structured tool toggles |
| `hermes mcp install` | write | take effect: restart gateway/dashboard |
| `hermes mcp login` | write | OAuth browser flow; take effect: restart gateway/dashboard |
| `hermes mcp picker` | write | interactive catalog picker |
| `hermes mcp reauth` | write | OAuth browser flow; take effect: restart gateway/dashboard |
| `hermes mcp remove` | admin | removes server config; take effect: restart gateway/dashboard |
| `hermes mcp serve` | admin | long-running stdio server |
| `hermes memory off` | write | disables external provider; take effect: restart gateway/dashboard |
| `hermes memory reset` | admin | erases MEMORY.md + USER.md; take effect: restart gateway/dashboard |
| `hermes memory setup` | write | interactive provider selection; take effect: restart gateway/dashboard |
| `hermes migrate relay` | admin | rewrites exporter env vars |
| `hermes migrate xai` | admin | rewrites retiring xAi model refs in config.yaml |
| `hermes moa configure` | write | interactive picker |
| `hermes moa delete` | admin | no --json → parse text |
| `hermes model` | write | interactive provider/model picker — not scriptable; take effect: restart gateway/dashboard |
| `hermes pairing approve` | write | authorizes a user on a messaging platform |
| `hermes pairing clear-pending` | admin | no --json → parse text |
| `hermes pairing revoke` | admin | cuts a user's access |
| `hermes pause` | admin | global ESTOP: blocks ALL new cron/kanban/gateway work |
| `hermes peer add` | write | no --json → parse text |
| `hermes peer remove` | admin | peer registration deleted |
| `hermes pets install` | write | downloads from petdex |
| `hermes pets off` | write | no --json → parse text |
| `hermes pets remove` | write | no --json → parse text |
| `hermes pets scale` | write | no --json → parse text |
| `hermes pets select` | write | no --json → parse text |
| `hermes plugins adopt` | write | take effect: restart gateway/dashboard |
| `hermes plugins disable` | write | take effect: restart gateway/dashboard |
| `hermes plugins enable` | write | take effect: restart gateway/dashboard |
| `hermes plugins install` | write | git fetch + write into plugins/; take effect: restart gateway/dashboard |
| `hermes plugins pack install` | write | take effect: restart gateway/dashboard |
| `hermes plugins remove` | admin | deletes the plugin dir + install record; take effect: restart gateway/dashboard |
| `hermes plugins trust-update-url` | write | only path that moves a saved update_url tag |
| `hermes plugins update` | write | pulls latest; can change behaviour live; take effect: restart gateway/dashboard |
| `hermes portal login` | write | OAuth browser flow |
| `hermes portal tools` | write | no --json → parse text |
| `hermes profile alias` | write | creates/removes wrapper scripts on PATH |
| `hermes profile create` | write | no --json → parse text |
| `hermes profile delete` | admin | deletes profile home incl. sessions/state; irreversible |
| `hermes profile describe` | write | no --json → parse text |
| `hermes profile import` | admin | restores a profile archive over an existing home |
| `hermes profile install` | admin | installs a profile distribution from git |
| `hermes profile migrate-identity` | admin | rewrites session+routing identity |
| `hermes profile purge-identity` | admin | purges sessions/routing rows for a profile |
| `hermes profile rename` | write | no --json → parse text |
| `hermes profile update` | write | overwrites distribution-owned files |
| `hermes profile use` | write | changes the sticky default profile host-wide; take effect: restart gateway/dashboard |
| `hermes project add-folder` | write | no --json → parse text |
| `hermes project archive` | admin | no --json → parse text |
| `hermes project bind-board` | write | no --json → parse text |
| `hermes project create` | write | no --json → parse text |
| `hermes project remove-folder` | write | no --json → parse text |
| `hermes project rename` | write | no --json → parse text |
| `hermes project restore` | admin | no --json → parse text |
| `hermes project set-primary` | write | no --json → parse text |
| `hermes project use` | write | no --json → parse text |
| `hermes proxy start` | admin | foreground proxy |
| `hermes resume` | admin | lifts the global ESTOP |
| `hermes secrets 1password disable` | admin | no --json → parse text |
| `hermes secrets 1password remove` | write | no --json → parse text |
| `hermes secrets 1password set` | write | no --json → parse text |
| `hermes secrets 1password setup` | admin | take effect: restart gateway/dashboard |
| `hermes secrets 1password sync` | write | --apply writes .env |
| `hermes secrets 1password token` | admin | writes a token |
| `hermes secrets bitwarden disable` | admin | no --json → parse text |
| `hermes secrets bitwarden install` | write | no --json → parse text |
| `hermes secrets bitwarden setup` | admin | take effect: restart gateway/dashboard |
| `hermes secrets bitwarden sync` | write | --apply writes .env |
| `hermes secrets bitwarden token` | admin | writes a token into .env / secret store |
| `hermes secrets bw` | write | no --json → parse text |
| `hermes secrets onepassword disable` | admin | no --json → parse text |
| `hermes secrets onepassword remove` | write | no --json → parse text |
| `hermes secrets onepassword set` | write | no --json → parse text |
| `hermes secrets onepassword setup` | admin | take effect: restart gateway/dashboard |
| `hermes secrets onepassword sync` | write | --apply writes .env |
| `hermes secrets onepassword token` | admin | writes a token |
| `hermes secrets op` | write | no --json → parse text |
| `hermes serve` | admin | long-running backend server; take effect: restart gateway/dashboard |
| `hermes sessions archive` | write | soft-hide only (no deletion); has --dry-run |
| `hermes sessions clean-markers` | admin | has --dry-run |
| `hermes sessions delete` | admin | deletes a session row + messages |
| `hermes sessions discard` | admin | no --json → parse text |
| `hermes sessions import` | admin | pulls Claude Code / Codex CLI conversations |
| `hermes sessions optimize` | admin | VACUUM + FTS merge; can lock the DB briefly |
| `hermes sessions optimize-storage` | admin | no --json → parse text |
| `hermes sessions pin` | write | no --json → parse text |
| `hermes sessions prune` | admin | bulk delete by filter; irreversible; has --dry-run |
| `hermes sessions recover` | admin | offline recovery on a damaged state.db |
| `hermes sessions rename` | write | no --json → parse text |
| `hermes sessions repair` | admin | offline DB repair; back up state.db first |
| `hermes sessions repair-routing` | admin | no --json → parse text |
| `hermes sessions retitle-skills` | write | no --json → parse text |
| `hermes sessions set-journal-mode` | admin | changes on-disk journal mode |
| `hermes sessions unpin` | write | no --json → parse text |
| `hermes setup agent` | write | wizard section; writes config.yaml |
| `hermes setup gateway` | write | wizard section; writes config.yaml |
| `hermes setup model` | write | wizard section; writes config.yaml |
| `hermes setup telemetry` | write | wizard section; writes config.yaml |
| `hermes setup terminal` | write | wizard section; writes config.yaml |
| `hermes setup tools` | write | wizard section; writes config.yaml |
| `hermes setup tts` | write | wizard section; writes config.yaml |
| `hermes skills config` | write | interactive toggles |
| `hermes skills install` | write | fetches from a registry/git; writes skills/ |
| `hermes skills opt-in` | write | no --json → parse text |
| `hermes skills opt-out` | write | no --json → parse text |
| `hermes skills publish` | admin | publishes to a registry (external side effect) |
| `hermes skills repair-official` | admin | no --json → parse text |
| `hermes skills reset` | admin | clears a bundled skill's sync-manifest entry |
| `hermes skills snapshot export` | write | no --json → parse text |
| `hermes skills snapshot import` | admin | no --json → parse text |
| `hermes skills tap add` | write | no --json → parse text |
| `hermes skills tap remove` | admin | no --json → parse text |
| `hermes skills trust` | write | grants repo-local skills prompt access |
| `hermes skills uninstall` | admin | removes a hub-installed skill |
| `hermes skills untrust` | write | no --json → parse text |
| `hermes skills update` | admin | no --json → parse text |
| `hermes skin set` | write | writes a colour into config |
| `hermes skin use` | write | writes display.skin |
| `hermes tools disable` | write | can lock the agent out of its own tools; platform-scoped; take effect: restart gateway/dashboard |
| `hermes tools enable` | write | platform-scoped toolset matrix; take effect: restart gateway/dashboard |
| `hermes tools post-setup` | write | runs a backend's install hook |
| `hermes uninstall` | admin | removes the install; keep configs via flags; has --dry-run |
| `hermes update` | admin | git pull + reinstall deps; patches may be lost — plan first; has --plan |
| `hermes vault add` | write | stores a credential in the encrypted vault |
| `hermes vault rm` | admin | deletes a vault item |
| `hermes webhook remove` | admin | no --json → parse text |
| `hermes webhook subscribe` | write | no --json → parse text |
| `hermes webhook test` | write | no --json → parse text |
| `hermes whatsapp` | write | QR-code pairing, needs a terminal |
| `hermes whatsapp-cloud` | write | no --json → parse text |

## 5. Effective only after a gateway/dashboard restart

| Command | Tool | Toolset |
|---|---|---|
| `hermes computer-use install` | `hermes_computer_use_install` | tools |
| `hermes config edit` | `hermes_config_edit` | config |
| `hermes config migrate` | `hermes_config_migrate` | config |
| `hermes config set` | `hermes_config_set` | config |
| `hermes config unset` | `hermes_config_unset` | config |
| `hermes egress disable` | `hermes_egress_disable` | ops |
| `hermes egress install` | `hermes_egress_install` | ops |
| `hermes egress restart` | `hermes_egress_restart` | ops |
| `hermes egress setup` | `hermes_egress_setup` | ops |
| `hermes fallback add` | `hermes_fallback_add` | models |
| `hermes fallback clear` | `hermes_fallback_clear` | models |
| `hermes fallback remove` | `hermes_fallback_remove` | models |
| `hermes gateway enroll` | `hermes_gateway_enroll` | ops |
| `hermes gateway ensure` | `hermes_gateway_ensure` | ops |
| `hermes gateway install` | `hermes_gateway_install` | ops |
| `hermes gateway migrate` | `hermes_gateway_migrate` | ops |
| `hermes gateway migrate-legacy` | `hermes_gateway_migrate_legacy` | ops |
| `hermes gateway restart` | `hermes_gateway_restart` | ops |
| `hermes gateway run` | `hermes_gateway_run` | ops |
| `hermes gateway setup` | `hermes_gateway_setup` | ops |
| `hermes gateway start` | `hermes_gateway_start` | ops |
| `hermes gateway stop` | `hermes_gateway_stop` | ops |
| `hermes gateway uninstall` | `hermes_gateway_uninstall` | ops |
| `hermes kanban daemon` | `hermes_kanban_daemon` | kanban |
| `hermes lsp install` | `hermes_lsp_install` | tools |
| `hermes lsp restart` | `hermes_lsp_restart` | tools |
| `hermes mcp add` | `hermes_mcp_add` | mcp |
| `hermes mcp install` | `hermes_mcp_install` | mcp |
| `hermes mcp login` | `hermes_mcp_login` | mcp |
| `hermes mcp reauth` | `hermes_mcp_reauth` | mcp |
| `hermes mcp remove` | `hermes_mcp_remove` | mcp |
| `hermes memory off` | `hermes_memory_off` | memory |
| `hermes memory reset` | `hermes_memory_reset` | memory |
| `hermes memory setup` | `hermes_memory_setup` | memory |
| `hermes model` | `hermes_model` | models |
| `hermes plugins adopt` | `hermes_plugins_adopt` | plugins |
| `hermes plugins disable` | `hermes_plugins_disable` | plugins |
| `hermes plugins enable` | `hermes_plugins_enable` | plugins |
| `hermes plugins install` | `hermes_plugins_install` | plugins |
| `hermes plugins pack install` | `hermes_plugins_pack_install` | plugins |
| `hermes plugins remove` | `hermes_plugins_remove` | plugins |
| `hermes plugins update` | `hermes_plugins_update` | plugins |
| `hermes profile use` | `hermes_profile_use` | profiles |
| `hermes secrets 1password setup` | `hermes_secrets_1password_setup` | memory |
| `hermes secrets bitwarden setup` | `hermes_secrets_bitwarden_setup` | memory |
| `hermes secrets onepassword setup` | `hermes_secrets_onepassword_setup` | memory |
| `hermes serve` | `hermes_serve` | ops |
| `hermes tools disable` | `hermes_tools_disable` | tools |
| `hermes tools enable` | `hermes_tools_enable` | tools |

## 6. Proposed core tools (offered directly)

Only this list is offered to an MCP client directly; everything else from §2 goes through
`hermes_search_tools` → `hermes_describe_tool` → `hermes_call` (with the identical scope check).

| Tool | Toolset | access | Purpose |
|---|---|---|---|
| `hermes_status` | status | read | Lage des Systems: Gateway, Version, Health |
| `hermes_doctor` | status | read | Diagnose-Report (read-only) |
| `hermes_usage` | status | read | Token-/Kosten-Übersicht |
| `hermes_config_get` | config | read | Config-Wert lesen (Secrets maskiert) |
| `hermes_config_set` | config | write | Config-Wert setzen (mit dry_run-Diff) |
| `hermes_model` | models | read | aktives Modell + Alias-Liste |
| `hermes_model_set` | models | write | Modell/Alias setzen |
| `hermes_fallback_list` | models | read | Fallback-Kette anzeigen |
| `hermes_fallback_add` | models | write | Fallback-Anbieter hinzufügen |
| `hermes_sessions_list` | sessions | read | letzte Sessions |
| `hermes_sessions_show` | sessions | read | Session-Inhalt lesen |
| `hermes_chat_send` | chat | write | Nachricht an einen Agenten senden |
| `hermes_run_status` | chat | read | Status eines gestarteten Laufs |
| `hermes_run_approval` | approvals | write | Freigabe für einen hermcp-Lauf (run-scoped) |
| `hermes_tools_list` | tools | read | Tool-Status |
| `hermes_tools_enable` | tools | write | Tool/Toolset aktivieren |
| `hermes_tools_disable` | tools | write | Tool/Toolset deaktivieren |
| `hermes_plugins_list` | plugins | read | installierte Plugins |
| `hermes_plugins_enable` | plugins | write | Plugin aktivieren/deaktivieren |
| `hermes_skills_list` | skills | read | verfügbare Skills |
| `hermes_mcp_list` | mcp | read | MCP-Server-Status |
| `hermes_mcp_add` | mcp | write | MCP-Server hinzufügen |
| `hermes_profile_list` | profiles | read | Profile mit Status |
| `hermes_profile_show` | profiles | read | Profil-Details |
| `hermes_kanban_list` | kanban | read | Karten auflisten |
| `hermes_kanban_show` | kanban | read | Karte lesen |
| `hermes_kanban_create` | kanban | write | Karte anlegen |
| `hermes_kanban_complete` | kanban | write | Karte abschließen |
| `hermes_kanban_comment` | kanban | write | Kommentar an Karte |
| `hermes_cron_list` | cron | read | Cron-Jobs auflisten |
| `hermes_cron_create` | cron | write | Cron-Job anlegen |
| `hermes_cron_run` | cron | write | Cron-Job jetzt ausführen |
| `hermes_memory_status` | memory | read | Memory-Provider-Status |
| `hermes_logs` | ops | read | Log-Tail |
| `hermes_whoami` | meta | read | eigene Scopes/Identität |
| `hermes_search_tools` | meta | read | Tool-Katalog durchsuchen |
| `hermes_describe_tool` | meta | read | Tool-Schema + Doku |
| `hermes_call` | meta | write | Tool aus dem Katalog aufrufen (Scope-Check) |
| `hermes_keys_list` | admin | admin | eigene Keys/Scopes anzeigen |
| `hermes_audit_tail` | admin | admin | Audit-Log-Tail |

**40 core tools**, 383 catalog tools in total.

Rationale: exactly one read tool per read toolset, one write tool per write toolset (plus
`dry_run`), and `hermes_call` as pass-through for the rest. This keeps the tool list small
enough for voice modes and token budgets without losing capability.