# Tool annotations

Generated from the live registry by `python -m hermcp docs` — do not edit by hand.

Every tool carries a `title` and the four MCP `ToolAnnotations` hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). A client sees them at `tools/list`, and for the tools that are not advertised directly through `hermes_search_tools` / `hermes_describe_tool`.

**Hints are advisory; scopes enforce.** The hints tell a client what kind of prompt to show before it calls a tool. What a key may actually call is decided by its scopes (`access`, `toolsets`, `deny_tools`, plus the explicit `approvals`, `dangerous`, `secrets_write`, `inbox_push` flags), and `tools/list` only advertises what that key may use. Never read a permission out of a hint.

Class codes (the four booleans, so they cannot drift apart):

| code | readOnly | destructive | idempotent | openWorld | meaning |
|---|---|---|---|---|---|
| `R` | yes | no | yes | no | read-only, idempotent, own network |
| `R-o` | yes | no | yes | yes | read-only, idempotent, reaches the internet |
| `C` | no | no | no | no | additive write (create/send/queue) |
| `C-i` | no | no | yes | no | additive write, repeating it changes nothing |
| `C-o` | no | no | no | yes | additive write that talks to the internet |
| `D` | no | yes | no | no | destructive write (delete/overwrite/abort) |
| `D-i` | no | yes | yes | no | destructive write, repeating it is a no-op |
| `G` | no | yes | no | yes | generic dispatcher: conservative union of everything it can reach |

## Where the hints disagree with the declared access

The registry tags a tool `read` / `write` / `admin`; the hint says what the call really does. These are the tools where the two disagree — each one is deliberate, and each is gated by a scope (never by the annotation):

| tool | declared access | class | why the hint differs |
|---|---|---|---|
| `audit_tail` | admin | `R` | access=admin, but the call only reads the audit log |
| `keys_list` | admin | `R` | access=admin, but the call only lists key names and scopes |
| `inbox_ack` | read | `C-i` | access=read, but it marks this consumer's inbox events read |
| `inbox_push` | read | `C` | access=read, but it appends an event to another consumer's inbox (inbox_push scope) |
| `session_card_set` | read | `C-i` | access=read, but it registers or updates a session card |
| `hermes_call` | read | `G` | access=read, but it dispatches any tool in the caller's scope, admin included |

## Every tool

| tool | toolset | access | class | readOnly | destructive | idempotent | openWorld | title |
|---|---|---|---|---|---|---|---|---|
| `audit_tail` | admin | admin | `R` | yes | no | yes | no | Tail the audit log |
| `capture_report` | admin | admin | `C` | no | no | no | no | Report captured client requests |
| `keys_list` | admin | admin | `R` | yes | no | yes | no | List API keys |
| `approvals_answer` | approvals | write | `D` | no | yes | no | no | Approvals answer |
| `approvals_bridge_status` | approvals | read | `R` | yes | no | yes | no | Approvals bridge status |
| `approvals_pending` | approvals | read | `R` | yes | no | yes | no | Approvals pending |
| `chat_conversations` | chat | read | `R` | yes | no | yes | no | Chat conversations |
| `chat_history` | chat | read | `R` | yes | no | yes | no | Chat history |
| `chat_run_approval` | chat | write | `D` | no | yes | no | no | Chat run approval |
| `chat_run_cancel` | chat | write | `D-i` | no | yes | yes | no | Chat run cancel |
| `chat_run_status` | chat | read | `R` | yes | no | yes | no | Chat run status |
| `chat_run_steer` | chat | write | `C` | no | no | no | no | Chat run steer |
| `chat_run_stop` | chat | write | `D-i` | no | yes | yes | no | Chat run stop |
| `chat_send` | chat | write | `C` | no | no | no | no | Chat send |
| `chat_session_queue` | chat | read | `R` | yes | no | yes | no | Chat session queue |
| `config_backups` | config | read | `R` | yes | no | yes | no | Config backups |
| `config_check` | config | read | `R` | yes | no | yes | no | Config check |
| `config_get` | config | read | `R` | yes | no | yes | no | Config get |
| `config_restore` | config | admin | `D` | no | yes | no | no | Config restore |
| `config_set` | config | write | `D-i` | no | yes | yes | no | Config set |
| `config_unset` | config | write | `D-i` | no | yes | yes | no | Config unset |
| `cron_create` | cron | write | `C` | no | no | no | no | Cron create |
| `cron_edit` | cron | write | `C-i` | no | no | yes | no | Cron edit |
| `cron_list` | cron | read | `R` | yes | no | yes | no | Cron list |
| `cron_model_set` | cron | admin | `C-i` | no | no | yes | no | Cron model set |
| `cron_models_bulk` | cron | admin | `D` | no | yes | no | no | Cron models bulk |
| `cron_pause` | cron | write | `C-i` | no | no | yes | no | Cron pause |
| `cron_remove` | cron | admin | `D` | no | yes | no | no | Cron remove |
| `cron_resume` | cron | write | `C` | no | no | no | no | Cron resume |
| `cron_run` | cron | write | `C` | no | no | no | no | Cron run |
| `cron_run_output` | cron | read | `R` | yes | no | yes | no | Cron run output |
| `cron_runs` | cron | read | `R` | yes | no | yes | no | Cron runs |
| `cron_show` | cron | read | `R` | yes | no | yes | no | Cron show |
| `cron_status` | cron | read | `R` | yes | no | yes | no | Cron status |
| `approval_detail` | inbox | read | `R` | yes | no | yes | no | Approval detail |
| `clients_overview` | inbox | read | `R` | yes | no | yes | no | Sessions by client |
| `inbox_ack` | inbox | read | `C-i` | no | no | yes | no | Acknowledge inbox events |
| `inbox_attention` | inbox | read | `R` | yes | no | yes | no | Inbox attention |
| `inbox_list` | inbox | read | `R` | yes | no | yes | no | Inbox list |
| `inbox_poll` | inbox | read | `R` | yes | no | yes | no | Inbox poll |
| `inbox_push` | inbox | read | `C` | no | no | no | no | Push an inbox event |
| `runs_active` | inbox | read | `R` | yes | no | yes | no | List active runs |
| `session_card_set` | inbox | read | `C-i` | no | no | yes | no | Register or update a session card |
| `session_cards_list` | inbox | read | `R` | yes | no | yes | no | List session cards |
| `kanban_archive` | kanban | admin | `D` | no | yes | no | no | Kanban archive |
| `kanban_assign` | kanban | write | `C` | no | no | no | no | Kanban assign |
| `kanban_assignees` | kanban | read | `R` | yes | no | yes | no | Kanban assignees |
| `kanban_block` | kanban | write | `C` | no | no | no | no | Kanban block |
| `kanban_board_create` | kanban | admin | `C` | no | no | no | no | Kanban board create |
| `kanban_boards` | kanban | read | `R` | yes | no | yes | no | Kanban boards |
| `kanban_comment` | kanban | write | `C` | no | no | no | no | Kanban comment |
| `kanban_complete` | kanban | write | `C-i` | no | no | yes | no | Kanban complete |
| `kanban_create` | kanban | write | `C` | no | no | no | no | Kanban create |
| `kanban_edit` | kanban | write | `C` | no | no | no | no | Kanban edit |
| `kanban_list` | kanban | read | `R` | yes | no | yes | no | Kanban list |
| `kanban_log` | kanban | read | `R` | yes | no | yes | no | Kanban log |
| `kanban_show` | kanban | read | `R` | yes | no | yes | no | Kanban show |
| `kanban_stats` | kanban | read | `R` | yes | no | yes | no | Kanban stats |
| `kanban_unblock` | kanban | write | `C` | no | no | no | no | Kanban unblock |
| `mcp_add` | mcp | write | `C-i` | no | no | yes | no | MCP add |
| `mcp_disable` | mcp | write | `C-i` | no | no | yes | no | MCP disable |
| `mcp_enable` | mcp | write | `C-i` | no | no | yes | no | MCP enable |
| `mcp_get` | mcp | read | `R` | yes | no | yes | no | MCP get |
| `mcp_list` | mcp | read | `R` | yes | no | yes | no | MCP list |
| `mcp_remove` | mcp | admin | `D` | no | yes | no | no | MCP remove |
| `mcp_test` | mcp | write | `C-o` | no | no | no | yes | MCP test |
| `memory_journey_list` | memory | read | `R` | yes | no | yes | no | Memory journey list |
| `memory_off` | memory | write | `C-i` | no | no | yes | no | Memory off |
| `memory_reset` | memory | admin | `D` | no | yes | no | no | Memory reset |
| `memory_status` | memory | read | `R` | yes | no | yes | no | Memory status |
| `hermes_call` | meta | read | `G` | no | yes | no | yes | Call any tool |
| `hermes_describe_tool` | meta | read | `R` | yes | no | yes | no | Describe a tool |
| `hermes_search_tools` | meta | read | `R` | yes | no | yes | no | Search tools |
| `hermes_whoami` | meta | read | `R` | yes | no | yes | no | Show this key's identity |
| `models_alias_remove` | models | write | `C` | no | no | no | no | Models alias remove |
| `models_alias_set` | models | write | `C` | no | no | no | no | Models alias set |
| `models_auxiliary` | models | read | `R` | yes | no | yes | no | Models auxiliary |
| `models_auxiliary_set` | models | write | `C` | no | no | no | no | Models auxiliary set |
| `models_current` | models | read | `R` | yes | no | yes | no | Models current |
| `models_fallback_add` | models | admin | `C-i` | no | no | yes | no | Models fallback add |
| `models_fallback_remove` | models | admin | `D-i` | no | yes | yes | no | Models fallback remove |
| `models_fallbacks` | models | read | `R` | yes | no | yes | no | Models fallbacks |
| `models_list` | models | read | `R-o` | yes | no | yes | yes | Models list |
| `models_providers` | models | read | `R` | yes | no | yes | no | Models providers |
| `models_set` | models | write | `C-i` | no | no | yes | no | Models set |
| `dashboard_restart` | ops | admin | `D` | no | yes | no | no | Dashboard restart |
| `dashboard_status` | ops | read | `R` | yes | no | yes | no | Dashboard status |
| `gateway_restart` | ops | admin | `D` | no | yes | no | no | Gateway restart |
| `hermcp_restart` | ops | admin | `D` | no | yes | no | no | Hermcp restart |
| `logs_tail` | ops | read | `R` | yes | no | yes | no | Tail a log file |
| `ops_gateway_logs` | ops | read | `R` | yes | no | yes | no | Ops gateway logs |
| `hooks_list` | plugins | read | `R` | yes | no | yes | no | Hooks list |
| `plugin_config_get` | plugins | read | `R` | yes | no | yes | no | Plugin config get |
| `plugins_check_updates` | plugins | read | `R-o` | yes | no | yes | yes | Plugins check updates |
| `plugins_disable` | plugins | write | `C` | no | no | no | no | Plugins disable |
| `plugins_enable` | plugins | write | `C` | no | no | no | no | Plugins enable |
| `plugins_info` | plugins | read | `R` | yes | no | yes | no | Plugins info |
| `plugins_install` | plugins | admin | `C-o` | no | no | no | yes | Plugins install |
| `plugins_list` | plugins | read | `R` | yes | no | yes | no | Plugins list |
| `plugins_remove` | plugins | admin | `D` | no | yes | no | no | Plugins remove |
| `plugins_update` | plugins | admin | `C-o` | no | no | no | yes | Plugins update |
| `profile_files_read` | profiles | read | `R` | yes | no | yes | no | Profile files read |
| `profiles_create` | profiles | admin | `C` | no | no | no | no | Profiles create |
| `profiles_delete` | profiles | admin | `D` | no | yes | no | no | Profiles delete |
| `profiles_describe` | profiles | write | `C-i` | no | no | yes | no | Profiles describe |
| `profiles_list` | profiles | read | `R` | yes | no | yes | no | Profiles list |
| `profiles_rename` | profiles | admin | `D-i` | no | yes | yes | no | Profiles rename |
| `profiles_show` | profiles | read | `R` | yes | no | yes | no | Profiles show |
| `provider_auth_cancel` | providers | write | `D-i` | no | yes | yes | no | Provider auth cancel |
| `provider_auth_continue` | providers | write | `C-o` | no | no | no | yes | Provider auth continue |
| `provider_auth_start` | providers | write | `C-o` | no | no | no | yes | Provider auth start |
| `provider_auth_status` | providers | read | `R` | yes | no | yes | no | Provider auth status |
| `providers_add` | providers | admin | `C-i` | no | no | yes | no | Providers add |
| `providers_list` | providers | read | `R` | yes | no | yes | no | Providers list |
| `secret_delete` | providers | admin | `D-i` | no | yes | yes | no | Secret delete |
| `secret_list` | providers | read | `R` | yes | no | yes | no | Secret list |
| `secret_set` | providers | write | `C-i` | no | no | yes | no | Secret set |
| `secrets_status` | providers | read | `R` | yes | no | yes | no | Secrets status |
| `vault_list` | providers | read | `R` | yes | no | yes | no | Vault list |
| `sessions_archive` | sessions | write | `C-i` | no | no | yes | no | Sessions archive |
| `sessions_delete` | sessions | admin | `D` | no | yes | no | no | Sessions delete |
| `sessions_export` | sessions | read | `R` | yes | no | yes | no | Sessions export |
| `sessions_get` | sessions | read | `R` | yes | no | yes | no | Sessions get |
| `sessions_list` | sessions | read | `R` | yes | no | yes | no | Sessions list |
| `sessions_messages` | sessions | read | `R` | yes | no | yes | no | Sessions messages |
| `sessions_pin` | sessions | write | `C-i` | no | no | yes | no | Sessions pin |
| `sessions_rename` | sessions | write | `C-i` | no | no | yes | no | Sessions rename |
| `sessions_search` | sessions | read | `R` | yes | no | yes | no | Sessions search |
| `skills_disable` | skills | write | `C` | no | no | no | no | Skills disable |
| `skills_edit` | skills | write | `D` | no | yes | no | no | Skills edit |
| `skills_enable` | skills | write | `C` | no | no | no | no | Skills enable |
| `skills_install` | skills | admin | `C-o` | no | no | no | yes | Skills install |
| `skills_list` | skills | read | `R` | yes | no | yes | no | Skills list |
| `skills_search` | skills | read | `R-o` | yes | no | yes | yes | Skills search |
| `skills_uninstall` | skills | admin | `D` | no | yes | no | no | Skills uninstall |
| `skills_view` | skills | read | `R` | yes | no | yes | no | Skills view |
| `agents_live` | status | read | `R` | yes | no | yes | no | List live agents |
| `hermes_health` | status | read | `R` | yes | no | yes | no | Health overview |
| `hermes_status` | status | read | `R` | yes | no | yes | no | Level-0 status digest |
| `orchestration_overview` | status | read | `R` | yes | no | yes | no | Orchestration overview |
| `status_doctor` | status | read | `R-o` | yes | no | yes | yes | Run doctor diagnostics |
| `status_gateway` | status | read | `R` | yes | no | yes | no | Gateway status |
| `status_usage` | status | read | `R` | yes | no | yes | no | Usage and cost report |
| `status_version` | status | read | `R` | yes | no | yes | no | Hermes version |
| `tools_disable` | tools | write | `C-i` | no | no | yes | no | Tools disable |
| `tools_enable` | tools | write | `C-i` | no | no | yes | no | Tools enable |
| `tools_list` | tools | read | `R` | yes | no | yes | no | Tools list |
| `tools_summary` | tools | read | `R` | yes | no | yes | no | Tools summary |
