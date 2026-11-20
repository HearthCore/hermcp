"""`dry_run` on a read tool is a no-op, not an error (issue #67).

Models attach `dry_run` to any call. A read tool applies nothing either way, so the flag must not
turn a harmless call into `invalid_arguments` — and it must not reach the handler either. Write and
admin tools keep the flag, including its type check.
"""
from starlette.testclient import TestClient

from hermcp.registry import Principal, Registry, Tool
from hermcp.scopes import Scopes


def call(happ, tool, args, access="read"):
    return happ.registry.invoke(tool, args, Principal("k", "k", Scopes(access=access)), happ.settings, None)


def body(result):
    """The result without the volatile provenance block (`_meta` carries timestamps)."""
    return {k: v for k, v in result.items() if k != "_meta"}


# ---- the reported repro, over the wire ---------------------------------------------------------
def test_read_tools_accept_dry_run_over_mcp(happ):
    c = TestClient(happ.app)
    _, tok = happ.keystore.create("a", Scopes(access="read"))
    for name in ("profiles_list", "hermes_health", "cron_list"):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": name, "arguments": {"dry_run": True}}})
        res = r.json()["result"]
        assert res["isError"] is False, (name, res["content"][0]["text"])
        assert "invalid_arguments" not in res["content"][0]["text"], name


# ---- the flag changes nothing ----------------------------------------------------------------
def test_read_tool_result_is_identical_with_and_without_dry_run(happ):
    for name in ("profiles_list", "cron_list", "kanban_list"):
        plain, flagged = call(happ, name, {}), call(happ, name, {"dry_run": True})
        assert plain["ok"] and flagged["ok"], (name, flagged)
        assert body(flagged["result"]) == body(plain["result"]), name
        assert "dry_run" not in flagged["result"] and "applied" not in flagged["result"], name


def test_every_read_tool_drops_dry_run_before_the_handler(happ):
    """Registry-wide: no read tool rejects the flag and no handler ever sees it."""
    reg, seen = Registry(), {}
    for t in happ.registry.tools.values():
        if t.access != "read":
            continue

        def handler(ctx, _name=t.name, **kw):
            seen[_name] = kw
            return {"ok": True}

        reg.add(Tool(name=t.name, toolset=t.toolset, access="read", description="probe", handler=handler,
                     properties=t.properties, profile_aware=t.profile_aware, meta=t.meta,
                     requires_approvals=t.requires_approvals, dangerous=t.dangerous,
                     requires_inbox_push=t.requires_inbox_push))
    p = Principal("k", "k", Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True,
                                   inbox_push=True))
    for name in list(reg.tools):
        r = reg.invoke(name, {"dry_run": True}, p, happ.settings, None)
        assert r["ok"], (name, r)
        assert "dry_run" not in seen[name], name


# ---- write/admin tools are unchanged ---------------------------------------------------------
def test_write_tool_still_type_checks_dry_run(happ):
    r = call(happ, "config_set", {"key": "display.skin", "value": "mono", "dry_run": "yes"}, access="admin")
    assert r["ok"] is False and r["error"]["code"] == "invalid_arguments"
    assert "dry_run" in r["error"]["message"]
