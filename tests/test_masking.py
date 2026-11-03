"""Unified secret masking (hermcp.redact): read style (edge markers), write style (nothing), write diffs.

Synthetic values only. No value in this file is a real credential; they are built from letters so they cannot
match anything real.
"""
import json
import re

import pytest
import yaml
from starlette.testclient import TestClient

from hermcp.redact import (MARK, SECRET_CHANGED, SECRET_UNCHANGED, STARS, diff_changes, looks_secret, mask_value,
                           redact, redact_shapes, redact_text, redact_result, scrub)
from hermcp.scopes import Scopes

PASS = "Qx7mK2vLpR9sTz4WnB"                     # 18 chars, stands in for SUBWAVE_ADMIN_PASS
PASS_B = "Qx7m" + "Z" * 10 + "wnB1"             # same first 4, different end
SHORT = "tinyvalue1"                            # 10 chars
LONG = "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zAb3d"   # 36 chars
JWT = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXRlc3Q"


def edges(v: str) -> list[str]:
    return [v[:4], v[-4:]]


def no_edges(blob: str, *values: str) -> bool:
    """Neither a value nor its 4-character edges (of values >= 12 chars) occur in `blob`."""
    for v in values:
        if v in blob:
            return False
        if len(v) >= 12 and any(e in blob for e in edges(v)):
            return False
    return True


# ---- mask_value / markers ----------------------------------------------------------------------------------
def test_mask_value_rules():
    assert mask_value("x" * 15, True) == STARS and mask_value("", True) == STARS
    assert mask_value("a" * 16, True) == "aaaa" + STARS + "aaaa"
    long = mask_value(LONG, True)
    assert long == LONG[:4] + STARS + LONG[-4:] and len(long) == 16
    assert len(mask_value("z" * 500, True)) == 16                       # the real length is not revealed
    assert mask_value(PASS, False) == MARK == mask_value(SHORT, False)  # write style never shows anything


def test_short_values_show_no_edge_characters():
    out = redact({"api_key": SHORT}, strict=True, partial=True)["api_key"]
    assert out == STARS and SHORT[:2] not in out


def test_marker_equal_values_equal_marks_different_edges_differ():
    a1 = redact({"env": {"X_PASS": PASS}}, strict=True, partial=True)
    a2 = redact({"headers": {"X-Other-Token": PASS}}, strict=True, partial=True)
    b = redact({"env": {"X_PASS": PASS[:-4] + "ZZZZ"}}, strict=True, partial=True)
    assert a1["env"]["X_PASS"] == a2["headers"]["X-Other-Token"] == mask_value(PASS, True)
    assert a1["env"]["X_PASS"] != b["env"]["X_PASS"]


def test_masking_is_idempotent():
    for v in (PASS, SHORT, LONG, "Bearer " + LONG):
        once = redact({"env": {"A_SECRET": v}}, strict=True, partial=True)
        assert redact(once, strict=True, partial=True) == once
        assert redact(once, strict=True, partial=False) == {"env": {"A_SECRET": MARK}}   # write style drops edge markers
    t = redact_text("Authorization: Bearer " + LONG, partial=True)
    assert redact_text(t, partial=True) == t


# ---- detection: names -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["SUBWAVE_ADMIN_PASS", "DB_PASS", "pass", "pwd", "ROOT_PASSWD", "api_key", "X-Api-Key",
                                  "access_key", "AWS_ACCESS_KEY", "client_secret", "Authorization", "bearer", "cookie",
                                  "private_key", "SIGNATURE", "credential", "refresh_token", "my_secret_thing"])
def test_secret_names_masked_read_and_write(name):
    r = redact({"cfg": {name: PASS}}, strict=True, partial=True)["cfg"][name]
    w = redact({"cfg": {name: PASS}}, strict=True)["cfg"][name]
    assert r == mask_value(PASS, True) and w == MARK


@pytest.mark.parametrize("name", ["bypass_enabled", "passed", "compass", "monkey_count", "tokens_used"])
def test_non_secret_names_untouched(name):
    assert redact({name: PASS}, strict=True) == {name: PASS}


def test_env_and_headers_are_credential_containers_with_a_harmless_exception_list():
    env = {"SUBWAVE_ADMIN_PASS": PASS, "SOME_RANDOM_VAR": "plainvalue99", "REF": "${SUBWAVE_ADMIN_PASS}",
           "LOG_LEVEL": "debug", "PORT": "8080", "CLIENT_ID": "my-app-client-id", "BASE_URL": "https://api.example.com/v1",
           "HOME": "/home/user", "NAME_OF_VAR": "OPENAI_API_KEY", "TIMEOUT": 30, "DEBUG": True}
    out = redact({"mcp_servers": {"demo": {"env": env, "headers": {"X-Custom": "plainvalue99", "Accept": "application/json"}}}},
                 strict=True, partial=True)["mcp_servers"]["demo"]
    assert out["env"]["SUBWAVE_ADMIN_PASS"] == mask_value(PASS, True)
    assert out["env"]["SOME_RANDOM_VAR"] == "plainvalue99"        # short plain id: label, not secret
    assert out["env"]["REF"] == "${SUBWAVE_ADMIN_PASS}"
    for k in ("LOG_LEVEL", "PORT", "CLIENT_ID", "BASE_URL", "HOME", "NAME_OF_VAR", "TIMEOUT", "DEBUG"):
        assert out["env"][k] == env[k], k
    assert out["headers"] == {"X-Custom": "plainvalue99", "Accept": "application/json"}
    assert redact({"headers": {"X-Custom": "custom-thing"}}, strict=True, partial=True)["headers"]["X-Custom"] == "custom-thing"


# ---- detection: shapes, nesting, free text ------------------------------------------------------------------------
def test_value_shapes_under_neutral_names_nested_and_in_lists():
    data = {"a": [{"b": {"note": "Bearer " + LONG}}, {"c": [JWT, f"https://user:{PASS}@host.example/x"]},
                  {"d": "sk-" + LONG}, {"e": "ghp_" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1w"}]}
    for partial in (True, False):
        out = redact(data, strict=True, partial=partial)
        blob = json.dumps(out)
        assert LONG not in blob and PASS not in blob and JWT not in blob
        assert "host.example" in blob                              # the non-secret part stays
    assert redact(data, partial=True)["a"][0]["b"]["note"] == "Bearer " + mask_value(LONG, True)


def test_url_userinfo_and_secret_query_free_text():
    t = f"GET https://user:{PASS}@h.example/a?token={LONG}&page=2 failed; password={PASS}; --api-key {LONG}"
    for partial in (True, False):
        out = redact_text(t, partial=partial)
        assert PASS not in out and LONG not in out and "page=2" in out and "h.example" in out
    assert no_edges(redact_text(t), PASS, LONG)


def test_blob_anywhere_is_masked_but_ids_and_paths_are_not():
    ids = {"uuid": "a3f9c1d2-1111-4222-8333-444455556666", "sha1": "d7c62e8a1b2c3d4e5f60718293a4b5c6d7e8f901",
           "path": "/home/user/.hermes/profiles/researcher/config.yaml", "url": "https://api.example.com/v1/models",
           "session": "20261021_150000_ab12cd", "model": "claude-sonnet-5-5", "flag": True, "n": 42, "f": 1.5}
    assert redact(ids, partial=True) == ids
    assert redact({"note": LONG + LONG[:8]}, partial=True)["note"] == mask_value(LONG + LONG[:8], True)


def test_negative_values_not_mangled():
    cfg = {"model": {"default": "m", "provider": "anthropic", "max_tokens": 4096},
           "mcp_servers": {"x": {"url": "https://mcp.example.com/mcp", "enabled": True, "timeout": 30,
                                 "headers": {"Authorization": "${TOKEN_REF}"}, "env": {"KEY": "${KEY_REF}"},
                                 "oauth": {"client_id": "client-123", "scope": "read write"}}}}
    assert redact(cfg, strict=True, partial=True) == cfg


def test_oauth_container_masks_leaves_but_keeps_shape_and_client_id():
    out = redact({"oauth": {"client_id": "client-123", "client_secret": PASS, "token_url": "https://a.example/t"}},
                 strict=True, partial=True)["oauth"]
    assert out == {"client_id": "client-123", "client_secret": mask_value(PASS, True), "token_url": "https://a.example/t"}


def test_looks_secret_names_and_shapes_for_mcp_add_guard():
    assert looks_secret("SUBWAVE_ADMIN_PASS", PASS) and looks_secret("x", "sk-" + LONG)
    assert not looks_secret("SUBWAVE_ADMIN_PASS", "${SUBWAVE_ADMIN_PASS}") and not looks_secret("LOG_LEVEL", "debug")


# ---- write diffs -------------------------------------------------------------------------------------------
def test_diff_never_contains_secret_values():
    before = {"subwave": {"command": "x", "env": {"SUBWAVE_ADMIN_PASS": PASS, "URL": "https://a.example"}, "enabled": True}}
    d = diff_changes("mcp_servers", before, {})
    blob = json.dumps(d["changes"], ensure_ascii=False)
    assert no_edges(blob, PASS) and SECRET_CHANGED in blob and "https://a.example" in blob
    assert PASS in d["secrets"]                                     # raw list is internal bookkeeping only
    paths = {c["path"] for c in d["changes"]}
    assert "mcp_servers.subwave.env.SUBWAVE_ADMIN_PASS" in paths and "mcp_servers.subwave.command" in paths


def test_diff_only_changed_keys_and_unchanged_secret_marker():
    cfg = {"a": {"env": {"X_PASS": PASS}, "enabled": True}, "b": {"x": 1}}
    d = diff_changes("mcp_servers", cfg, {**cfg, "a": {**cfg["a"], "enabled": False}})
    assert d["changes"] == [{"path": "mcp_servers.a.enabled", "change": "changed", "before": True, "after": False}]
    same = diff_changes("providers.x.api_key", PASS, PASS)
    assert same["changes"] == [{"path": "providers.x.api_key", "change": "unchanged", "value": SECRET_UNCHANGED}]
    chg = diff_changes("providers.x.api_key", PASS, PASS_B)
    assert chg["changes"] == [{"path": "providers.x.api_key", "change": "changed", "value": SECRET_CHANGED}]


def test_scrub_removes_known_values_and_shapes():
    t = f"Set x = {PASS} done; Bearer {LONG}"
    out = scrub(t, [PASS])
    assert PASS not in out and LONG not in out


def test_names_only_tools_keep_names_but_mask_shapes():
    res = {"secrets": [{"name": "SUBWAVE_ADMIN_PASS", "set": True, "description": f"token: {LONG}"}]}
    out = redact_result("providers", "secret_list", res)
    assert out["secrets"][0]["name"] == "SUBWAVE_ADMIN_PASS" and LONG not in json.dumps(out)


# ---- through the server: write path regression ------------------------------------------------------------------------
@pytest.fixture()
def call(happ):
    c = TestClient(happ.app)
    _, token = happ.keystore.create("t", Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True, inbox_push=True))

    def _call(tool_name, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": args}})
        res = r.json()["result"]
        raw = res["content"][0]["text"]
        body = json.loads(raw)
        return (not res["isError"]), body.get("result", body.get("error")), raw
    _call.home = happ.keystore.path.parent.parent.parent
    return _call


def _cfg(call):
    return yaml.safe_load((call.home / "config.yaml").read_text())


def _seed(call):
    cfg = _cfg(call)
    cfg["mcp_servers"] = {
        "subwave": {"command": "subwave-mcp", "args": ["--port", "9"],
                    "env": {"SUBWAVE_ADMIN_PASS": PASS, "SUBWAVE_URL": "https://subwave.example/api"}},
        "remote": {"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer " + LONG, "X-Mode": "fast"}},
    }
    cfg["providers"] = {"x": {"api_key": LONG, "base_url": "https://p.example/v1"}}
    (call.home / "config.yaml").write_text(yaml.safe_dump(cfg))


def clean(raw: str):
    assert no_edges(raw, PASS, PASS_B, LONG, JWT), raw
    assert PASS not in raw and LONG not in raw


def test_mcp_remove_dry_run_and_real_have_no_secret_not_even_edges(call):
    _seed(call)
    ok, r, raw = call("mcp_remove", name="subwave", dry_run=True)
    assert ok and r["applied"] is False and "before" not in r and "after" not in r
    clean(raw)
    assert any(c["path"] == "mcp_servers.subwave.env.SUBWAVE_ADMIN_PASS" for c in r["changes"])
    assert "subwave" in _cfg(call)["mcp_servers"]
    ok, r, raw = call("mcp_remove", name="subwave")
    assert ok and r["applied"] and r["backup"]
    clean(raw)
    assert "subwave" not in _cfg(call)["mcp_servers"] and "remote" in _cfg(call)["mcp_servers"]
    assert not any(c["path"].startswith("mcp_servers.remote") for c in r["changes"])       # only what changed


def test_mcp_add_enable_disable_responses(call):
    _seed(call)
    ok, r, raw = call("mcp_add", name="n1", command="x", env={"SUBWAVE_ADMIN_PASS": PASS})       # literal secret refused
    assert not ok and r["code"] == "rejected"
    clean(raw)
    ok, r, raw = call("mcp_add", name="n1", command="x", env={"SOME_PASS": "${SOME_PASS}", "MODE": "fast"})
    assert ok and r["applied"] and {c["change"] for c in r["changes"]} == {"added"}
    assert any(c.get("after") == "${SOME_PASS}" for c in r["changes"])
    for tool in ("mcp_disable", "mcp_enable"):
        for dry in (True, False):
            ok, r, raw = call(tool, name="subwave", dry_run=dry)
            assert ok, raw
            clean(raw)
            assert all("SUBWAVE_ADMIN_PASS" not in c["path"] for c in r["changes"])           # unchanged secrets not listed
    assert _cfg(call)["mcp_servers"]["subwave"]["env"]["SUBWAVE_ADMIN_PASS"] == PASS         # data untouched


def test_config_set_unset_restore_responses(call):
    _seed(call)
    for dry in (True, False):
        ok, r, raw = call("config_set", key="providers.x.api_key", value=PASS_B, dry_run=dry)
        assert ok and r["changes"] == [{"path": "providers.x.api_key", "change": "changed", "value": SECRET_CHANGED}], raw
        clean(raw)
    assert _cfg(call)["providers"]["x"]["api_key"] == PASS_B
    ok, r, raw = call("config_set", key="providers.x.api_key", value=PASS_B)
    assert ok and r["changes"][0]["value"] == SECRET_UNCHANGED
    clean(raw)
    ok, r, raw = call("config_set", key="providers.x.base_url", value="https://q.example/v1")
    assert ok and r["changes"][0]["before"] == "https://p.example/v1" and r["changes"][0]["after"] == "https://q.example/v1"
    for dry in (True, False):
        ok, r, raw = call("config_unset", key="providers.x.api_key", dry_run=dry)
        assert ok, raw
        clean(raw)
    ok, r, raw = call("config_backups")
    assert ok and r["backups"]
    clean(raw)
    name = r["backups"][-1]["name"]
    for dry in (True, False):
        ok, r, raw = call("config_restore", backup=name, dry_run=dry)
        assert ok, raw
        clean(raw)


def test_mcp_remove_unknown_error_and_validation_messages_clean(call):
    _seed(call)
    ok, r, raw = call("mcp_remove", name="does-not-exist")
    assert not ok and r["code"] == "not_found"
    ok, r, raw = call("config_set", key=f"x.password={PASS}", value="1")
    clean(raw)


def test_secret_tools_never_return_values(call):
    ok, r, raw = call("secret_set", name="MY_TEST_PASS", value=PASS)
    assert ok
    clean(raw)
    for tool, args in (("secret_list", {}), ("secrets_status", {}), ("vault_list", {})):
        ok, r, raw = call(tool, **args)
        assert ok, raw
        clean(raw)
    ok, r, raw = call("secret_delete", name="MY_TEST_PASS", dry_run=True)
    clean(raw)
    ok, r, raw = call("secret_delete", name="MY_TEST_PASS")
    clean(raw)


# ---- read path: styles, meta tools, sweep --------------------------------------------------------------------------
def test_read_tools_use_edge_markers_write_diff_has_none(call):
    _seed(call)
    ok, r, raw = call("mcp_get", name="subwave")
    assert r["env"]["SUBWAVE_ADMIN_PASS"] == mask_value(PASS, True) and r["env"]["SUBWAVE_URL"] == "https://subwave.example/api"
    ok, r, raw = call("mcp_get", name="remote")
    assert r["headers"]["Authorization"] == "Bearer " + mask_value(LONG, True) and r["headers"]["X-Mode"] == "fast"
    ok, r, raw = call("config_get", key="mcp_servers.subwave.env.SUBWAVE_ADMIN_PASS")
    assert r["value"] == mask_value(PASS, True)
    ok, r, raw = call("config_get", key="mcp_servers.subwave.env")
    assert r["value"]["SUBWAVE_ADMIN_PASS"] == mask_value(PASS, True)
    ok, r, raw = call("config_get")
    assert PASS not in raw and LONG not in raw and mask_value(PASS, True) in raw
    ok, r, raw = call("mcp_list", detail=True)
    assert PASS not in raw and LONG not in raw


def test_hermes_call_wrapper_masks_like_the_direct_call(call):
    _seed(call)
    ok, r, raw = call("hermes_call", name="mcp_get", args={"name": "subwave"})
    assert ok and PASS not in raw and mask_value(PASS, True) in raw
    ok, r, raw = call("hermes_call", name="mcp_remove", args={"name": "subwave", "dry_run": True})
    assert ok
    clean(raw)
    ok, r, raw = call("hermes_call", name="config_set", args={"key": "providers.x.api_key", "value": PASS_B})
    assert ok
    clean(raw)


def test_every_zero_argument_read_tool_is_clean(call):
    """Sweep: every read tool callable without arguments, run against a config stuffed with synthetic secrets."""
    from hermcp.tools import reg
    _seed(call)
    cfg = _cfg(call)
    cfg["dashboard"] = {"password_hash": "scrypt:1:2:3$" + LONG, "session_secret": PASS}
    cfg["plugins"] = {"x": {"token": PASS}}
    cfg["auxiliary"] = {"vision": {"api_key": LONG, "model": "m"}}
    (call.home / "config.yaml").write_text(yaml.safe_dump(cfg))
    (call.home / ".env").write_text(f"MY_TEST_PASS={PASS}\n")
    skip = {"mcp_test", "approvals_answer", "chat_send", "plugins_check_updates", "status_doctor", "hermes_health",
            "skills_search", "orchestration_overview"}
    seen = 0
    for t in reg.tools.values():
        if t.access != "read" or t.required or t.name in skip or t.name.startswith("hermes_search"):
            continue
        ok, r, raw = call(t.name)
        assert PASS not in raw and LONG not in raw, t.name
        seen += 1
    assert seen > 30


def test_orchestration_overview_and_profile_files_do_not_leak(call):
    _seed(call)
    ok, r, raw = call("orchestration_overview", skills=False)
    assert PASS not in raw and LONG not in raw
    ok, r, raw = call("profile_files_read", file="config.yaml")
    assert PASS not in raw and LONG not in raw, raw[:300]


def test_audit_and_inbox_paths_use_the_same_masker(call):
    _seed(call)
    call("config_set", key="providers.x.api_key", value=PASS_B)
    ok, r, raw = call("audit_tail", lines=20)
    assert ok and PASS_B not in raw
    ok, r, raw = call("inbox_push", source="t", kind="note", summary=f"token={PASS} and Bearer {LONG}")
    assert ok and PASS not in raw and LONG not in raw


def test_free_text_fields_in_tool_results(call):
    from hermcp.registry import Tool
    from hermcp.tools import reg
    text = f"line1 password={PASS} Authorization: Bearer {LONG} https://u:{PASS}@h.example/x?api_key={LONG}"
    for style, access in (("read", "read"), ("write", "write")):
        name = f"zz_text_{style}"
        if name not in reg.tools:
            reg.add(Tool(name=name, toolset="sessions", access=access, description="t",
                         handler=lambda ctx, **kw: {"output": text, "error": text, "warnings": [text],
                                                    "logs": [{"line": text}]}))
        ok, r, raw = call(name)
        assert ok and PASS not in raw and LONG not in raw, name
        if style == "write":
            assert no_edges(raw, PASS, LONG)
        assert "line1" in raw and "h.example" in raw


def test_redact_shapes_helper_is_pure():
    data = {"a": [f"token: {LONG}", "${REF}"]}
    snap = json.dumps(data)
    out = redact_shapes(data)
    assert json.dumps(data) == snap and LONG not in json.dumps(out) and out["a"][1] == "${REF}"


def test_pure_function_no_mutation_and_depth_bound():
    deep: dict = {}
    cur = deep
    for _ in range(60):
        cur["n"] = {}
        cur = cur["n"]
    cur["password"] = PASS
    assert PASS not in json.dumps(redact(deep, strict=True))
    assert re.search(r"\[redacted\]", json.dumps(redact(deep, strict=True)))
