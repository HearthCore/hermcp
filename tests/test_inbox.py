"""Event inbox: tools, scopes, HTTP endpoint, rotation, limits."""
import json
import sqlite3

import pytest
from starlette.testclient import TestClient

from hermcp.app import create_app
from hermcp.keys import Scopes
from hermcp.settings import load_settings


def make(home, **env):
    base = {"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1", **env}
    app = create_app(load_settings(base))
    c = TestClient(app.app)
    keys = {
        "voice": Scopes(access="read", toolsets=["inbox"]),
        "producer": Scopes(access="read", toolsets=["inbox"], inbox_push=True),
        "admin": Scopes(access="admin", approvals=True, secrets_write=True, dangerous=True),   # no inbox_push
        "nostatus": Scopes(access="read", toolsets=["status"], inbox_push=True),               # inbox toolset missing
    }
    tok = {n: app.keystore.create(n, sc)[1] for n, sc in keys.items()}

    def call(who, tool, /, **args):
        r = c.post("/mcp", headers={"Authorization": f"Bearer {tok[who]}"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        res = r.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))

    def post(who, payload, raw=None):
        h = {"Authorization": f"Bearer {tok[who]}"} if who else {}
        return c.post("/inbox", headers=h, content=raw if raw is not None else json.dumps(payload))
    call.post, call.client, call.tok, call.app = post, c, tok, app
    return call


@pytest.fixture()
def box(home):
    return make(home)


def push(box, **kw):
    a = {"source": "hermes", "kind": "job_finished", "summary": "ok", **kw}
    return box("producer", "inbox_push", **a)


def test_push_poll_ack_roundtrip(box):
    ok, r = push(box, summary="run abc done", ref="run_abc", severity="warning")
    ev = r["event"]
    assert ok and ev["id"] == 1 and ev["ts"].endswith("Z") and ev["acked"] is False
    assert ev["ref"] == "run_abc" and ev["severity"] == "warning" and ev["pushed_by"] == "producer"
    ok, r = box("voice", "inbox_poll")
    assert ok and r["untrusted"] is True and "not instructions" in r["notice"]
    assert [e["id"] for e in r["events"]] == [1] and r["unread_total"] == 1
    assert box("voice", "inbox_poll")[1]["unread_total"] == 1          # poll does not ack
    ok, r = box("voice", "inbox_ack", ids=[1, 99])
    assert ok and r["acked"] == [1] and r["not_found"] == [99] and r["unread_total"] == 0
    assert box("voice", "inbox_poll")[1]["events"] == []
    ok, r = box("voice", "inbox_list")
    assert r["untrusted"] and r["events"][0]["acked"] is True and r["events"][0]["acked_at"]


def test_filters_limit_order(box):
    for i in range(5):
        push(box, source="hermes" if i % 2 == 0 else "script", kind="job_failed" if i < 2 else "info", summary=f"e{i}")
    ok, r = box("voice", "inbox_poll", limit=2)
    assert [e["summary"] for e in r["events"]] == ["e0", "e1"] and r["unread_total"] == 5   # oldest first
    assert {e["source"] for e in box("voice", "inbox_poll", source="script")[1]["events"]} == {"script"}
    assert len(box("voice", "inbox_poll", kind="job_failed")[1]["events"]) == 2
    assert [e["summary"] for e in box("voice", "inbox_poll", after_id=3)[1]["events"]] == ["e3", "e4"]
    box("voice", "inbox_ack", ids=[1, 2])
    ok, r = box("voice", "inbox_list", status="acked")
    assert [e["id"] for e in r["events"]] == [2, 1]                                          # newest first
    assert len(box("voice", "inbox_list", status="unread")[1]["events"]) == 3
    assert [e["id"] for e in box("voice", "inbox_list", before_id=3, limit=1)[1]["events"]] == [2]


def test_length_and_content_limits(box):
    ok, r = push(box, summary="x" * 501)
    assert not ok and r["code"] == "invalid_arguments"
    assert not push(box, source="s" * 65)[0] and not push(box, kind="k" * 49)[0]
    assert not push(box, ref="r" * 201)[0] and not push(box, severity="panic")[0]
    assert not push(box, kind="rm -rf /")[0]                       # kind/source: token charset only
    ok, r = push(box, summary="line1\nline2\x00\x07 key hmcp_abcdef12_" + "z" * 30 + " Bearer abc.def")
    s = r["event"]["summary"]
    assert ok and "\n" not in s and "\x00" not in s and "hmcp_abcdef12" not in s and "[redacted]" in s
    assert push(box, summary="x" * 500)[0]
    assert not box("producer", "inbox_push", source="a", kind="b")[0]        # summary missing


def test_rotation_by_count_and_age(home):
    b = make(home, HERMCP_INBOX_MAX_EVENTS="5")
    for i in range(8):
        b("producer", "inbox_push", source="s", kind="k", summary=f"e{i}")
    b("voice", "inbox_ack", ids=[1])        # acked event goes first when over the cap
    b("producer", "inbox_push", source="s", kind="k", summary="e8")
    ids = [e["id"] for e in b("voice", "inbox_list")[1]["events"]]
    assert len(ids) == 5 and 1 not in ids and ids[0] == 9
    # age: acked events older than retention are dropped on next push
    b.app.settings.inbox_retention_days = 1.0
    b("voice", "inbox_ack", ids=[ids[-1]])
    db = sqlite3.connect(b.app.settings.data / "inbox.db")
    db.execute("UPDATE events SET acked_at='2020-01-01T00:00:00Z' WHERE id=?", (ids[-1],))
    db.commit()
    b("producer", "inbox_push", source="s", kind="k", summary="trigger")
    left = [e["id"] for e in b("voice", "inbox_list")[1]["events"]]
    assert ids[-1] not in left


def test_scopes(box):
    ok, r = box("voice", "inbox_push", source="a", kind="b", summary="c")
    assert not ok and r["code"] == "forbidden" and "inbox_push" in r["message"]
    ok, r = box("admin", "inbox_push", source="a", kind="b", summary="c")      # admin + '*' does not imply it
    assert not ok and r["code"] == "forbidden"
    assert box("admin", "inbox_poll")[0]                                         # but admin may read
    ok, r = box("nostatus", "inbox_push", source="a", kind="b", summary="c")
    assert not ok and "toolset" in r["message"]
    assert not box("nostatus", "inbox_poll")[0]
    # visibility in tools/list
    def names(who):
        r = box.client.post("/mcp", headers={"Authorization": f"Bearer {box.tok[who]}"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        return {t["name"] for t in r.json()["result"]["tools"]}
    assert {"inbox_poll", "inbox_ack"} <= names("voice") and "inbox_push" not in names("voice")
    assert "inbox_push" in names("producer")


def test_push_rate_limit(home):
    b = make(home, HERMCP_INBOX_RATE_PER_MIN="3")
    res = [b("producer", "inbox_push", source="s", kind="k", summary=str(i)) for i in range(5)]
    assert [ok for ok, _ in res] == [True, True, True, False, False] and res[3][1]["code"] == "rate_limited"


def test_http_endpoint(box):
    r = box.post("producer", {"source": "cron", "kind": "info", "summary": "nightly done", "ref": "job1"})
    assert r.status_code == 201 and r.json()["result"]["event"]["source"] == "cron"
    assert "hmcp_" not in r.text                                                 # no key material in answers
    assert box.post(None, {}).status_code == 401
    assert box.post("voice", {"source": "a", "kind": "b", "summary": "c"}).status_code == 403
    assert box.post("producer", {"source": "a", "kind": "b", "summary": "x" * 600}).status_code == 400
    assert box.post("producer", None, raw="not json").status_code == 400
    assert box.post("producer", None, raw="[1]").status_code == 400
    assert box.post("producer", {"source": "a", "kind": "b", "summary": "x" * 9000}).status_code == 413
    assert box("voice", "inbox_poll")[1]["unread_total"] == 1                    # only the valid one landed


def test_http_rate_limit(home):
    b = make(home, HERMCP_INBOX_RATE_PER_MIN="1")
    p = {"source": "a", "kind": "b", "summary": "c"}
    assert b.post("producer", p).status_code == 201
    r = b.post("producer", p)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_audit_has_no_token(box):
    push(box, summary="hello")
    log = (box.app.settings.data / "audit.jsonl").read_text()
    assert "inbox_push" in log and box.tok["producer"] not in log


def test_key_scope_roundtrip():
    sc = Scopes.from_dict({"access": "read", "toolsets": ["inbox"], "inbox_push": True})
    assert sc.inbox_push and Scopes.from_dict(sc.to_dict()).inbox_push
    assert Scopes().inbox_push is False


def test_ack_batch_limits(box):
    assert not box("voice", "inbox_ack", ids=[])[0]
    assert not box("voice", "inbox_ack", ids=list(range(1, 102)))[0]
    assert not box("voice", "inbox_ack", ids=["1"])[0]
