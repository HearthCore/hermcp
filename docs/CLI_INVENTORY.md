# Hermes CLI Inventory

Source: `hermes <cmd> --help` output (read-only, `--help` only). Binary: `/usr/local/lib/hermes-agent/venv/bin/hermes`, version `v0.22.1+683.g848fc0a.dirty (2026.9.24)`.

Conventions:
- Usage lines are copied from argparse output. Multi-line usage is joined onto one line.
- `[...]` = optional, `<...>` = required placeholder in the top-level usage line.
- "No --json" means the subcommand's `--help` shows no `--json` (or equivalent) option.

## Top-level `hermes`

Usage:

```
hermes [-h] [--version] [-z PROMPT] [--usage-file PATH] [-m MODEL] [--provider PROVIDER] [--reasoning LEVEL] [-t TOOLSETS] [--resume SESSION] [--no-restore-cwd] [--in DIR] [--continue [SESSION_NAME]] [--worktree] [--accept-hooks] [--skills SKILLS] [--yolo] [--pass-session-id] [--checkpoints] [--ignore-user-config] [--ignore-rules] [--safe-mode] [--tui] [--native] [--cli] [--dev] <command> ...
```

- `hermes --version` / `hermes -V`: prints a text version block (no JSON).
- `hermes -z PROMPT` / `--oneshot PROMPT`: prints only the final response text. Pair with `--usage-file PATH` to write a JSON usage report. This is not a `--json` flag on the top-level command.
- Global `-p <profile>` / `--profile`: accepted (verified: `hermes -p default status --help` parses and runs). It is not listed in the top-level `options:` block. It appears only in the Examples text as `hermes -p <profile> <cmd>  (also --profile)`.
- No `hermes version` subcommand exists. Version is only reachable via `--version`.
- Top-level commands (names only, all listed by `hermes --help`): chat, model, moa, fallback, worktree, browser, secrets, egress, migrate, codex-runtime, gateway, proxy, lsp, setup, whatsapp, whatsapp-cloud, slack, send, logout, auth, status, pause, resume, cron, webhook, peer, portal, kanban, project, hooks, doctor, verify, security, approvals, dump, debug, backup, checkpoints, import, import-agent, config, skin, console, pairing, skills, bundles, plugins, curator, pets, journey (learning, memory-graph), memory, tools, computer-use, mcp, connectors, sessions, insights, usage, monitoring, claw, vault, update, uninstall, acp, profile, completion, dashboard, serve, desktop (gui), logs, prompt-size.

## status

- `hermes status [-h] [--full] [--deep]`
  - `--full` / `--all`: show every section (keys redacted).
  - `--deep`: run deep checks (implies `--full`).
- No `--json`.

## doctor

- `hermes doctor [-h] [--fix] [--live] [--ack ADVISORY_ID]`
  - `--fix`: attempt automatic fixes.
  - `--live`: run one bounded read-only live probe per configured tool backend (makes network calls).
  - `--ack ADVISORY_ID`: acknowledge a security advisory and exit.
- No `--json`.

## insights

- `hermes insights [-h] [--days DAYS] [--source SOURCE]`
  - `--days DAYS` (default 30), `--source SOURCE` (platform filter).
- No `--json`.

## usage

- `hermes usage [-h] [--provider PROVIDER] [--json]`
  - `--provider PROVIDER`: provider to query (default: configured provider).
  - `--json`: print one JSON document instead of the text block.
- Has `--json`.

## logs

- `hermes logs [-h] [-n LINES] [-f] [--level LEVEL] [--session ID] [--since TIME] [--component NAME] [log_name]`
  - positional `log_name`: `agent` (default), `errors`, `gateway`, `gui`, `desktop`, `update`, `handoff`, or `list`.
  - `-n LINES` / `--lines LINES` (default 50), `-f` / `--follow`, `--level LEVEL` (DEBUG/INFO/WARNING/ERROR), `--session ID`, `--since TIME` (e.g. 1h, 30m, 2d), `--component NAME` (gateway, agent, tools, cli, cron, gui).
- No `--json`. Output is plain log text.

## config

Usage: `hermes config [-h] {show,edit,get,set,unset,path,env-path,check,migrate} ...`

- `hermes config show [-h]`: show current configuration. No `--json`.
- `hermes config edit [-h]`: open config file in `$EDITOR`. Interactive. No `--json`.
- `hermes config get [-h] [--json] [--raw] [key]`: print a resolved value.
  - positional `key` (e.g. `model`).
  - `--json`: print value as JSON.
  - `--raw`: print credential values unmasked.
  - Has `--json`.
- `hermes config set [-h] [--force] [key] [value]`: set a value.
  - positional `key`, `value`.
  - `--force`: write an unknown key.
  - No `--json`.
- `hermes config unset [-h] [key]`: remove a value. positional `key`. No `--json`.
- `hermes config path [-h]`: print config file path. No `--json`.
- `hermes config env-path [-h]`: print `.env` path. No `--json`.
- `hermes config check [-h]`: check for missing/outdated config. No `--json`.
- `hermes config migrate [-h]`: update config with new options. No `--json`.

## model

- `hermes model [-h] [--refresh] [--portal-url PORTAL_URL] [--inference-url INFERENCE_URL] [--client-id CLIENT_ID] [--scope SCOPE] [--no-browser] [--timeout TIMEOUT] [--ca-bundle CA_BUNDLE] [--insecure]`
  - Interactive picker. `--refresh` re-fetches provider model lists. Nous-login flags: `--portal-url`, `--inference-url`, `--client-id`, `--scope`, `--no-browser`, `--timeout`, `--ca-bundle`, `--insecure`.
- No `--json`. Interactive.

## fallback

Usage: `hermes fallback [-h] {list,ls,add,remove,rm,clear} ...`

- `hermes fallback list [-h]` (alias `ls`): show the fallback chain. No `--json`.
- `hermes fallback add [-h]`: pick provider and model, append to chain. Interactive. No `--json`.
- `hermes fallback remove [-h]` (alias `rm`): pick an entry to delete. Interactive. No `--json`.
- `hermes fallback clear [-h]`: remove all entries. No `--json`.

## sessions

Usage: `hermes sessions [-h] {list,export,delete,prune,archive,optimize,clean-markers,optimize-storage,repair,set-journal-mode,repair-routing,repair-profiles,repair-prompts,recover,stats,rename,discard,pin,unpin,pinned,retitle-skills,browse,import} ...`

- `hermes sessions list [-h] [--source SOURCE] [--limit LIMIT] [--workspace NEEDLE]`. No `--json`.
- `hermes sessions export [-h] [--format {jsonl,md,qmd,html,trace}] [--upload] [--public] [--no-redact] [--only {user-prompts}] [--session-id SESSION_ID] [--older-than AGE] [--newer-than AGE] [--before TIME] [--after TIME] [--source SOURCE] [--title TITLE] [--end-reason END_REASON] [--cwd CWD] [--min-messages MIN_MESSAGES] [--max-messages MAX_MESSAGES] [--model MODEL] [--provider PROVIDER] [--user USER] [--chat-id CHAT_ID] [--chat-type CHAT_TYPE] [--branch BRANCH] [--min-tokens MIN_TOKENS] [--max-tokens MAX_TOKENS] [--min-cost MIN_COST] [--max-cost MAX_COST] [--min-tool-calls MIN_TOOL_CALLS] [--max-tool-calls MAX_TOOL_CALLS] [--dry-run] [--yes] [--redact] [--lineage {single,logical}] [--delete-after-verified] [--force] [OUTPUT]`
  - Machine-readable via `--format jsonl` (a format choice, not a `--json` flag). `--format trace` emits Claude Code JSONL. No `--json` flag.
- `hermes sessions delete [-h]`: delete a session. No `--json`.
- `hermes sessions prune [-h]`: delete old sessions (filterable). No `--json`.
- `hermes sessions archive [-h]`: bulk soft-hide sessions. No `--json`.
- `hermes sessions optimize [-h]`: reclaim disk space. No `--json`.
- `hermes sessions clean-markers [-h]`: clear stale tool-call marker content. No `--json`.
- `hermes sessions optimize-storage [-h]`: migrate search index. No `--json`.
- `hermes sessions repair [-h]`: repair malformed state.db schema. No `--json`.
- `hermes sessions set-journal-mode [-h]`: convert state.db journal mode offline. No `--json`.
- `hermes sessions repair-routing [-h]`: re-stamp gateway sessions. No `--json`.
- `hermes sessions repair-profiles [-h]`: settle session/routing state under wrong profile. No `--json`.
- `hermes sessions repair-prompts [-h]`: report or clear degraded system prompts. No `--json`.
- `hermes sessions recover [-h]`: rebuild session data into a separate clean database. No `--json`.
- `hermes sessions stats [-h]`: show session store statistics. No `--json`.
- `hermes sessions rename [-h]`: set or change a session title. No `--json`.
- `hermes sessions discard [-h]`: acknowledge turns lost in a gateway crash. No `--json`.
- `hermes sessions pin [-h]`: pin session(s). No `--json`.
- `hermes sessions unpin [-h]`: unpin session(s). No `--json`.
- `hermes sessions pinned [-h] [--json]`: list pinned sessions. Has `--json`.
- `hermes sessions retitle-skills [-h]`: re-title skill-derived sessions. No `--json`.
- `hermes sessions browse [-h] [--source SOURCE] [--limit LIMIT]`: interactive picker. No `--json`.
- `hermes sessions import [-h]`: import a Claude Code or Codex CLI session. No `--json`.

Note: the per-subcommand `--help` for `delete`, `prune`, `archive`, `optimize`, `clean-markers`, `optimize-storage`, `repair`, `set-journal-mode`, `repair-routing`, `repair-profiles`, `repair-prompts`, `recover`, `stats`, `rename`, `discard`, `pin`, `unpin`, `retitle-skills`, and `import` was checked with a `--json` grep only (no match). Their full usage lines were not individually captured.

## profile

Usage: `hermes profile [-h] {list,use,create,delete,describe,show,alias,rename,purge-identity,migrate-identity,export,import,install,update,info} ...`

- `hermes profile list [-h]`: list profiles. No `--json`.
- `hermes profile use [-h] profile_name`: set sticky default profile. No `--json`.
- `hermes profile create [-h]`: create a profile. No `--json`.
- `hermes profile delete [-h]`: delete a profile. No `--json`.
- `hermes profile describe [-h] [--text TEXT] [--auto] [--overwrite] [--all] [profile_name]`: read or set description. No `--json`.
- `hermes profile show [-h] profile_name`: show profile details. No `--json`.
- `hermes profile alias [-h]`: manage wrapper scripts. No `--json`.
- `hermes profile rename [-h]`: rename a profile. No `--json`.
- `hermes profile purge-identity [-h]`: retry session/routing identity purge. No `--json`.
- `hermes profile migrate-identity [-h]`: retry identity migration. No `--json`.
- `hermes profile export [-h]`: export to archive. No `--json`.
- `hermes profile import [-h]`: import from archive. No `--json`.
- `hermes profile install [-h]`: install a distribution from git URL or local dir. No `--json`.
- `hermes profile update [-h]`: re-pull a distribution. No `--json`.
- `hermes profile info [-h] profile_name`: show distribution manifest. No `--json`.

Note: the `--help` output for `create`, `delete`, `alias`, `rename`, `purge-identity`, `migrate-identity`, `export`, `import`, `install`, and `update` was checked only by a `--json` grep (no match). Usage lines not captured.

## tools

Usage: `hermes tools [-h] [--summary] {list,disable,enable,post-setup} ...`

- `hermes tools [--summary]`: `--summary` prints enabled tools per platform and exits. No `--json`.
- `hermes tools list [-h] [--platform PLATFORM]`: default platform `cli`. No `--json`.
- `hermes tools disable [-h]`: disable toolsets or MCP tools. No `--json`.
- `hermes tools enable [-h]`: enable toolsets or MCP tools. No `--json`.
- `hermes tools post-setup [-h]`: run a provider's post-setup install hook. No `--json`.

## plugins

Usage: `hermes plugins [-h] {install,search,browse,validate,update,adopt,trust-update-url,check-updates,check,remove,rm,uninstall,list,ls,enable,disable,capabilities,doctor,pack,show,info} ...`

- `hermes plugins install [-h]`: install from catalog, Git URL, or `owner/repo`. No `--json`.
- `hermes plugins search [-h]`: search curated catalog. No `--json`.
- `hermes plugins browse [-h]`: list every curated catalog entry. No `--json`.
- `hermes plugins validate [-h]`: validate a plugin directory (CI gate). No `--json`.
- `hermes plugins update [-h]`: pull latest for an installed plugin. No `--json`.
- `hermes plugins adopt [-h]`: adopt a self-cloned plugin dir. No `--json`.
- `hermes plugins trust-update-url [-h]`: confirm a changed update_url. No `--json`.
- `hermes plugins check-updates [-h]` (alias `check`): read-only update check. No `--json`.
- `hermes plugins remove [-h]` (aliases `rm`, `uninstall`): remove a plugin. No `--json`.
- `hermes plugins list [-h] [--enabled] [--user] [--no-bundled] [--plain] [--json]`: list installed plugins. Has `--json`.
- `hermes plugins enable [-h]`: enable a plugin. No `--json`.
- `hermes plugins disable [-h]`: disable a plugin. No `--json`.
- `hermes plugins capabilities [-h]`: declared vs granted capabilities. No `--json`.
- `hermes plugins doctor [-h]`: validate a plugin with runtime contracts. No `--json`.
- `hermes plugins pack [-h]`: declarative plugin sets (`hermes-pack.yaml`). No `--json`.
- `hermes plugins show [-h]` (alias `info`): show details for one plugin. No `--json`.

Note: `hermes plugins ls` is also an alias of `list`, as shown in the help.

## mcp

Usage: `hermes mcp [-h] [--accept-hooks] {serve,add,remove,rm,list,ls,test,configure,config,login,reauth,picker,catalog,install} ...`

- `hermes mcp serve [-h]`: run Hermes as an MCP server. No `--json`.
- `hermes mcp add [-h]`: add an MCP server. No `--json`.
- `hermes mcp remove [-h]` (alias `rm`): remove an MCP server. No `--json`.
- `hermes mcp list [-h]` (alias `ls`): list configured MCP servers. No `--json`.
- `hermes mcp test [-h]`: test MCP server connection. No `--json`.
- `hermes mcp configure [-h]` (alias `config`): toggle tool selection. No `--json`.
- `hermes mcp login [-h]`: force re-auth for an OAuth MCP server. No `--json`.
- `hermes mcp reauth [-h]`: re-auth one server or all (`--all`). No `--json`.
- `hermes mcp picker [-h]`: interactive catalog picker. No `--json`.
- `hermes mcp catalog [-h]`: list Nous-approved MCPs. No `--json`.
- `hermes mcp install [-h]`: install a catalog MCP by name. No `--json`.

## skills

Usage: `hermes skills [-h] {trust,untrust,browse,search,install,inspect,list,check,update,audit,uninstall,reset,list-modified,diff,opt-out,opt-in,repair-official,publish,snapshot,tap,config} ...`

- `hermes skills trust [-h]`: trust a project's repo-local skills. No `--json`.
- `hermes skills untrust [-h]`: revoke project-skill trust. No `--json`.
- `hermes skills browse [-h]`: browse available skills (paginated). No `--json`.
- `hermes skills search [-h] [--source {all,official,skills-sh,well-known,github,clawhub,lobehub,browse-sh,nvidia,openai,anthropic,huggingface,voltagent,gstack,minimax}] [--limit LIMIT] [--json] query`: search registries. Has `--json`.
- `hermes skills install [-h]`: install a skill. No `--json`.
- `hermes skills inspect [-h]`: preview a skill. No `--json`.
- `hermes skills list [-h] [--source {all,hub,builtin,local}] [--enabled-only]`: list installed skills. No `--json`.
- `hermes skills check [-h]`: check hub skills for updates. No `--json`.
- `hermes skills update [-h]`: update hub skills. No `--json`.
- `hermes skills audit [-h]`: re-scan hub skills. No `--json`.
- `hermes skills uninstall [-h]`: remove a hub skill. No `--json`.
- `hermes skills reset [-h]`: reset a bundled skill. No `--json`.
- `hermes skills list-modified [-h]`: list edited bundled skills. No `--json`.
- `hermes skills diff [-h]`: diff a bundled skill against stock. No `--json`.
- `hermes skills opt-out [-h]` / `hermes skills opt-in [-h]`: stop/re-enable bundled-skill seeding. No `--json`.
- `hermes skills repair-official [-h]`: backfill official optional skills. No `--json`.
- `hermes skills publish [-h]`: publish a skill. No `--json`.
- `hermes skills snapshot [-h]`: export/import skill configurations. No `--json`.
- `hermes skills tap [-h]`: manage skill sources. No `--json`.
- `hermes skills config [-h]`: interactive enable/disable per skill. No `--json`.

## kanban

Usage: `hermes kanban [-h] [--board <slug>] {init,boards,create,swarm,list,ls,show,assign,set-model,reclaim,reassign,diagnostics,diag,link,unlink,claim,comment,attach,attachments,attach-rm,complete,edit,block,schedule,unblock,request-review,request-changes,reopen-review,promote,archive,tail,dispatch,daemon,watch,stats,notify-subscribe,notify-list,notify-unsubscribe,log,runs,heartbeat,assignees,context,specify,decompose,gc,repair} ...`

- Global option: `--board <slug>` (applies to all kanban subcommands; defaults to the active board).

- `hermes kanban init [-h]`: create kanban.db if missing. No `--json`.
- `hermes kanban boards [-h] {list,ls,create,new,rm,remove,delete,switch,use,show,current,rename,set-default-workdir,export,import} ...`: manage boards. No `--json` at group level (subcommands not individually checked for `--json`).
- `hermes kanban create [-h] [--body BODY] [--body-file PATH] [--assignee ASSIGNEE] [--parent PARENT] [--workspace WORKSPACE] [--branch BRANCH] [--project PROJECT] [--tenant TENANT] [--priority PRIORITY] [--triage] [--idempotency-key IDEMPOTENCY_KEY] [--max-runtime MAX_RUNTIME] [--created-by CREATED_BY] [--skill SKILLS] [--max-retries N] [--model MODEL_OVERRIDE] [--provider PROVIDER_OVERRIDE] [--completion-contract CONTRACT] [--goal] [--goal-max-turns N] [--initial-status {blocked,running}] [--json] title`: positional `title`. Has `--json`.
- `hermes kanban swarm [-h] [--worker PROFILE:TITLE[:SKILL,SKILL]] --verifier VERIFIER --synthesizer SYNTHESIZER [--tenant TENANT] [--priority PRIORITY] [--created-by CREATED_BY] [--idempotency-key IDEMPOTENCY_KEY] [--json] goal`: positional `goal`. Has `--json`.
- `hermes kanban list [-h] [--mine] [--assignee ASSIGNEE] [--status {archived,blocked,done,ready,review,running,scheduled,todo,triage}] [--tenant TENANT] [--session SESSION] [--archived] [--json] [--sort {assignee,completed-desc,created,created-desc,priority,priority-desc,status,title,updated}] [--workflow-template-id ID] [--step-key KEY]` (alias `ls`). Has `--json`.
- `hermes kanban show [-h] [--json] [--state-type {status,outcome}] [--state-name VALUE] task_id`: positional `task_id`. Has `--json`.
- `hermes kanban assign [-h] task_id profile`: profile name or `none`. No `--json`.
- `hermes kanban set-model [-h] [--provider PROVIDER] task_id [model]`: `model` or `none`. No `--json`.
- `hermes kanban reclaim [-h] [--reason REASON] task_id`: release a running claim. No `--json`.
- `hermes kanban reassign [-h] [--reclaim] [--reason REASON] task_id profile`: No `--json`.
- `hermes kanban diagnostics [-h] [--severity {warning,error,critical}] [--task TASK] [--json]` (alias `diag`): Has `--json`.
- `hermes kanban link [-h] parent_id child_id`: add dependency. No `--json`.
- `hermes kanban unlink [-h] parent_id child_id`: remove dependency. No `--json`.
- `hermes kanban claim [-h] [--ttl TTL] task_id`: claim a ready task. No `--json`.
- `hermes kanban comment [-h] [--author AUTHOR] [--max-len MAX_LEN] task_id text [text ...]`: No `--json`.
- `hermes kanban attach [-h] [--content-type CONTENT_TYPE] [--name NAME] [--author AUTHOR] task_id path`: No `--json`.
- `hermes kanban attachments [-h] [--json] task_id`: Has `--json`.
- `hermes kanban attach-rm [-h] attachment_id`: No `--json`.
- `hermes kanban complete [-h] [--result RESULT] [--summary SUMMARY] [--metadata METADATA] [--force] task_ids [task_ids ...]`: No `--json`.
- `hermes kanban edit [-h] [--title TITLE] [--body BODY] [--priority PRIORITY] [--result RESULT] [--summary SUMMARY] [--metadata METADATA] task_id`: No `--json`.
- `hermes kanban block [-h] [--ids IDS [IDS ...]] [--kind {capability,dependency,needs_input,transient}] task_id [reason ...]`: No `--json`.
- `hermes kanban schedule [-h] [--ids IDS [IDS ...]] task_id [reason ...]`: No `--json`.
- `hermes kanban unblock [-h] [--reason REASON] task_ids [task_ids ...]`: No `--json`.
- `hermes kanban request-review [-h] [--summary SUMMARY] [--reviewer REVIEWER] [--metadata METADATA] [--force] task_id`: No `--json`.
- `hermes kanban request-changes [-h] task_id reason [reason ...]`: No `--json`.
- `hermes kanban reopen-review [-h] [--reason REASON] task_ids [task_ids ...]`: No `--json`.
- `hermes kanban promote [-h] [--ids IDS [IDS ...]] [--dry-run] [--json] task_id [reason ...]`: Has `--json`.
- `hermes kanban archive [-h] [--rm PURGE_IDS [PURGE_IDS ...]] [task_ids ...]`: No `--json`.
- `hermes kanban tail [-h] [--interval INTERVAL] task_id`: follow event stream. No `--json`.
- `hermes kanban dispatch [-h] [--dry-run] [--max MAX] [--failure-limit FAILURE_LIMIT] [--json]`: Has `--json`.
- `hermes kanban daemon`: DEPRECATED (dispatcher runs in gateway; use `hermes gateway start`). Listed in usage only; `--help` not captured.
- `hermes kanban watch [-h] [--assignee ASSIGNEE] [--tenant TENANT] [--kinds KINDS] [--interval INTERVAL]`: live stream. No `--json`.
- `hermes kanban stats [-h] [--json]`: Has `--json`.
- `hermes kanban notify-subscribe [-h] --platform PLATFORM --chat-id CHAT_ID [--thread-id THREAD_ID] [--user-id USER_ID] [--user-id-alt USER_ID_ALT] [--chat-type {dm,group,channel,thread}] [--parent-chat-id PARENT_CHAT_ID] [--guild-id GUILD_ID] [--notifier-profile NOTIFIER_PROFILE] [--delivery-mode {notify,notify+wake,wake}] task_id`: No `--json`.
- `hermes kanban notify-list [-h] [--json] [task_id]`: Has `--json`.
- `hermes kanban notify-unsubscribe [-h] --platform PLATFORM --chat-id CHAT_ID [--thread-id THREAD_ID] task_id`: No `--json`.
- `hermes kanban log [-h] [--tail TAIL] task_id`: worker log. No `--json`.
- `hermes kanban runs [-h] [--json] [--state-type {status,outcome}] [--state-name VALUE] task_id`: Has `--json`.
- `hermes kanban heartbeat [-h] [--note NOTE] task_id`: No `--json`.
- `hermes kanban assignees [-h] [--json]`: Has `--json`.
- `hermes kanban context [-h] task_id`: No `--json`.
- `hermes kanban specify [-h] [--all] [--tenant TENANT] [--author AUTHOR] [--json] [task_id]`: Has `--json` (one JSON object per task).
- `hermes kanban decompose [-h] [--all] [--tenant TENANT] [--author AUTHOR] [--json] [task_id]`: Has `--json` (one JSON object per task).
- `hermes kanban gc [-h] [--event-retention-days EVENT_RETENTION_DAYS] [--log-retention-days LOG_RETENTION_DAYS]`: No `--json`.
- `hermes kanban repair [-h] [--json]`: Has `--json`.

## cron

Usage: `hermes cron [-h] [--accept-hooks] {list,create,add,edit,pause,resume,run,remove,rm,delete,status,runs,history,incidents,notepad,doctor,tick} ...`

- `hermes cron list [-h] [--all]`: `--all` includes disabled and completed jobs. No `--json`.
- `hermes cron create [-h] [--name NAME] [--deliver DELIVER] [--failure-deliver FAILURE_DELIVER] [--repeat REPEAT] [--skill SKILLS] [--script SCRIPT] [--no-agent] [--monitor-script MONITOR_SCRIPT] [--monitor-url MONITOR_URL] [--workdir WORKDIR] [--model MODEL] [--pin] [--provider MODEL_PROVIDER] [--reasoning-effort REASONING_EFFORT] [--interpreter INTERPRETER] [--continuity] [--paused] [--paused-reason PAUSED_REASON] schedule [prompt]`: alias `add`. Positionals `schedule`, `prompt`. No `--json`.
- `hermes cron edit [-h] [--schedule SCHEDULE] [--prompt PROMPT] [--name NAME] [--deliver DELIVER] [--failure-deliver FAILURE_DELIVER] [--repeat REPEAT] [--skill SKILLS] [--add-skill ADD_SKILLS] [--remove-skill REMOVE_SKILLS] [--clear-skills] [--script SCRIPT] [--no-agent] [--agent] [--continuity] [--no-continuity] [--monitor-script MONITOR_SCRIPT] [--monitor-url MONITOR_URL] [--workdir WORKDIR] [--model MODEL] [--pin | --unpin] [--provider MODEL_PROVIDER] [--reasoning-effort REASONING_EFFORT] [--interpreter INTERPRETER] job_id`: No `--json`.
- `hermes cron pause [-h] job_id`: No `--json`.
- `hermes cron resume [-h] [--at RUN_AT] [--run-now] job_id`: No `--json`.
- `hermes cron run [-h] [--accept-hooks] job_id`: run on next tick. No `--json`.
- `hermes cron remove [-h] job_id` (aliases `rm`, `delete`): No `--json`.
- `hermes cron status [-h]`: No `--json`.
- `hermes cron runs [-h] [--limit LIMIT] [job_id]` (alias `history`): No `--json`.
- `hermes cron incidents [-h] [--state {detected,alerted,resolved,closed}] [{list,ack}] [incident_id]`: No `--json`.
- `hermes cron notepad [-h] job_id [{get,set,delete,list}] [key] [value]`: No `--json`.
- `hermes cron doctor [-h]`: No `--json`.
- `hermes cron tick [-h] [--accept-hooks]`: run due jobs once. No `--json`.

## memory

Usage: `hermes memory [-h] {setup,status,off,reset} ...`

- `hermes memory setup [-h]`: interactive provider setup. No `--json`.
- `hermes memory status [-h]`: show memory provider config. No `--json`.
- `hermes memory off [-h]`: disable external provider. No `--json`.
- `hermes memory reset [-h]`: erase built-in MEMORY.md and USER.md. No `--json`.

## gateway

Usage: `hermes gateway [-h] [--accept-hooks] {run,ensure,start,stop,restart,status,install,uninstall,list,setup,migrate-legacy,migrate,enroll} ...`

- `hermes gateway status [-h] [--deep] [-l] [--system]`
  - `--deep`: deep status check.
  - `-l` / `--full`: full, untruncated service/log output.
  - `--system`: target the Linux system-level service.
  - No `--json`.
- Other subcommands (no `--json` checked): `run`, `ensure`, `start`, `stop`, `restart`, `install`, `uninstall`, `list`, `setup`, `migrate-legacy`, `migrate`, `enroll`.

## approvals

Usage: `hermes approvals [-h] <subcommand> ...`

- `hermes approvals suggest [-h] [--apply N[,M...]] [--json] [--days DAYS] [--min-count MIN_COUNT] [--limit LIMIT] [--db DB]`
  - Mines past approvals into `command_allowlist` proposals. Writes only with `--apply`.
  - Has `--json`.
- `hermes approvals test [-h] [--env-type ENV_TYPE] [--json] ...`
  - positional `command` (prefix with `--`). Dry-run verdict; exit codes 0 allow, 2 ask, 3 deny.
  - Has `--json`.

## secrets

Usage: `hermes secrets [-h] {bitwarden,bw,onepassword,op,1password} ...`

- `hermes secrets bitwarden [-h] {setup,status,token,sync,disable,install} ...` (alias `bw`). Subcommands `setup`, `status`, `token`, `sync`, `disable`, `install`. Checked `status`: no `--json`.
- `hermes secrets onepassword [-h] {setup,status,token,set,remove,sync,disable} ...` (aliases `op`, `1password`). Subcommands `setup`, `status`, `token`, `set`, `remove`, `sync`, `disable`. Checked `status`: no `--json`.

## Machine-readable support

| command | has --json | notes |
|---|---|---|
| `hermes --version` / `-V` | no | Text version block. No `hermes version` subcommand. |
| `hermes -z PROMPT` | no | Plain final-text output. `--usage-file PATH` writes a JSON usage report. |
| `hermes -p/--profile` | n/a | Global flag, accepted but not listed in top-level options. |
| `status` | no | `--full`, `--deep` only. |
| `doctor` | no | `--fix`, `--live`, `--ack`. |
| `insights` | no | `--days`, `--source`. |
| `usage` | yes | `--json` prints one JSON document. |
| `logs` | no | Plain text. |
| `config show` | no | |
| `config edit` | no | Interactive. |
| `config get` | yes | `--json` prints the value as JSON. |
| `config set` | no | |
| `config unset` | no | |
| `config path` | no | |
| `config env-path` | no | |
| `config check` | no | |
| `config migrate` | no | |
| `model` | no | Interactive. |
| `fallback` (list/add/remove/clear) | no | |
| `sessions` (general) | no | `sessions export --format jsonl` is a format choice, not `--json`. |
| `sessions pinned` | yes | `--json` for backup/restore scripting. |
| `profile` (all) | no | |
| `tools` (list/enable/disable/post-setup) | no | |
| `plugins list` | yes | `--json`. |
| `plugins` (other subcommands) | no | |
| `mcp` (all) | no | |
| `skills search` | yes | `--json`. |
| `skills` (other subcommands) | no | |
| `memory` (all) | no | |
| `gateway status` | no | |
| `gateway` (other subcommands) | no | Not individually checked for `--json`. |
| `kanban create` | yes | |
| `kanban swarm` | yes | |
| `kanban list` | yes | |
| `kanban show` | yes | |
| `kanban diagnostics` | yes | |
| `kanban attachments` | yes | |
| `kanban promote` | yes | |
| `kanban dispatch` | yes | |
| `kanban stats` | yes | |
| `kanban notify-list` | yes | |
| `kanban runs` | yes | |
| `kanban assignees` | yes | |
| `kanban specify` | yes | One JSON object per task. |
| `kanban decompose` | yes | One JSON object per task. |
| `kanban repair` | yes | |
| `kanban` (assign, set-model, reclaim, reassign, link, unlink, claim, comment, attach, attach-rm, complete, edit, block, schedule, unblock, request-review, request-changes, reopen-review, archive, tail, watch, notify-subscribe, notify-unsubscribe, log, heartbeat, context, gc, init) | no | |
| `kanban boards` | no | Subcommands not individually checked. |
| `cron` (all) | no | |
| `approvals suggest` | yes | `--json`. |
| `approvals test` | yes | `--json`. |
| `secrets` (bitwarden, onepassword) | no | Only `status` checked. |
