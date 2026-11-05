"""What a profile-restricted key may call.

A key restricted to profiles (scopes.profiles != ['*']) is limited to those profiles. For profile-aware tools the
registry forces the profile. Tools that are NOT profile-aware touch global state, so each of them is classified here:

  open  harmless without profile data (meta tools, whoami, version, public skill search)
  own   the handler itself reduces the answer / the action to the key's profile(s)
  deny  global or administrative: not callable and not listed for restricted keys

Profile-aware tools can be overridden to deny (they would otherwise reach process-wide state). Every registered tool must
appear in one of the tables or be profile-aware: tests fail for a new unclassified tool, so nothing is open by accident.
"""
from __future__ import annotations

OPEN = {
    "hermes_call": "wrapper: re-enters the registry with the same key, so every check applies again",
    "hermes_describe_tool": "only describes tools the key may call",
    "hermes_search_tools": "only lists tools the key may call",
    "hermes_whoami": "shows the key's own scopes",
    "skills_search": "public skill registries, no Hermes state",
    "status_version": "version string only",
}

OWN = {
    "hermes_health": "profiles, platforms, served/parked reduced to the key's profile(s)",
    "agents_live": "only rows attributable to the key's profile(s); a profile argument outside the scope is refused",
    "orchestration_overview": "profiles, cron, MCP, plugins, gateway, runs reduced to the key's profile(s); no kanban config or boards",
    "profiles_list": "only the key's profile(s)",
    "profiles_show": "refuses other profiles",
    "runs_active": "only runs of this key on its profile(s)",
    "inbox_poll": "only run-watcher events of runs on the key's profile(s)",
    "inbox_list": "only run-watcher events of runs on the key's profile(s)",
    "inbox_ack": "only such events",
    "kanban_list": "forced to tasks assigned to the key's profile(s)",
    "kanban_show": "only tasks assigned to the key's profile(s); links to foreign tasks are counted, not shown",
    "kanban_log": "only worker logs of tasks assigned to the key's profile(s)",
    "kanban_comment": "only tasks of the key's profile(s)",
    "kanban_edit": "only tasks of the key's profile(s)",
    "kanban_block": "only tasks of the key's profile(s)",
    "kanban_unblock": "only tasks of the key's profile(s)",
    "kanban_complete": "only tasks of the key's profile(s)",
    "kanban_create": "assignee forced to the key's profile; parents must be its own tasks",
    "kanban_assign": "only to the key's own profile (no hand-off to other profiles)",
}

DENY = {
    "audit_tail": "global audit log of all keys",
    "keys_list": "key administration",
    "inbox_push": "producer scope for global events",
    "kanban_stats": "counts over all profiles' tasks",
    "kanban_assignees": "lists all profiles with task counts",
    "kanban_boards": "boards and counts are global",
    "kanban_archive": "admin",
    "kanban_board_create": "admin",
    "models_providers": "global provider list",
    "gateway_restart": "admin, affects every profile",
    "ops_gateway_logs": "systemd journal of the shared gateway",
    "dashboard_status": "machine-wide dashboard process, unit and ports",
    "dashboard_restart": "admin, restarts the shared dashboard of every profile",
    "hermcp_restart": "admin, restarts this hermcp process (drops the MCP connection)",
    "plugins_check_updates": "global plugin inventory",
    "plugins_install": "admin, global code",
    "plugins_remove": "admin, global code",
    "plugins_update": "admin, global code",
    "profiles_create": "admin, creates other profiles",
    "profiles_delete": "admin",
    "profiles_describe": "global write (routing description)",
    "profiles_rename": "admin",
    "provider_auth_cancel": "global provider credentials",
    "provider_auth_continue": "global provider credentials",
    "provider_auth_start": "global provider credentials",
    "provider_auth_status": "global provider credentials",
    "providers_add": "admin, global",
    "providers_list": "global provider list",
}

NOTES = {
    "approvals_pending": "per-profile socket; on a shared multiplex socket only the sessions whose key namespace is this profile's are listed",
    "approvals_answer": "same bridge rule plus: a session_key of another profile is refused before the bridge is called",
    "approvals_bridge_status": "ping only (reachable, pid); no approval data",   # profile-aware tools whose handler narrows behaviour for restricted keys
    "logs_tail": "own profile logs only; gateway/agent/errors of the default profile are the shared gateway log (denied); worker:<id> only for own tasks",
    "plugin_config_get": "profile config section and own plugin dir; shared plugin dirs: manifest only",
    "secrets_status": "own profile .env and config only; no default .env, no server environment",
    "status_gateway": "platforms of other profiles are not shown",
    "chat_run_status": "run must belong to the key's profile (hermcp watch record)",
    "chat_run_stop": "same",
    "chat_run_approval": "same",
    "chat_run_steer": "same (also the run found by chat_send mode=steer / mode=interrupt)",
    "chat_run_cancel": "same",
    "chat_session_queue": "profile-scoped read; run_ids of runs started by other keys are hidden for restricted keys",
    "hooks_list": "own profile hooks; shared plugin manifests by name only",
}


def classify(tool) -> str:
    n = tool.name
    if n in DENY:
        return "deny"
    if n in OPEN:
        return "open"
    if n in OWN:
        return "own"
    if tool.profile_aware:
        return "profile"
    return "unclassified"


def bound_verdict(tool, *, bound: bool) -> tuple[bool, str]:
    """(allowed, reason) for a profile-restricted key. `bound` = restricted to exactly one profile."""
    c = classify(tool)
    if c == "deny":
        return False, f"not available to profile-restricted keys: {DENY[tool.name]}"
    if c == "unclassified":
        return False, "not classified for profile-restricted keys (denied by default)"
    if bound and tool.access == "admin":
        return False, "admin tools are not available to a key bound to one profile"
    return True, ""


def table(tools) -> list[dict[str, str]]:
    """Rows for docs: tool, access, class, reason."""
    rows = []
    for t in sorted(tools, key=lambda t: (t.toolset, t.name)):
        c = classify(t)
        ok, why = bound_verdict(t, bound=True)
        if c == "deny":
            verdict, reason = "no", DENY[t.name]
        elif not ok:
            verdict, reason = "no", why
        elif c == "own":
            verdict, reason = "filtered", OWN[t.name]
        elif c == "open":
            verdict, reason = "yes", OPEN[t.name]
        else:
            verdict, reason = "yes (own profile)", NOTES.get(t.name, "profile argument forced to the bound profile")
        rows.append({"tool": t.name, "toolset": t.toolset, "access": t.access, "bound_key": verdict, "why": reason})
    return rows
