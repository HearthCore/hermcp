# Profile-bound keys

A key created with `hermcp keys create --profile <P> --access read|write` is limited to profile `<P>`: profile-aware tools
run against P and refuse any other `profile` argument with `forbidden` (message names the pinned and the requested
profile, no data). Tools that are not profile-aware are only reachable when classified as filtered or open below.

Regenerate with `python -m hermcp docs-bound-keys` (or `hermcp keys policy` for the terminal version).

| Tool | Access | Bound key | Why |
|---|---|---|---|
| `audit_tail` | admin | no | global audit log of all keys |
| `keys_list` | admin | no | key administration |
| `approvals_answer` | write | yes (own profile) | a session_key of another profile is refused before the bridge is called |
| `approvals_bridge_status` | read | yes (own profile) | ping only (reachable, pid); no approval data |
| `approvals_pending` | read | yes (own profile) | per-profile socket; on a shared multiplex socket only the sessions whose key namespace is this profile's are listed |
| `chat_conversations` | read | yes (own profile) | profile argument forced to the bound profile |
| `chat_history` | read | yes (own profile) | profile argument forced to the bound profile |
| `chat_run_approval` | write | yes (own profile) | same |
| `chat_run_status` | read | yes (own profile) | run must belong to the key's profile (hermcp watch record) |
| `chat_run_stop` | write | yes (own profile) | same |
| `chat_send` | write | yes (own profile) | profile argument forced to the bound profile |
| `config_backups` | read | yes (own profile) | profile argument forced to the bound profile |
| `config_check` | read | yes (own profile) | profile argument forced to the bound profile |
| `config_get` | read | yes (own profile) | profile argument forced to the bound profile |
| `config_restore` | admin | no | admin tools are not available to a key bound to one profile |
| `config_set` | write | yes (own profile) | profile argument forced to the bound profile |
| `config_unset` | write | yes (own profile) | profile argument forced to the bound profile |
| `cron_create` | write | yes (own profile) | profile argument forced to the bound profile |
| `cron_edit` | write | yes (own profile) | profile argument forced to the bound profile |
| `cron_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `cron_model_set` | admin | no | admin tools are not available to a key bound to one profile |
| `cron_models_bulk` | admin | no | admin tools are not available to a key bound to one profile |
| `cron_pause` | write | yes (own profile) | profile argument forced to the bound profile |
| `cron_remove` | admin | no | admin tools are not available to a key bound to one profile |
| `cron_resume` | write | yes (own profile) | profile argument forced to the bound profile |
| `cron_run` | write | yes (own profile) | profile argument forced to the bound profile |
| `cron_runs` | read | yes (own profile) | profile argument forced to the bound profile |
| `cron_show` | read | yes (own profile) | profile argument forced to the bound profile |
| `cron_status` | read | yes (own profile) | profile argument forced to the bound profile |
| `inbox_ack` | read | filtered | only such events |
| `inbox_list` | read | filtered | only run-watcher events of runs on the key's profile(s) |
| `inbox_poll` | read | filtered | only run-watcher events of runs on the key's profile(s) |
| `inbox_push` | read | no | producer scope for global events |
| `runs_active` | read | filtered | only runs of this key on its profile(s) |
| `kanban_archive` | admin | no | admin |
| `kanban_assign` | write | filtered | only to the key's own profile (no hand-off to other profiles) |
| `kanban_assignees` | read | no | lists all profiles with task counts |
| `kanban_block` | write | filtered | only tasks of the key's profile(s) |
| `kanban_board_create` | admin | no | admin |
| `kanban_boards` | read | no | boards and counts are global |
| `kanban_comment` | write | filtered | only tasks of the key's profile(s) |
| `kanban_complete` | write | filtered | only tasks of the key's profile(s) |
| `kanban_create` | write | filtered | assignee forced to the key's profile; parents must be its own tasks |
| `kanban_edit` | write | filtered | only tasks of the key's profile(s) |
| `kanban_list` | read | filtered | forced to tasks assigned to the key's profile(s) |
| `kanban_log` | read | filtered | only worker logs of tasks assigned to the key's profile(s) |
| `kanban_show` | read | filtered | only tasks assigned to the key's profile(s); links to foreign tasks are counted, not shown |
| `kanban_stats` | read | no | counts over all profiles' tasks |
| `kanban_unblock` | write | filtered | only tasks of the key's profile(s) |
| `mcp_add` | write | yes (own profile) | profile argument forced to the bound profile |
| `mcp_disable` | write | yes (own profile) | profile argument forced to the bound profile |
| `mcp_enable` | write | yes (own profile) | profile argument forced to the bound profile |
| `mcp_get` | read | yes (own profile) | profile argument forced to the bound profile |
| `mcp_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `mcp_remove` | admin | no | admin tools are not available to a key bound to one profile |
| `mcp_test` | write | yes (own profile) | profile argument forced to the bound profile |
| `memory_journey_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `memory_off` | write | yes (own profile) | profile argument forced to the bound profile |
| `memory_reset` | admin | no | admin tools are not available to a key bound to one profile |
| `memory_status` | read | yes (own profile) | profile argument forced to the bound profile |
| `hermes_call` | read | yes | wrapper: re-enters the registry with the same key, so every check applies again |
| `hermes_describe_tool` | read | yes | only describes tools the key may call |
| `hermes_search_tools` | read | yes | only lists tools the key may call |
| `hermes_whoami` | read | yes | shows the key's own scopes |
| `models_alias_remove` | write | yes (own profile) | profile argument forced to the bound profile |
| `models_alias_set` | write | yes (own profile) | profile argument forced to the bound profile |
| `models_auxiliary` | read | yes (own profile) | profile argument forced to the bound profile |
| `models_auxiliary_set` | write | yes (own profile) | profile argument forced to the bound profile |
| `models_current` | read | yes (own profile) | profile argument forced to the bound profile |
| `models_fallback_add` | admin | no | admin tools are not available to a key bound to one profile |
| `models_fallback_remove` | admin | no | admin tools are not available to a key bound to one profile |
| `models_fallbacks` | read | yes (own profile) | profile argument forced to the bound profile |
| `models_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `models_providers` | read | no | global provider list |
| `models_set` | write | yes (own profile) | profile argument forced to the bound profile |
| `gateway_restart` | admin | no | admin, affects every profile |
| `dashboard_status` | read | no | machine-wide dashboard process, unit and ports |
| `dashboard_restart` | admin | no | admin, restarts the shared dashboard of every profile |
| `logs_tail` | read | yes (own profile) | own profile logs only; gateway/agent/errors of the default profile are the shared gateway log (denied); worker:<id> only for own tasks |
| `ops_gateway_logs` | read | no | systemd journal of the shared gateway |
| `hooks_list` | read | yes (own profile) | own profile hooks; shared plugin manifests by name only |
| `plugin_config_get` | read | yes (own profile) | profile config section and own plugin dir; shared plugin dirs: manifest only |
| `plugins_check_updates` | read | no | global plugin inventory |
| `plugins_disable` | write | yes (own profile) | profile argument forced to the bound profile |
| `plugins_enable` | write | yes (own profile) | profile argument forced to the bound profile |
| `plugins_info` | read | yes (own profile) | profile argument forced to the bound profile |
| `plugins_install` | admin | no | admin, global code |
| `plugins_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `plugins_remove` | admin | no | admin, global code |
| `plugins_update` | admin | no | admin, global code |
| `profile_files_read` | read | yes (own profile) | profile argument forced to the bound profile |
| `profiles_create` | admin | no | admin, creates other profiles |
| `profiles_delete` | admin | no | admin |
| `profiles_describe` | write | no | global write (routing description) |
| `profiles_list` | read | filtered | only the key's profile(s) |
| `profiles_rename` | admin | no | admin |
| `profiles_show` | read | filtered | refuses other profiles |
| `provider_auth_cancel` | write | no | global provider credentials |
| `provider_auth_continue` | write | no | global provider credentials |
| `provider_auth_start` | write | no | global provider credentials |
| `provider_auth_status` | read | no | global provider credentials |
| `providers_add` | admin | no | admin, global |
| `providers_list` | read | no | global provider list |
| `secret_delete` | admin | no | admin tools are not available to a key bound to one profile |
| `secret_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `secret_set` | write | yes (own profile) | profile argument forced to the bound profile |
| `secrets_status` | read | yes (own profile) | own profile .env and config only; no default .env, no server environment |
| `vault_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `sessions_archive` | write | yes (own profile) | profile argument forced to the bound profile |
| `sessions_delete` | admin | no | admin tools are not available to a key bound to one profile |
| `sessions_export` | read | yes (own profile) | profile argument forced to the bound profile |
| `sessions_get` | read | yes (own profile) | profile argument forced to the bound profile |
| `sessions_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `sessions_messages` | read | yes (own profile) | profile argument forced to the bound profile |
| `sessions_pin` | write | yes (own profile) | profile argument forced to the bound profile |
| `sessions_rename` | write | yes (own profile) | profile argument forced to the bound profile |
| `sessions_search` | read | yes (own profile) | profile argument forced to the bound profile |
| `skills_disable` | write | yes (own profile) | profile argument forced to the bound profile |
| `skills_edit` | write | yes (own profile) | profile argument forced to the bound profile |
| `skills_enable` | write | yes (own profile) | profile argument forced to the bound profile |
| `skills_install` | admin | no | admin tools are not available to a key bound to one profile |
| `skills_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `skills_search` | read | yes | public skill registries, no Hermes state |
| `skills_uninstall` | admin | no | admin tools are not available to a key bound to one profile |
| `skills_view` | read | yes (own profile) | profile argument forced to the bound profile |
| `hermes_health` | read | filtered | profiles, platforms, served/parked reduced to the key's profile(s) |
| `orchestration_overview` | read | filtered | profiles, cron, MCP, plugins, gateway, runs reduced to the key's profile(s); no kanban config or boards |
| `status_doctor` | read | yes (own profile) | profile argument forced to the bound profile |
| `status_gateway` | read | yes (own profile) | platforms of other profiles are not shown |
| `status_usage` | read | yes (own profile) | profile argument forced to the bound profile |
| `status_version` | read | yes | version string only |
| `tools_disable` | write | yes (own profile) | profile argument forced to the bound profile |
| `tools_enable` | write | yes (own profile) | profile argument forced to the bound profile |
| `tools_list` | read | yes (own profile) | profile argument forced to the bound profile |
| `tools_summary` | read | yes (own profile) | profile argument forced to the bound profile |

Not available to a key bound to one profile: every admin tool (`access=admin`), all `keys_*`, `audit_tail`,
`gateway_restart`, `ops_gateway_logs`, provider credentials, profile administration, board-level kanban views, and
global plugin inventory. `approvals_pending`/`approvals_answer` work on both gateway modes: with one socket per
profile home (separate gateways) the socket itself is the boundary; on a multiplex gateway's single shared socket
only the sessions whose key namespace is the key's profile are listed and answered, and a default-bound key sees
only `agent:main:*` (outside multiplex mode such a key is refused, because the socket is shared by every profile).
