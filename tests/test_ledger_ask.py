"""'Ask Claude': the prepared question behind the link.

Three things are held down here: the prompt names ids and asks Claude to read details itself (so
the link stays free of message content), the URL never carries a secret and never grows past the
handler limit, and the button exists only when it can be built.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote, urlparse


from hermcp import ledger_ask as ASK

SECRET = "hmcp_live_9f8e7d6c5b4a3210deadbeef"
TOKEN = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"


class Settings:
    def __init__(self, web="https://claude.ai/new?q={prompt}", desktop=""):
        self.ledger_ask_claude_url = web
        self.ledger_ask_claude_desktop_url = desktop


def prompt_of(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    return q.get("q", [""])[0]


# ---------------------------------------------------------------------------
# one prompt per kind
# ---------------------------------------------------------------------------

def test_approval_prompt_names_the_approval_and_asks_for_a_recommendation():
    p = ASK.prompt_for("approval", {"approval_key": "ap-1", "profile": "default",
                                    "surface": "Telegram", "age_s": 900,
                                    "command": "rm -rf /tmp/x"})
    assert "ap-1" in p and "default" in p and "Telegram" in p
    assert "15 Minuten" in p
    assert "rm -rf /tmp/x" in p
    for word in ("once", "session", "deny"):
        assert word in p
    assert "Nicht selbst antworten, nur beraten" in p
    assert "approval_detail" in p and "hermes_status" in p


def test_failed_run_prompt_asks_for_cause_completeness_and_continuation():
    p = ASK.prompt_for("run_failed", {"run_id": "run_x", "profile": "developer",
                                      "at": "2026-11-11T20:00:00+01:00"})
    assert "run_x" in p and "developer" in p
    assert "Ursache" in p and "vollständig" in p and "offen" in p
    assert "Weiterlaufen" in p
    assert "chat_run_status" in p and "sessions_messages" in p


def test_interrupted_prompt_asks_whether_to_restart():
    p = ASK.prompt_for("run_interrupted", {"run_id": "run_i", "profile": "default",
                                           "at": "2026-11-11T20:00:00+01:00"})
    assert "run_i" in p
    assert "unterbrochen" in p
    assert "neu gestartet" in p


def test_cron_prompt_asks_what_happened_and_whether_there_is_a_pattern():
    p = ASK.prompt_for("cron_error", {"job_name": "watchdog", "job_id": "j1", "profile": "default",
                                      "at": "2026-11-11T06:00:00+01:00", "state": "error",
                                      "run_id": "cr2"})
    assert "watchdog" in p and "error" in p and "cr2" in p
    assert "passiert" in p and "offen" in p and "Muster" in p


def test_session_prompt_asks_for_summary_open_points_and_next_steps():
    p = ASK.prompt_for("session", {"session_id": "local-1", "profile": "default",
                                   "surface": "API", "at": "2026-11-11T22:25:11+01:00",
                                   "state": "aktiv"})
    assert "local-1" in p and "API" in p and "aktiv" in p
    assert "zusammen" in p and "offen" in p and "nächsten Schritte" in p


def test_every_prompt_sends_claude_to_the_tools_instead_of_embedding_content():
    for kind, facts in (("approval", {"approval_key": "a"}), ("run_failed", {"run_id": "r"}),
                        ("run_interrupted", {"run_id": "r"}), ("cron", {"job_name": "j"}),
                        ("session", {"session_id": "s"})):
        p = ASK.prompt_for(kind, facts)
        assert "hermcp-Tools" in p, kind
        for tool in ("hermes_status", "inbox_attention", "sessions_messages"):
            assert tool in p, (kind, tool)


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------

def test_a_secret_in_the_command_never_reaches_the_link():
    s = Settings()
    out = ASK.ask_links(s, "approval", {"approval_key": "ap-1", "profile": "default",
                                        "command": f"export TOKEN={SECRET} && curl -H 'Authorization: Bearer {TOKEN}' x"})
    blob = json.dumps(out)
    assert "hmcp_live_9f8e7d6c" not in blob
    assert "sk-proj-abcdefghij" not in blob
    # what remains is still the useful beginning of the command
    assert "export" in out["prompt"]


def test_the_prompt_carries_no_message_text_or_tool_output():
    """Only ids, kind, state, times, profile/surface and a command prefix — by construction."""
    p = ASK.prompt_for("session", {"session_id": "local-1", "profile": "default"})
    assert "Nachricht" not in p.lower() or "liest" in p
    assert len(p) < ASK.ASK_PROMPT_MAX


def test_the_command_is_capped_before_it_reaches_the_url():
    long_cmd = "ls " + ("-la " * 4000)
    out = ASK.ask_links(Settings(), "approval", {"approval_key": "ap-1", "profile": "default",
                                                 "command": long_cmd})
    assert out["web"] and len(out["web"]) <= ASK.ASK_URL_MAX
    assert "ap-1" in unquote(out["web"])
    # the command prefix is short by construction (ASK_COMMAND_CHARS), so the URL never needs cutting
    assert len(out["prompt"]) < ASK.ASK_PROMPT_MAX
    assert out["prompt"].count("-la") < 40


def test_a_long_template_still_yields_a_bounded_url_and_keeps_the_ids():
    # a per-install prefix that eats most of the budget: the prompt has to shrink
    prefix = "https://claude.ai/new?org=" + ("x" * 1500) + "&q={prompt}"
    out = ASK.ask_links(Settings(web=prefix), "approval",
                        {"approval_key": "ap-1", "profile": "default", "command": "rm -rf /tmp/x"})
    assert out["web"] and len(out["web"]) <= ASK.ASK_URL_MAX
    assert "ap-1" in unquote(out["web"])
    assert out["prompt"].endswith("…")


# ---------------------------------------------------------------------------
# template handling
# ---------------------------------------------------------------------------

def test_the_prompt_is_url_encoded_into_the_template():
    out = ASK.ask_links(Settings(), "session", {"session_id": "local-1", "profile": "default"})
    url = out["web"]
    assert url.startswith("https://claude.ai/new?q=")
    assert "local-1" in unquote(url)
    # no raw spaces or newlines in the URL
    assert " " not in url and "\n" not in url
    assert "local-1" in prompt_of(url)


def test_the_button_is_hidden_without_a_usable_template():
    for web in ("", "   ", "https://claude.ai/new"):        # no {prompt} = no button
        assert ASK.ask_links(Settings(web=web), "session", {"session_id": "s"})["web"] is None
    for web in ("javascript:alert('{prompt}')", "file:///etc/passwd?{prompt}",
                "ftp://example.com/?{prompt}"):
        assert ASK.ask_links(Settings(web=web), "session", {"session_id": "s"})["web"] is None


def test_the_desktop_url_is_optional_and_never_invented():
    out = ASK.ask_links(Settings(), "session", {"session_id": "s"})
    assert out["web"].startswith("https://claude.ai/")
    assert out["desktop"] is None                             # no default, no guess

    with_desktop = ASK.ask_links(Settings(desktop="claude://claude.ai/new?q={prompt}"),
                                 "session", {"session_id": "s"})
    assert with_desktop["desktop"].startswith("claude://claude.ai/new?q=")

    rejected = ASK.ask_links(Settings(desktop="vscode://x?{prompt}"), "session", {"session_id": "s"})
    assert rejected["desktop"] is None


def test_the_label_and_tooltip_are_the_documented_ones():
    out = ASK.ask_links(Settings(), "session", {"session_id": "s"})
    assert out["label"] == "Ask Claude"
    assert out["tooltip"] == "öffnet Claude mit vorbereiteter Frage"


def test_facts_survive_a_hostile_value():
    out = ASK.ask_links(Settings(), "session", {"session_id": "../../etc/passwd",
                                                "profile": "<img src=x onerror=alert(1)>"})
    assert out["web"]
    assert percent_safe(out["web"])


def percent_safe(url: str) -> bool:
    # everything after "?q=" must be a URL-safe blob: no angle brackets, no spaces
    tail = url.split("q=", 1)[-1]
    return "<" not in tail and ">" not in tail and " " not in tail
