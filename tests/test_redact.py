"""Output redaction (hermcp.redact): synthetic values only, no real secrets anywhere in this file."""
import copy
import json

import pytest
import yaml
from starlette.testclient import TestClient

from hermcp.redact import MARK, SECRET_CHANGED, STARS, looks_random, mode_for, redact, redact_text
from hermcp.scopes import Scopes

S = "SYNTHVALUE0123456789ABCDEF"          # stands in for any secret
RND = "Zk3x9Qw2Lp7Vb5Nc8Rt1Yh4Gd6Fs0Jm2Kq"   # random-looking 36 chars


def leaks(obj) -> bool:
    return S in json.dumps(obj) or RND in json.dumps(obj)


# ---- key names ---------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["password_hash", "session_secret", "passphrase", "hmac", "salt", "cookie",
                                  "credentials", "dsn", "client_secret", "api_key", "X-Api-Key", "Authorization",
                                  "MY_PASS_TOKEN", "private_key", "signing_key", "refresh_token", "pwd", "jwt", "hash", "admin_hash"])
def test_secret_key_names_are_fully_redacted(name):
    out = redact({"a": {name: S}}, strict=True)
    assert out == {"a": {name: MARK}}


def test_secret_name_redacts_whole_subtree_but_keeps_shape():
    out = redact({"secret": {"inner": S, "list": [S, "x"], "n": 5, "flag": True}}, strict=True)
    assert out == {"secret": {"inner": MARK, "list": [MARK, MARK], "n": 5, "flag": True}}


def test_list_items_inherit_the_parent_key_name():
    out = redact({"tokens": [S, S + "2"], "api_keys": [S]}, strict=True)
    assert not leaks(out) and out["tokens"] == [MARK, MARK]


def test_pair_style_key_value_dicts_use_the_name():
    out = redact({"key": "providers.x.api_key", "before": S, "after": S + "2", "applied": True})
    assert out["before"] == MARK and out["after"] == MARK and out["applied"] is True


def test_no_head_or_tail_survives():
    for v in (S, "abcdefghijkl", "x" * 40):
        assert redact({"api_key": v}, strict=True)["api_key"] == MARK
    assert redact({"api_key": "short"}, strict=True)["api_key"] == MARK          # not "***", not a length hint


def test_numbers_bools_and_env_refs_stay():
    cfg = {"token_count": 5, "max_tokens": 4096, "context_tokens": 128000, "api_key": "${OPENAI_API_KEY}",
           "key_env": "OPENAI_API_KEY", "api_key_env": "FAL_KEY", "auth": "oauth", "secret": False, "enabled": True}
    assert redact(cfg, strict=True) == cfg


def test_strict_vs_generic_key_word():
    row = {"session_key": "agent:main:telegram:42", "idempotency_key": "job-7", "pattern_key": "rm -rf",
           "request_id": "req_123"}
    assert redact(row) == row                                              # generic: ids stay
    assert redact({"key": S}, strict=True) == {"key": MARK}                # config: a bare `key` is a credential
    assert redact({"key": S}) == {"key": S}


# ---- value shapes ------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [
    "scrypt:32768:8:1$" + S + "$" + S, "pbkdf2:sha256:600000$" + S, "$2b$12$" + "a" * 50, "$argon2id$v=19$" + S,
    "Bearer " + S, "basic " + S, "sk-" + S, "sk_" + S, "ghp_" + "a" * 36, "github_pat_" + "A" * 30, "xoxb-" + "1" * 20,
    "hf_" + "a" * 30, "AKIA" + "A" * 16, "eyJhbGciOi" + "x" * 5 + "." + "eyJzdWIi" + "y" * 8 + "." + "sig" * 4,
    "hmcp_abc_" + "z" * 24,
])
def test_value_shapes_redacted_under_neutral_key(value):
    out = redact({"note": value, "items": [value]})
    assert MARK in out["note"] and MARK in out["items"][0]
    assert value not in json.dumps(out)


def test_url_userinfo_and_query_secrets():
    out = redact({"u": f"https://user:{S}@host.example/x", "v": f"https://host.example/x?a=1&token={S}&b=2",
                  "w": f"postgres://u:{S}@db/x", "ok": "https://host.example/path?page=2"})
    assert not leaks(out)
    assert out["u"] == f"https://{MARK}@host.example/x" and "a=1" in out["v"] and "b=2" in out["v"]
    assert out["ok"] == "https://host.example/path?page=2"


def test_url_fields_drop_any_query_even_with_unknown_parameter_names():
    out = redact({"base_url": f"https://h.invalid/v1?k={S}&x=1", "webhook_url": f"https://h.invalid/hook#{S}",
                  "homepage": "https://h.invalid/a?page=2", "base_url2": "https://h.invalid/v1"})
    assert not leaks(out) and out["base_url"] == f"https://h.invalid/v1?k={MARK}&x=1"       # only the credential-like value goes
    assert out["homepage"] == "https://h.invalid/a?page=2" and out["base_url2"] == "https://h.invalid/v1"


def test_pem_block():
    pem = "-----BEGIN PRIVATE KEY-----\n" + S + "\n-----END PRIVATE KEY-----"
    assert S not in redact_text("before\n" + pem + "\nafter") and "after" in redact_text("before\n" + pem + "\nafter")


def test_assignments_and_cli_flags_in_text():
    t = redact_text(f"start --token {S} --port 80 password={S} api_key: {S} \"secret\": \"{S}\" --api-key={S}")
    assert S not in t and "--port 80" in t
    t2 = redact_text("curl -H 'Authorization: Bearer " + S + "'")
    assert S not in t2 and "curl -H" in t2


def test_args_list_after_secret_flag():
    out = redact({"args": ["serve", "--token", S, "--port", "80", "--api-key", RND]}, strict=True)
    assert out["args"] == ["serve", "--token", MARK, "--port", "80", "--api-key", MARK]


# ---- key-like ids are not secrets: idempotency/request/approval/session keys ----------------------
def test_key_like_id_keys_and_flags_survive():
    # kanban_create dry-run argv: the value of --idempotency-key is an id, not a secret
    argv = ["hermes", "kanban", "create", "T", "--idempotency-key", "idem-abc-123",
            "--request-key", "rk-1", "--approval-key", "ap-2"]
    assert redact(argv) == argv
    assert redact(argv, strict=True) == argv
    # the same keys as field names (both profiles)
    for key in ("idempotency_key", "request_key", "approval_key", "session_key", "client_session"):
        assert redact({key: "abc-123-xyz"}, strict=True) == {key: "abc-123-xyz"}
        assert redact({key: "abc-123-xyz"}) == {key: "abc-123-xyz"}


def test_real_secrets_still_masked_besides_key_like_ids():
    # Gegenprobe: the neighbouring actual secrets keep getting masked
    out = redact({"args": ["create", "--idempotency-key", "idem-abc-123", "--api-key", S]}, strict=True)
    assert out["args"] == ["create", "--idempotency-key", "idem-abc-123", "--api-key", MARK]
    t = redact_text(f"token={S} failed")
    assert S not in t and "token=" in t
    assert redact({"env": {"API_SERVER_KEY": S}}, strict=True)["env"]["API_SERVER_KEY"] == MARK
    assert redact({"api_key": S}, strict=True)["api_key"] == MARK


# ---- entropy rule: careful, secret contexts only ----------------------------------------------
def test_entropy_rule_only_in_secret_contexts():
    assert redact({"env": {"A": RND}}, strict=True)["env"]["A"] == MARK
    assert redact({"headers": {"X-Thing": RND}}, strict=True)["headers"]["X-Thing"] == MARK
    assert redact({"notes": RND})["notes"] == RND                           # documented gap: neutral key, neutral context


def test_entropy_rule_does_not_hit_normal_values():
    ctx = {"env": {"LOG_LEVEL": "debug", "HOME": "/home/user", "PYTHONPATH": "/usr/local/lib/hermes-agent/venv/lib/python3.13",
                   "URL": "https://api.example.com/v1/models", "PORT": "8080", "TIMEOUT": 30}}
    assert redact(ctx, strict=True) == ctx
    # inside a credential container an ID / NAME key holds a label (a uuid, a unit name), not a credential
    out = redact({"env": {"ID": "a3f9c1d2-1111-4222-8333-444455556666", "NAME": "hermes-gateway.service"}}, strict=True)
    assert out == {"env": {"ID": "a3f9c1d2-1111-4222-8333-444455556666", "NAME": "hermes-gateway.service"}}
    # an unexplained VALUE under a plain key name is still masked (doubt means mask)
    assert redact({"env": {"SOMETHING": RND}}, strict=True) == {"env": {"SOMETHING": MARK}}
    # ...but the same strings outside a credential container are ids and stay
    assert redact({"id": "a3f9c1d2-1111-4222-8333-444455556666", "unit": "hermes-gateway.service"}) == \
        {"id": "a3f9c1d2-1111-4222-8333-444455556666", "unit": "hermes-gateway.service"}
    assert not looks_random("a" * 40) and not looks_random("short")


# ---- usefulness: what must survive -------------------------------------------------------------
def test_ids_names_paths_timestamps_survive():
    row = {"id": "20261021_150000_ab12cd", "run_id": "run_9f8e7d6c", "name": "researcher", "title": "Fix the token bug",
           "path": "/home/user/.hermes/profiles/researcher/config.yaml", "uuid": "a3f9c1d2-1111-4222-8333-444455556666",
           "commit": "d7c62e8", "ts": "2026-10-21T13:20:43+02:00", "model": "claude-sonnet-5-5", "tokens": 1200,
           "input_tokens": 90, "status": "completed", "approval_id": "req-1", "preview": "token refreshed OK",
           "log": "2026-10-21 gateway started pid=12345 token_expired: true max_tokens=100000 error: password required"}
    assert redact(row) == row


def test_redact_does_not_mutate_input():
    cfg = {"a": {"password": S, "args": ["--token", S]}, "b": [{"api_key": S}]}
    before = copy.deepcopy(cfg)
    redact(cfg, strict=True)
    assert cfg == before
    assert redact(cfg, strict=True) == redact(cfg, strict=True)           # idempotent/pure


def test_idempotent_on_already_redacted():
    once = redact({"a": {"password": S}, "t": "Bearer " + S}, strict=True)
    assert redact(once, strict=True) == once


def test_mode_selection():
    assert mode_for("config", "config_get") == ("strict", "read") and mode_for("mcp", "mcp_get") == ("strict", "read")
    assert mode_for("sessions", "sessions_messages") == ("generic", "read")
    assert mode_for("ops", "ops_gateway_logs") == ("generic", "read")
    assert mode_for("config", "config_set", "write") == ("strict", "write")        # write/admin: no characters at all
    assert mode_for("mcp", "mcp_remove", "admin") == ("strict", "write")
    assert mode_for("admin", "audit_tail", "admin") == ("generic", "read")         # admin tools that only read
    assert mode_for("inbox", "inbox_poll") == ("generic", "read")                  # nothing is exempt any more
    assert mode_for("providers", "secret_list") == ("names", "read") and mode_for("meta", "hermes_describe_tool") is None


# ---- through the tools -------------------------------------------------------------------------
@pytest.fixture()
def call(happ):
    c = TestClient(happ.app)
    _, token = happ.keystore.create("t", Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True))

    def _call(tool_name, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": args}})
        res = r.json()["result"]
        raw = res["content"][0]["text"]
        body = json.loads(raw)
        return (not res["isError"]), body.get("result", body.get("error")), raw
    _call.home = happ.keystore.path.parent.parent.parent
    return _call


def _write_cfg(call):
    (call.home / "config.yaml").write_text(yaml.safe_dump({
        "model": {"default": "m"},
        "dashboard": {"password_hash": "scrypt:32768:8:1$" + S, "session_secret": S, "username": "admin"},
        "auxiliary": {"vision": {"api_key": S, "model": "vm", "timeout": 30}},
        "mcp_servers": {"demo": {"url": f"https://u:{S}@example.invalid/mcp?token={S}", "args": ["--token", S, "-v"],
                                 "env": {"FOO_API_KEY": S, "LOG": "debug"}, "headers": {"Authorization": "Bearer " + S}}},
        "fallback_providers": [{"provider": "p", "model": "m", "api_key": S, "base_url": "https://x.invalid/v1"}],
    }))


def test_config_get_whole_and_sections_never_leak(call):
    _write_cfg(call)
    for args in ({}, {"key": "dashboard"}, {"key": "auxiliary"}, {"key": "dashboard.password_hash"},
                 {"key": "dashboard.session_secret"}, {"key": "mcp_servers"}):
        ok, r, raw = call("config_get", **args)
        assert ok and S not in raw, args
    ok, r, _ = call("config_get", key="dashboard")
    assert r["value"]["username"] == "admin" and r["value"]["password_hash"] == "scry" + STARS + "CDEF"        # read style: 4 + ******** + 4
    ok, r, _ = call("config_get", key="auxiliary")
    assert r["value"]["vision"]["model"] == "vm" and r["value"]["vision"]["timeout"] == 30


def test_mcp_get_list_models_auxiliary_and_fallbacks(call):
    _write_cfg(call)
    for tool, args in (("mcp_get", {"name": "demo"}), ("mcp_list", {}), ("models_auxiliary", {}),
                       ("models_auxiliary", {"task": "vision"}), ("models_fallbacks", {}), ("models_current", {})):
        ok, r, raw = call(tool, **args)
        assert ok and S not in raw, tool
    ok, r, _ = call("models_fallbacks")
    assert r["fallbacks"][0]["base_url"] == "https://x.invalid/v1" and r["fallbacks"][0]["api_key"] == "SYNT" + STARS + "CDEF"


def test_config_set_unset_dry_run_do_not_echo_secrets(call):
    _write_cfg(call)
    ok, r, raw = call("config_set", key="dashboard.password_hash", value="scrypt:1:2:3$" + S, dry_run=True)
    assert ok and S not in raw and r["changes"] == [{"path": "dashboard.password_hash", "change": "changed", "value": SECRET_CHANGED}]
    assert "scry" not in raw and "CDEF" not in raw                           # write style: no edge markers either
    ok, r, raw = call("config_unset", key="dashboard.session_secret", dry_run=True)
    assert ok and S not in raw
    ok, r, raw = call("config_set", key="dashboard.session_secret", value=S + "2")
    assert ok and S not in raw                                              # real write goes through; output redacted
    assert S + "2" in (call.home / "config.yaml").read_text()               # ...and the DATA is untouched by redaction


def test_text_channels_are_redacted_centrally(happ, call, monkeypatch):
    from hermcp.registry import Tool
    from hermcp.tools import reg
    text = f"line1 password={S} Authorization: Bearer {S} https://u:{S}@h/x ok-id=run_123"
    for name, toolset in (("zz_sessions", "sessions"), ("zz_ops", "ops"), ("zz_cron", "cron"),
                          ("zz_kanban", "kanban"), ("zz_skills", "skills"), ("zz_chat", "chat"), ("zz_status", "status")):
        if name not in reg.tools:
            reg.add(Tool(name=name, toolset=toolset, access="read", description="t",
                         handler=lambda ctx, **kw: {"content": text, "rows": [{"preview": text, "id": "s_1"}]}))
        ok, r, raw = call(name)
        assert ok and S not in raw, name
        assert "run_123" in raw and r["rows"][0]["id"] == "s_1"
    ok, r, raw = call("zz_status")
    assert "line1" in r["content"]


def test_ops_gateway_logs_redacted(call, monkeypatch):
    import subprocess
    out = f"2026-10-21T13:00:00+02:00 gw[1]: connecting with token={S} to https://api.example/v1?api_key={S}\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))
    ok, r, raw = call("ops_gateway_logs", lines=5)
    assert ok and S not in raw and "2026-10-21T13:00:00+02:00" in r["log"] and "gw[1]" in r["log"]


def test_untouched_toolsets_keep_their_content(call):
    ok, r, _ = call("secret_set", name="MY_TEST_TOKEN", value=S)
    assert ok and r["name"] == "MY_TEST_TOKEN"
    ok, r, raw = call("secret_list", prefix="MY_TEST")
    assert ok and r["secrets"][0]["name"] == "MY_TEST_TOKEN" and S not in raw
    assert S in (call.home / ".env").read_text()                            # data untouched


def test_error_messages_are_redacted(call):
    ok, r, raw = call("config_get", key=f"x.token={S}")
    assert S not in raw
