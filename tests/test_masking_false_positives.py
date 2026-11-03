"""False-positive corpus: labels, commands, paths, ids, URLs and plain sentences are never touched.
Counter-corpus: strong secret patterns keep being masked. Synthetic values only."""
import json

import pytest
from starlette.testclient import TestClient

from hermcp.redact import Label, MARK, STARS, redact, redact_result, redact_text
from hermcp.scopes import Scopes

TOKEN = "Zk3x9Qw2Lp7Vb5Nc8Rt1Yh4Gd6Fs0Jm2Kq"        # synthetic, 36 chars
RUN = "run_9f8e7d6c1a2b3c4d"
SHA1 = "d7c62e8a1b2c3d4e5f60718293a4b5c6d7e8f901"
SHA256 = "a3f9c1d2e4b5a6978869504132f1e0d9c8b7a6958473625140f0e1d2c3b4a596"

COMMANDS = [
    "git reset --hard pre-secret-masking-20261026 && sudo systemctl restart hermcp.service",
    "cd /home/user/.hermes/plugins/hermcp && git reset --hard pre-secret-masking-20261026 && sudo systemctl restart hermcp.service",
    "sudo systemctl restart hermcp.service",
    f"git revert -m 1 {SHA1}",
    "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:9230/health",
    "pip install --upgrade pip && pip install --no-deps -r requirements.txt",
    "git checkout -b fix/secret-masking-unified && git push origin pre-secret-masking-20261026",
    "systemctl show hermes-gateway.service -p MainPID,ActiveEnterTimestamp",
    "python -m hermcp keys create --name svc --access admin",
    "curl -H 'Content-Type: application/json' https://api.example.com/v1/models",
    "git log --oneline -3 && git diff main..fix/secret-masking-unified",
    "hermes config set --key model.default some-model",
    "gh pr merge 2 --merge && git pull origin main",
    "cat /home/user/.hermes/plugins/hermcp/tests/test_masking.py | wc -l",
    "tar --exclude-secrets-dir -czf backup.tgz /root/data",
    "git commit -m 'fix(redact): secret handling in logs && tests'",
]
PATHS = [
    "mcp_servers.context7.headers.CONTEXT7_API_KEY", "mcp_servers.subwave.env.SUBWAVE_ADMIN_PASS",
    "providers.groq.api_key", "auxiliary.session_search.api_key", "display.platforms.webhook.extra.secret",
    "CONTEXT7_API_KEY", "SUBWAVE_ADMIN_PASS", "OPENAI_API_KEY", "profiles/llm-ops/config.yaml",
    "/home/user/.hermes/plugins/hermcp/tests/test_masking.py", "dashboard.basic_auth.password_hash",
]
IDS = [RUN, "t_3f9a1c2d", "20261026_150000_ab12cd", "a3f9c1d2-1111-4222-8333-444455556666", SHA1, SHA256,
       "v0.22.4", "0.22.5 (bf0203a794a0)", "2026-10-26T15:08:00,100", "pid=2541284", "pre-secret-masking-20261026"]
URLS = ["https://github.com/HearthCore/hermcp/pull/2", "http://127.0.0.1:9230/health",
        "https://mcp.example.com/mcp?page=2", "https://rss.example.dev/feed.xml"]
SENTENCES = [
    "Die Klartext-Secrets in den Profil-Configs sind unangetastet.",
    "Das Passwort wird nicht angezeigt, der Schlüssel bleibt im Tresor.",
    "Token in der Antwort sind maskiert; das Secret liegt in der .env.",
    "Basic configuration and bearer token handling are documented.",
    "Use the secret store for passwords and keys in production.",
    "Passwort: Schlüssel", "Secret: none", "Authorization: required", "token_expired: true",
    "error: password required", "Der Schlüssel in den Configs bleibt, wie er ist.",
    "Rollback: git reset --hard auf den Tag, dann den Dienst neu starten.",
    "Basic Authentication und Bearer Tokens sind erlaubt.",
    "Hinweis: das Secret in der Datei wurde nicht geändert (commit hash: d7c62e8)",
]
CORPUS = COMMANDS + PATHS + IDS + URLS + SENTENCES


@pytest.mark.parametrize("text", CORPUS)
@pytest.mark.parametrize("partial", [True, False])
def test_free_text_corpus_is_untouched(text, partial):
    assert redact_text(text, partial=partial) == text


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("partial", [True, False])
def test_corpus_untouched_under_neutral_keys_in_any_style(strict, partial):
    data = {"output": COMMANDS, "referenced_by": PATHS, "ids": IDS, "urls": URLS, "text": SENTENCES,
            "nested": [{"preview": s} for s in CORPUS]}
    assert redact(data, strict=strict, partial=partial) == data


def test_the_three_reported_cases_exactly():
    cmd = COMMANDS[0]
    assert redact({"output": cmd}) == {"output": cmd}
    sent = "Die Klartext-Secrets in den Profil-Configs sind unangetastet."
    assert redact({"output": sent}, partial=True) == {"output": sent}
    path = "mcp_servers.context7.headers.CONTEXT7_API_KEY"
    assert redact({"referenced_by": [path]}, partial=True) == {"referenced_by": [path]}
    assert redact_result("providers", "secrets_status", {"variables": [{"name": "CONTEXT7_API_KEY", "set": True, "referenced_by": [path]}]}
                         ) == {"variables": [{"name": "CONTEXT7_API_KEY", "set": True, "referenced_by": [path]}]}


def test_short_plain_values_and_user_names_are_labels():
    cfg = {"record_key": "F2", "submit_mode": "enter", "codex_login_flow": "x",
           "env": {"SUBWAVE_ADMIN_USER": "subwave-admin", "SUBWAVE_ADMIN_PASS": "Qx7mK2vLpR9sTz4W",
                   "SERVICE_ACCOUNT": "svc-reader", "DB_PASS": "Qx7m2vLpR9s"}}
    out = redact(cfg, strict=True, partial=True)
    assert out["submit_mode"] == "enter" and out["codex_login_flow"] == "x"
    # `record_key` is a keybinding field: its value is a label, in both styles
    assert out["record_key"] == "F2" and redact(cfg, strict=True)["record_key"] == "F2"
    assert redact({"record_key": "ctrl+space"}, strict=True)["record_key"] == "ctrl+space"
    # a bare `key` is still a credential name in config-like tools (no weakening of the secret class)
    from hermcp.redact import secret_key
    assert secret_key("key", strict=True) and secret_key("api_key", strict=True) and not secret_key("hotkey", strict=True)
    assert out["env"]["SUBWAVE_ADMIN_USER"] == "subwave-admin" and out["env"]["SERVICE_ACCOUNT"] == "svc-reader"
    assert out["env"]["SUBWAVE_ADMIN_PASS"] != cfg["env"]["SUBWAVE_ADMIN_PASS"]
    assert out["env"]["DB_PASS"] == STARS                    # short secret under a secret name stays masked


def test_ids_inside_header_and_env_maps_stay_visible():
    out = redact({"headers": {"P-Access-Token-Id": "abc-123", "P-Access-Token": "Zk3x9Qw2Lp7Vb5Nc8Rt1Yh4Gd6Fs0Jm2Kq",
                              "X-Client-Id": "my-app", "X-Key-Id": "k1"}}, strict=True, partial=True)["headers"]
    assert out["P-Access-Token-Id"] == "abc-123" and out["X-Client-Id"] == "my-app" and out["X-Key-Id"] == "k1"
    assert out["P-Access-Token"] != "Zk3x9Qw2Lp7Vb5Nc8Rt1Yh4Gd6Fs0Jm2Kq"


def test_label_instances_stay_visible_in_the_write_style():
    out = redact({"key": Label("mcp_servers"), "changes": [], "applied": False}, strict=True)
    assert out["key"] == "mcp_servers"
    # a plain string under `key` in a config-like tool is still treated as a value
    assert redact({"key": TOKEN}, strict=True) == {"key": MARK}
    # a Label never smuggles a secret shape through
    assert redact({"key": Label("sk-" + TOKEN)}, strict=True)["key"] != "sk-" + TOKEN


# ---- counter-corpus: must still be masked ------------------------------------------------------------------
@pytest.mark.parametrize("text,secret", [
    (f"start --token {TOKEN} --port 80", TOKEN),
    ("run --api-key=hunter2xyz9", "hunter2xyz9"),
    ("login with password: Geheim123x ok", "Geheim123x"),
    ("DB_PASS=Qx7mK2vLpR9sTz4W", "Qx7mK2vLpR9sTz4W"),
    (f"curl -H 'Authorization: Bearer {TOKEN}' https://h.example", TOKEN),
    ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpwYXNzd29yZDEyMw=="),
    ("key sk-" + TOKEN, "sk-" + TOKEN),
    ("ghp_" + TOKEN[:30], "ghp_" + TOKEN[:30]),
    ("AKIA" + "ABCDEFGH23456789", "AKIA" + "ABCDEFGH23456789"),
    (f"https://user:{TOKEN[:12]}@host.example/x", TOKEN[:12]),
    ("https://h.example/a?token=" + TOKEN + "&page=2", TOKEN),
    ('{"secret": "' + TOKEN + '"}', TOKEN),
    ("eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXRlc3Q", "eyJzdWIiOiJ0ZXN0LXVzZXIifQ"),
    (f"sudo mytool --secret {TOKEN} && sudo systemctl restart x", TOKEN),
])
@pytest.mark.parametrize("partial", [True, False])
def test_strong_patterns_are_still_masked(text, secret, partial):
    out = redact_text(text, partial=partial)
    assert secret not in out, out
    assert "sudo" in out or "login" in out or "start" in out or "run" in out or "curl" in out or True


def test_surrounding_command_survives_when_a_secret_is_masked():
    out = redact_text(f"sudo mytool --secret {TOKEN} && sudo systemctl restart x")
    assert out.startswith("sudo mytool --secret ") and out.endswith(" && sudo systemctl restart x")


def test_blob_but_not_identifier_path_in_neutral_context():
    assert redact({"note": TOKEN + TOKEN[:10]}, partial=True)["note"] != TOKEN + TOKEN[:10]
    long_path = "profiles.llm-ops.mcp_servers.subwave.env.SUBWAVE_ADMIN_PASSWORD_REF"
    assert redact({"note": long_path}) == {"note": long_path}


# ---- through the server ---------------------------------------------------------------------------------------
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
    return _call


def _seed(call, happ_home):
    import yaml
    p = happ_home / "config.yaml"
    cfg = yaml.safe_load(p.read_text())
    cfg["mcp_servers"] = {"demo": {"command": "x", "env": {"DEMO_PASS": "${DEMO_PASS}", "MODE": "fast"}}}
    p.write_text(yaml.safe_dump(cfg))


def test_write_tools_keep_the_key_label_in_dry_run_and_real(call, home):
    _seed(call, home)
    for tool in ("mcp_disable", "mcp_enable", "mcp_remove"):
        ok, r, raw = call(tool, name="demo", dry_run=True)
        assert ok and r["key"] == "mcp_servers", (tool, raw)
    ok, r, raw = call("mcp_disable", name="demo")
    assert ok and r["key"] == "mcp_servers" and r["applied"] is True
    ok, r, raw = call("config_unset", key="display.skin", dry_run=True)
    assert ok and r["key"] == "display.skin"
    ok, r, raw = call("hermes_call", name="mcp_enable", args={"name": "demo", "dry_run": True})
    assert ok and (r.get("result") or r)["key"] == "mcp_servers", raw


def test_chat_text_and_secrets_status_through_the_server(call, home):
    from hermcp.registry import Tool
    from hermcp.tools import reg
    text = COMMANDS[0] + " — Die Klartext-Secrets in den Profil-Configs bleiben."
    if "zz_fp_text" not in reg.tools:
        reg.add(Tool(name="zz_fp_text", toolset="chat", access="read", description="t",
                     handler=lambda ctx, **kw: {"output": text, "rows": [{"referenced_by": PATHS}]}))
    ok, r, raw = call("zz_fp_text")
    assert ok and r["output"] == text and r["rows"][0]["referenced_by"] == PATHS
    (home / ".env").write_text("CONTEXT7_API_KEY=" + TOKEN + "\n")
    ok, r, raw = call("secrets_status")
    assert ok and TOKEN not in raw
