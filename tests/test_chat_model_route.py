"""chat_send per-run model route: model / provider / model_options.

The fake gateway mirrors the Hermes api_server contract that matters here (rev 83f882d7bab6, verified in
gateway/platforms/api_server.py `_request_agent_overrides`, api_server_runs.py and api_server_run_idempotency):
  * POST /v1/runs reads model / provider / model_options from the body;
  * the Idempotency-Key fingerprint covers the WHOLE body, so the same key with another body is HTTP 409;
  * precedence: a session's stored model binding beats the per-request model, which beats the global default;
  * the run status carries `model` (an echo of the request) and `runtime` {provider, model, route_source,
    requested} (what really served the run). The echo and the runtime can differ.
"""
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.testclient import TestClient

from hermcp.app import create_app
from hermcp.scopes import Scopes
from hermcp.settings import load_settings
from hermcp.tools import chat

ME = os.getpid()
GLOBAL = {"provider": "default-prov", "model": "default-model"}


class GW(BaseHTTPRequestHandler):
    runs: dict = {}
    idem: dict = {}
    posts: list = []
    steers: list = []
    bound: dict = {}         # session_id -> {"provider", "model"}: a stored binding / `/model` override

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        if self.path.endswith("/steer"):
            GW.steers.append(body)
            return self._send(200, {"accepted": True})
        GW.posts.append(body)
        key = self.headers.get("Idempotency-Key", "")
        fp = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if key in GW.idem:
            old_fp, rid = GW.idem[key]
            if old_fp != fp:
                return self._send(409, {"error": {"code": "idempotency_key_conflict", "message": "different payload"}})
            return self._send(202, {"run_id": rid, "status": "queued", "replayed": True})
        rid = f"run_{len(GW.runs) + 1}"
        sid = body.get("session_id") or rid
        requested = {"provider": body.get("provider") or "", "model": body.get("model") or ""}
        if sid in GW.bound:
            served, source = GW.bound[sid], "session"
        elif any(requested.values()):
            served = {"provider": requested["provider"] or GLOBAL["provider"], "model": requested["model"] or GLOBAL["model"]}
            source = "raw_request"
        else:
            served, source = GLOBAL, "global"
        runtime = {**served, "route_source": source, **({"requested": requested} if any(requested.values()) else {})}
        GW.runs[rid] = {"object": "hermes.run", "run_id": rid, "status": "queued", "session_id": sid,
                        "model": body.get("model") or "hermes-agent", "runtime": runtime}
        GW.idem[key] = (fp, rid)
        return self._send(202, {"run_id": rid, "status": "queued", "replayed": False})

    def do_GET(self):
        st = GW.runs.get(self.path.rsplit("/", 1)[-1])
        return self._send(200, st) if st else self._send(404, {"error": "unknown"})

    def log_message(self, *a):
        pass


@pytest.fixture()
def gw():
    GW.runs, GW.idem, GW.posts, GW.steers, GW.bound = {}, {}, [], [], {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), GW)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture()
def rig(home, gw):
    (home / "gateway_state.json").write_text(json.dumps({"pid": ME, "start_time": 1, "gateway_state": "running",
                                                         "platforms": {}, "served_profiles": ["default", "researcher"]}))
    app = create_app(load_settings({"HERMES_HOME": str(home), "HERMCP_ALLOWED_HOSTS": "testserver,127.0.0.1"}))
    app.settings.hermes_api_url = f"http://127.0.0.1:{gw.server_port}"
    client = TestClient(app.app)
    token = app.keystore.create("k", Scopes(access="write", toolsets=["chat", "inbox"]))[1]

    class R:
        pass
    r = R()
    r.app = app

    def call(tool, /, **args):
        resp = client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": tool, "arguments": args}})
        res = resp.json()["result"]
        body = json.loads(res["content"][0]["text"])
        return (not res["isError"]), body.get("result", body.get("error"))
    r.call = call
    return r


OPTS = {"reasoning": {"effort": "low"}}


# ---- schema ----------------------------------------------------------------------------------------------------
def test_tool_schema_has_optional_model_parameters(rig):
    tool = rig.app.registry.tools["chat_send"]
    for name in ("model", "provider", "model_options"):
        assert name in tool.properties
        assert name not in tool.required
    assert tool.required == ["message"]
    mo = tool.properties["model_options"]
    assert mo["additionalProperties"] is False and set(mo["properties"]) == set(chat.MODEL_OPTION_KEYS)
    assert "model" in tool.description and "runtime" in tool.description
    assert "runtime" in rig.app.registry.tools["chat_run_status"].description


# ---- backwards compatibility -----------------------------------------------------------------------------------
def test_without_model_parameters_payload_is_unchanged(rig):
    ok, r = rig.call("chat_send", message="hello")
    assert ok and GW.posts == [{"input": "hello"}]
    assert "requested_route" not in r and "route_note" not in r
    ok, r = rig.call("chat_send", message="again", session_id="s_1")
    assert ok and GW.posts[-1] == {"input": "again", "session_id": "s_1"}


# ---- payload ---------------------------------------------------------------------------------------------------
def test_model_provider_and_options_reach_the_api_payload(rig):
    ok, r = rig.call("chat_send", message="Fasse die Ergebnisse zusammen.", provider="example-provider",
                     model="example-model", model_options={"reasoning": {"effort": "low"}})
    assert ok
    assert GW.posts == [{"input": "Fasse die Ergebnisse zusammen.", "model": "example-model",
                         "provider": "example-provider", "model_options": {"reasoning": {"effort": "low"}}}]
    assert r["requested_route"] == {"model": "example-model", "provider": "example-provider",
                                    "model_options": {"reasoning": {"effort": "low"}}}
    assert "route_note" not in r                      # new session: nothing outranks the request


def test_each_parameter_works_alone_and_values_are_stripped(rig):
    assert rig.call("chat_send", message="a", model="  m-1  ")[0]
    assert GW.posts[-1] == {"input": "a", "model": "m-1"}
    assert rig.call("chat_send", message="b", provider="prov-x")[0]
    assert GW.posts[-1] == {"input": "b", "provider": "prov-x"}
    assert rig.call("chat_send", message="c", model_options={"fast": True})[0]
    assert GW.posts[-1] == {"input": "c", "model_options": {"fast": True}}
    assert rig.call("chat_send", message="d", model_options={"reasoning_effort": "high", "service_tier": "priority"})[0]
    assert GW.posts[-1]["model_options"] == {"reasoning_effort": "high", "service_tier": "priority"}


def test_existing_session_adds_the_caveat(rig):
    ok, r = rig.call("chat_send", message="x", session_id="s_old", model="m")
    assert ok and "runtime" in r["route_note"] and "binding" in r["route_note"]


def test_interrupt_mode_starts_a_run_with_the_route(rig):
    ok, r = rig.call("chat_send", message="replace", session_id="s_1", mode="interrupt", model="m-2")
    assert ok and GW.posts[-1] == {"input": "replace", "session_id": "s_1", "model": "m-2"}


# ---- dry run ---------------------------------------------------------------------------------------------------
def test_dry_run_shows_the_requested_route_and_starts_nothing(rig):
    ok, r = rig.call("chat_send", message="x", provider="p", model="m", model_options=OPTS, dry_run=True)
    assert ok and r["dry_run"] is True and r["applied"] is False
    assert r["requested_route"] == {"model": "m", "provider": "p", "model_options": OPTS}
    assert GW.posts == [] and GW.runs == {}
    ok, r = rig.call("chat_send", message="x", dry_run=True)
    assert ok and "requested_route" not in r


def test_dry_run_validates_too(rig):
    ok, e = rig.call("chat_send", message="x", model_options={"api_key": "k"}, dry_run=True)
    assert not ok and e["code"] == "invalid_arguments" and GW.posts == []


# ---- steer -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("extra", [{"model": "m"}, {"provider": "p"}, {"model_options": OPTS},
                                   {"model": "m", "provider": "p", "model_options": OPTS}])
def test_steer_rejects_model_parameters_instead_of_ignoring_them(rig, extra):
    ok, e = rig.call("chat_send", message="go left", session_id="s_1", mode="steer", **extra)
    assert not ok and e["code"] == "invalid_arguments"
    assert "steer" in e["message"] and "no new run" in e["message"]
    assert GW.posts == [] and GW.steers == []          # nothing was sent anywhere


# ---- validation ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("opts", [
    {"api_key": "sk-secret"},                          # credentials
    {"base_url": "http://evil"},                       # transport override
    {"headers": {"Authorization": "x"}},
    {"reasoning": {"effort": "low", "api_key": "k"}},  # smuggled inside the nested object
    {"reasoning": {"effort": "extreme"}},              # unknown effort
    {"reasoning": {"enabled": "yes"}},
    {"reasoning": {"enabled": False, "effort": "high"}},
    {"reasoning": {"effort": "low"}, "reasoning_effort": "high"},   # Hermes would let one silently win
    {"service_tier": "a b"},
    {"service_tier": "priority", "fast": True},
    {"fast": "true"},
    {"unknown": 1},
    {},                                                # empty: leave it out instead
])
def test_unsupported_or_contradicting_model_options_are_refused(rig, opts):
    ok, e = rig.call("chat_send", message="x", model_options=opts)
    assert not ok and e["code"] == "invalid_arguments" and GW.posts == []


def test_refusal_names_the_allowed_keys_and_never_echoes_the_value(rig):
    ok, e = rig.call("chat_send", message="x", model_options={"api_key": "sk-very-secret-value"})
    assert not ok and "api_key" in e["message"] and "sk-very-secret-value" not in json.dumps(e)
    assert all(k in e["message"] for k in chat.MODEL_OPTION_KEYS)      # the schema layer lists what is allowed
    with pytest.raises(chat.ToolError) as exc:                         # the code layer refuses the same on its own
        chat.normalize_model_options({"api_key": "sk-very-secret-value"})
    assert exc.value.code == "invalid_arguments" and "sk-very-secret-value" not in exc.value.message


@pytest.mark.parametrize("args", [
    {"model": ""}, {"model": "   "}, {"model": "a\nb"}, {"model": "x" * 201}, {"model": 5},
    {"provider": "has space"}, {"provider": "a/b"}, {"provider": "x" * 81}, {"provider": ""},
    {"model_options": "low"}, {"model_options": ["reasoning"]},
])
def test_invalid_model_and_provider_values_are_refused(rig, args):
    ok, e = rig.call("chat_send", message="x", **args)
    assert not ok and e["code"] == "invalid_arguments" and GW.posts == []


def test_normalize_returns_a_canonical_copy():
    src = {"reasoning": {"effort": "low", "enabled": True}, "fast": False}
    out = chat.normalize_model_options(src)
    assert out == src and out is not src and out["reasoning"] is not src["reasoning"]
    assert chat.normalize_model_options(None) == {}
    assert chat.model_route() == {}


# ---- idempotency -----------------------------------------------------------------------------------------------
def test_same_request_key_same_route_replays_the_original_run(rig):
    kw = dict(message="m", request_key="req-model-0001", model="m-1", provider="p", model_options=OPTS)
    ok, a = rig.call("chat_send", **kw)
    ok, b = rig.call("chat_send", **kw)
    assert ok and b["run_id"] == a["run_id"] and b["replayed"] is True and len(GW.runs) == 1


@pytest.mark.parametrize("changed", [{"model": "m-2"}, {"provider": "other"}, {"model_options": {"reasoning": {"effort": "high"}}},
                                     {"model_options": None}, {"model": None}])
def test_same_request_key_other_route_is_not_confirmed_as_a_retry(rig, changed):
    base = dict(message="m", request_key="req-model-0002", model="m-1", provider="p", model_options=OPTS)
    ok, a = rig.call("chat_send", **base)
    retry = {k: v for k, v in {**base, **changed}.items() if v is not None}
    ok, e = rig.call("chat_send", **retry)
    assert not ok and e["code"] == "request_key_conflict" and "model" in e["message"]
    assert len(GW.runs) == 1                           # the original run was not echoed back as a match


def test_adding_a_route_to_a_plain_request_key_is_a_conflict_too(rig):
    assert rig.call("chat_send", message="m", request_key="req-model-0003")[0]
    ok, e = rig.call("chat_send", message="m", request_key="req-model-0003", model="m-1")
    assert not ok and e["code"] == "request_key_conflict" and len(GW.runs) == 1


# ---- integration: the route that really served the run (runtime), not the echoed request field -----------------
def test_new_session_runtime_confirms_the_requested_route(rig):
    ok, r = rig.call("chat_send", message="x", provider="example-provider", model="example-model")
    ok, st = rig.call("chat_run_status", run_id=r["run_id"])
    assert ok
    assert st["runtime"] == {"provider": "example-provider", "model": "example-model", "route_source": "raw_request",
                             "requested": {"provider": "example-provider", "model": "example-model"}}


def test_session_binding_wins_and_the_difference_stays_visible(rig):
    GW.bound["s_bound"] = {"provider": "bound-prov", "model": "bound-model"}
    ok, r = rig.call("chat_send", message="x", session_id="s_bound", provider="example-provider", model="example-model")
    assert ok and "route_note" in r
    ok, st = rig.call("chat_run_status", run_id=r["run_id"])
    # the request-side echo claims the asked model ...
    assert st["model"] == "example-model"
    # ... runtime says what really ran, and keeps the request apart from it
    assert st["runtime"]["model"] == "bound-model" and st["runtime"]["provider"] == "bound-prov"
    assert st["runtime"]["route_source"] == "session"
    assert st["runtime"]["requested"] == {"provider": "example-provider", "model": "example-model"}
    assert st["runtime"]["model"] != st["runtime"]["requested"]["model"]


def test_run_without_route_reports_the_global_default(rig):
    ok, r = rig.call("chat_send", message="x")
    ok, st = rig.call("chat_run_status", run_id=r["run_id"])
    assert st["runtime"] == {**GLOBAL, "route_source": "global"}


def test_finished_run_returns_runtime_with_chat_send_wait(rig):
    ok, r = rig.call("chat_send", message="x", model="m-9", request_key="req-model-0004")
    GW.runs[r["run_id"]].update(status="completed", output="done")
    ok, r2 = rig.call("chat_send", message="x", model="m-9", request_key="req-model-0004", wait_seconds=2)
    assert ok and r2["status"] == "completed" and r2["runtime"]["model"] == "m-9"
    assert r2["requested_route"] == {"model": "m-9"}
