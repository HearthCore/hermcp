"""Approvals bridge with a multiplex gateway: mode detection, profile filter, cross-profile refusal.

No real gateway and no real ~/.hermes: every socket here is a fake AF_UNIX listener in a short temp
HERMES_HOME (AF_UNIX paths are limited to ~107 bytes).
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from hermcp import bridge
from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings

MAIN = "agent:main:telegram:dm:111"
MAIN_TILDE = "agent:main~:telegram:dm:222"
P1 = "agent:p1:telegram:dm:333"
P2 = "agent:p2:telegram:dm:444"
ODD = "cli:abc123"


class FakeBridge:
    """A minimal AF_UNIX bridge: canned answers per op, and a log of every request that arrived."""

    def __init__(self, path: Path, *, pending=None, answer=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pending = list(pending or [])
        self.answer = dict(answer or {"ok": True, "resolved": 1})
        self.seen: list[dict] = []
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(self.path))
        self._srv.listen(8)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with conn:
                try:
                    req = json.loads(conn.makefile("rb").readline() or b"{}")
                except ValueError:
                    continue
                if not isinstance(req, dict):
                    continue
                self.seen.append(req)
                op = req.get("op")
                if op == "ping":
                    resp = {"ok": True, "pid": 4242}
                elif op == "list":
                    resp = {"ok": True, "pending": self.pending}
                elif op == "answer":
                    resp = dict(self.answer)
                else:
                    resp = {"ok": False, "error": f"unknown op {op!r}"}
                conn.sendall((json.dumps(resp) + "\n").encode())

    def ops(self) -> list[str]:
        return [r.get("op") for r in self.seen]

    def close(self) -> None:
        self._srv.close()
        try:
            self.path.unlink()
        except OSError:
            pass


def stale_socket(path: Path) -> Path:
    """A socket file whose owner is gone: present on disk, connection refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()
    return path


@pytest.fixture()
def mhome(monkeypatch):
    h = Path(tempfile.mkdtemp(prefix="mx", dir="/tmp"))
    for name in ("p1", "p2"):
        (h / "profiles" / name).mkdir(parents=True, exist_ok=True)
        (h / "profiles" / name / "config.yaml").write_text(f"model:\n  default: {name}\n")
    (h / "config.yaml").write_text("model:\n  default: m\n")
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.setenv("HERMCP_HERMES_BIN", str(Path(__file__).with_name("fake_hermes.py")))
    monkeypatch.setenv("FAKE_HERMES_LOG", str(h / "fake.log"))
    yield h
    shutil.rmtree(h, ignore_errors=True)


def gateway_state(home: Path, served, *, state="running") -> None:
    """What the launch home's gateway_state.json records while the multiplexer runs."""
    (home / "gateway_state.json").write_text(json.dumps(
        {"pid": os.getpid(), "gateway_state": state, "served_profiles": list(served)}))


def owner(home: Path, profile: str) -> Path:
    return bridge.socket_path(home if profile == "default" else home / "profiles" / profile)


def resolve(home: Path, profile: str):
    from hermcp.adapters.common import list_profile_names, profile_home
    return bridge.resolve(profile, own_socket=bridge.socket_path(profile_home(profile)), root_home=home,
                          known_profiles=list_profile_names())


def caller(home: Path):
    """Registry caller with profile-pinned keys (no HTTP, no key store)."""
    from hermcp.tools import load_all
    reg, st = load_all(), load_settings({"HERMES_HOME": str(home)})
    who = {
        "p1": Principal("k1", "p1-key", Scopes(access="write", profiles=["p1"], approvals=True)),
        "p2": Principal("k2", "p2-key", Scopes(access="write", profiles=["p2"], approvals=True)),
        "adm": Principal("ka", "adm-key", Scopes(access="admin", approvals=True)),
    }

    def call(key: str, tool: str, /, **args):
        return reg.invoke(tool, args, who[key], st, None)
    return call


def bound_default(home: Path):
    """Same registry, one key restricted to the default profile."""
    from hermcp.tools import load_all
    reg, st = load_all(), load_settings({"HERMES_HOME": str(home)})
    principal = Principal("kd", "default-key", Scopes(access="write", profiles=["default"], approvals=True))
    return lambda tool, **args: reg.invoke(tool, args, principal, st, None)


# ---- 1. session_key -> profile -----------------------------------------------------------------
@pytest.mark.parametrize("key,expected", [
    (MAIN, "default"), (MAIN_TILDE, "main"), (P1, "p1"), (P2, "p2"),
    ("agent:media-agent:telegram:group:-100:42", "media-agent"),
    ("agent:main:telegram", "default"),                     # no chat id, still namespaced
    ("agent:main", None),                                   # no platform slot: not a session key
    ("agent:", None), ("agent::telegram:dm:1", None),        # empty namespace
    ("agent:a~b:telegram:dm:1", None),                       # '~' is outside the profile-id alphabet
    ("agent:main~:x", "main"),                               # the marked form is attributable
    (ODD, None), ("tg:123", None), ("Agent:main:telegram:dm:1", None), ("", None), (None, None),
    (1234, None), ({"session_key": MAIN}, None),
])
def test_profile_of_session_key(key, expected):
    assert bridge.profile_of_session_key(key) == expected


def test_profile_of_session_key_matches_hermes_own_inverse():
    """The mirror must agree with Hermes' storage-side inverse for every well formed key.

    It is deliberately STRICTER: ``agent:`` and a bare ``agent:main`` (no platform slot) are not
    session keys, so this side returns None there instead of "default" — fail closed.
    """
    from hermcp.adapters.common import ensure_hermes_importable
    ensure_hermes_importable()
    hermes_state_keys = pytest.importorskip("hermes_state_keys")
    for key in (MAIN, MAIN_TILDE, P1, P2, "agent:researcher:telegram:dm:1"):
        assert bridge.profile_of_session_key(key) == hermes_state_keys.profile_from_session_key(key)


# ---- 2. mode detection -------------------------------------------------------------------------
def test_multiplex_is_detected_from_the_recorded_served_profiles(mhome):
    gateway_state(mhome, ["default", "p1", "p2"])
    fake = FakeBridge(owner(mhome, "default"))
    try:
        t = resolve(mhome, "p1")
        assert (t.mode, t.shared, t.socket, t.gateway_pid) == (bridge.MULTIPLEX, True, owner(mhome, "default"), 4242)
        assert t.served_profiles == ["default", "p1", "p2"]
        assert resolve(mhome, "default").mode == bridge.MULTIPLEX
    finally:
        fake.close()


def test_per_home_socket_wins_while_it_answers(mhome):
    gateway_state(mhome, ["default", "p1", "p2"])            # a multiplexer runs ...
    root = FakeBridge(owner(mhome, "default"))
    own = FakeBridge(owner(mhome, "p1"))                     # ... and p1 has its own live gateway
    try:
        t = resolve(mhome, "p1")
        assert (t.mode, t.shared, t.socket) == (bridge.PER_HOME, False, owner(mhome, "p1"))
    finally:
        own.close()
        root.close()


def test_per_home_mode_without_any_multiplexer(mhome):
    own = FakeBridge(owner(mhome, "p1"))
    try:
        t = resolve(mhome, "p1")
        assert (t.mode, t.shared, t.socket) == (bridge.PER_HOME, False, owner(mhome, "p1"))
        assert t.served_profiles is None
    finally:
        own.close()


def test_neither_record_nor_socket_falls_back_to_the_own_socket(mhome):
    t = resolve(mhome, "p1")
    assert (t.mode, t.shared, t.socket, t.gateway_pid) == (bridge.PER_HOME, False, owner(mhome, "p1"), None)


def test_a_dead_multiplexer_record_is_not_multiplex(mhome):
    gateway_state(mhome, ["default", "p1"])                  # stale file, nothing answers on the root socket
    t = resolve(mhome, "p1")
    assert (t.mode, t.shared) == (bridge.PER_HOME, False)


def test_a_profile_outside_served_profiles_is_not_multiplex(mhome):
    gateway_state(mhome, ["default", "p2"])
    fake = FakeBridge(owner(mhome, "default"))
    try:
        assert resolve(mhome, "p1").mode == bridge.PER_HOME
        assert resolve(mhome, "p2").mode == bridge.MULTIPLEX
    finally:
        fake.close()


def test_empty_served_profiles_is_authoritative(mhome):
    gateway_state(mhome, [])
    fake = FakeBridge(owner(mhome, "default"))
    try:
        assert resolve(mhome, "p1").mode == bridge.PER_HOME
        assert resolve(mhome, "default").mode == bridge.PER_HOME
    finally:
        fake.close()


def test_config_flag_is_the_hint_for_a_record_without_served_profiles(mhome):
    (mhome / "config.yaml").write_text("model:\n  default: m\ngateway:\n  multiplex_profiles: true\n")
    (mhome / "gateway_state.json").write_text(json.dumps({"gateway_state": "running", "platforms": {}}))
    fake = FakeBridge(owner(mhome, "default"))
    try:
        from hermcp.adapters.common import list_profile_names
        assert bridge.served_profiles(mhome) is None                     # no record -> no answer without the roster
        assert bridge.served_profiles(mhome, list_profile_names()) == ["default", "p1", "p2"]
        assert resolve(mhome, "p1").mode == bridge.MULTIPLEX
        assert resolve(mhome, "p1").served_profiles == ["default", "p1", "p2"]
    finally:
        fake.close()


def test_config_flag_off_is_not_a_multiplexer(mhome):
    (mhome / "config.yaml").write_text("model:\n  default: m\ngateway:\n  multiplex_profiles: false\n")
    fake = FakeBridge(owner(mhome, "default"))
    try:
        assert bridge.config_multiplex_hint(mhome) is False
        assert resolve(mhome, "p1").mode == bridge.PER_HOME
    finally:
        fake.close()


def test_ping_never_raises_on_junk_or_missing_paths(mhome):
    assert bridge.ping(mhome / "nope.sock") is None
    assert bridge.ping(stale_socket(owner(mhome, "p2"))) is None
    assert bridge.runtime_status(mhome) is None
    (mhome / "gateway_state.json").write_text("{ not json")
    assert bridge.runtime_status(mhome) is None
    (mhome / "gateway_state.json").write_text("[1, 2]")
    assert bridge.runtime_status(mhome) is None


# ---- 3. the profile filter on the shared socket -------------------------------------------------
@pytest.fixture()
def shared(mhome):
    """A live multiplex socket whose queue holds one approval per profile."""
    gateway_state(mhome, ["default", "p1", "p2"])
    def entry(key, rid):
        return {"session_key": key, "request_id": rid, "command": f"run {rid}", "description": "d",
                "pattern_key": "rm", "surface": "telegram", "allowed_choices": ["once", "deny"],
                "timeout_seconds": 300}
    fake = FakeBridge(owner(mhome, "default"),
                      pending=[entry(MAIN, "r-main"), entry(P1, "r-p1"), entry(P2, "r-p2"), entry(ODD, "r-odd")])
    yield fake
    fake.close()


def test_a_pinned_key_lists_only_its_own_profile_sessions(shared, mhome):
    call = caller(mhome)
    r = call("p1", "approvals_pending")
    assert r["ok"], r
    out = r["result"]
    assert out["mode"] == bridge.MULTIPLEX and out["socket"] == str(owner(mhome, "default"))
    assert [a["request_id"] for a in out["pending"]] == ["r-p1"]
    assert [a["session_key"] for a in out["pending"]] == [P1]
    assert out["count"] == 1 and out["hidden_other_profiles"] == 3
    assert P2 not in json.dumps(out) and MAIN not in json.dumps(out) and ODD not in json.dumps(out)
    r2 = call("p2", "approvals_pending")
    assert [a["request_id"] for a in r2["result"]["pending"]] == ["r-p2"]
    assert shared.ops().count("list") == 2


def test_a_default_bound_key_on_the_shared_socket_sees_only_the_default_namespace(shared, mhome):
    """The old blanket refusal is lifted ONLY because the filter narrows the shared socket to agent:main."""
    r = bound_default(mhome)("approvals_pending")
    assert r["ok"], r
    out = r["result"]
    assert out["mode"] == bridge.MULTIPLEX and out["count"] == 1
    assert [a["session_key"] for a in out["pending"]] == [MAIN]
    assert P1 not in json.dumps(out) and P2 not in json.dumps(out) and ODD not in json.dumps(out)


def test_the_block_stays_when_the_socket_is_not_a_shared_one(mhome):
    """Per-home (or unknown) mode: a default-bound key is still refused, as before."""
    r = bound_default(mhome)("approvals_pending")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert "shared by every multiplexed profile" in r["error"]["message"]


def test_bridge_status_reports_the_mode_and_the_served_profiles(shared, mhome):
    st = caller(mhome)("adm", "approvals_bridge_status")["result"]
    assert st["mode"] == bridge.MULTIPLEX and st["shared_socket"] is True and st["reachable"] is True
    assert st["socket"] == str(owner(mhome, "default")) and st["gateway_pid"] == 4242
    assert st["served_profiles"] == ["default", "p1", "p2"] and st["served_by_multiplexer"] is True
    assert "'default'" in st["filter"]                         # an unrestricted admin key answers for default
    own = FakeBridge(owner(mhome, "p1"))
    try:
        st = caller(mhome)("p1", "approvals_bridge_status")["result"]
        assert (st["mode"], st["shared_socket"], st["socket"]) == (bridge.PER_HOME, False, str(owner(mhome, "p1")))
    finally:
        own.close()


def test_bridge_status_is_not_reachable_without_a_socket(mhome):
    st = caller(mhome)("p1", "approvals_bridge_status")["result"]
    assert st["reachable"] is False and st["mode"] == bridge.PER_HOME and st["fix"]
    assert "plugins.enabled" in st["fix"]


# ---- 4. answering: cross-profile is impossible ---------------------------------------------------
def test_answer_refuses_a_foreign_session_key_before_the_bridge_is_called(shared, mhome):
    call = caller(mhome)
    r = call("p1", "approvals_answer", session_key=P2, request_id="r-p2", choice="once")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert "'p2'" in r["error"]["message"] and "'p1'" in r["error"]["message"]
    assert "answer" not in shared.ops()                       # refused locally, the socket never saw it
    r = call("p1", "approvals_answer", session_key=MAIN, request_id="r-main", choice="once")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert "answer" not in shared.ops()
    r = call("p1", "approvals_answer", session_key=ODD, request_id="r-odd", choice="once")
    assert r["ok"] is False and "cannot be attributed" in r["error"]["message"]
    assert "answer" not in shared.ops()
    r = call("p1", "approvals_answer", session_key=P1, request_id="r-p1", choice="once")
    assert r["ok"] and r["result"]["resolved"] == 1 and r["result"]["mode"] == bridge.MULTIPLEX
    assert "answer" in shared.ops()


def test_dry_run_also_refuses_a_foreign_session_key(shared, mhome):
    r = caller(mhome)("p1", "approvals_answer", session_key=P2, request_id="r-p2", choice="once", dry_run=True)
    assert r["ok"] is False and r["error"]["code"] == "forbidden"


def test_an_admin_key_with_an_explicit_profile_is_checked_too(shared, mhome):
    call = caller(mhome)
    r = call("adm", "approvals_answer", profile="p1", session_key=P2, request_id="r-p2", choice="once")
    assert r["ok"] is False and r["error"]["code"] == "forbidden"
    assert "answer" not in shared.ops()
    r = call("adm", "approvals_answer", profile="p2", session_key=P2, request_id="r-p2", choice="once")
    assert r["ok"] and r["result"]["profile"] == "p2"


def test_unattributable_keys_are_only_refused_on_the_shared_socket(mhome):
    """Per-home mode: the socket is profile-scoped, so an unattributable key is the bridge's call."""
    own = FakeBridge(owner(mhome, "p1"))
    try:
        r = caller(mhome)("p1", "approvals_answer", session_key=ODD, request_id="r", choice="once")
        assert r["ok"] and r["result"]["mode"] == bridge.PER_HOME
    finally:
        own.close()


def test_a_foreign_key_is_refused_in_per_home_mode_too(mhome):
    own = FakeBridge(owner(mhome, "p1"))
    try:
        r = caller(mhome)("p1", "approvals_answer", session_key=P2, request_id="r", choice="once")
        assert r["ok"] is False and r["error"]["code"] == "forbidden"
        assert "answer" not in own.ops()
    finally:
        own.close()


def test_the_real_bridge_server_filters_and_refuses_on_the_wire(mhome, monkeypatch):
    """Integration: the real bridge server (bridge.start) with a fake in-gateway approval module.

    Proves the refusal on the real wire format, not only against a canned fake: the gateway's own
    resolve_gateway_approval must never see a cross-profile answer.
    """
    import types
    from hermcp import bridge as real

    class FakeApproval(types.ModuleType):
        def __init__(self):
            super().__init__("tools.approval")
            import threading
            self._lock = threading.Lock()
            self._gateway_queues = {
                P1: [types.SimpleNamespace(data={"request_id": "r1", "command": "rm -rf /x", "description": "d",
                                                 "pattern_key": "rm", "allowed_choices": ["once", "deny"],
                                                 "timeout_seconds": 300})],
                P2: [types.SimpleNamespace(data={"request_id": "r2", "command": "systemctl restart y", "description": "d",
                                                 "pattern_key": "systemctl", "allowed_choices": ["once", "deny"],
                                                 "timeout_seconds": 300})],
            }
            self.resolved: list[tuple] = []

        def list_gateway_approvals(self, key):
            return [dict(e.data) for e in self._gateway_queues.get(key, [])]

        def resolve_gateway_approval(self, key, choice, resolve_all=False, reason=None, request_id=None):
            q = self._gateway_queues.get(key, [])
            hit = [e for e in q if e.data["request_id"] == request_id]
            for e in hit:
                q.remove(e)
            self.resolved.append((key, choice, request_id))
            return len(hit)

    fake = FakeApproval()
    pkg = types.ModuleType("tools")
    pkg.approval = fake
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.approval", fake)
    gateway_state(mhome, ["default", "p1", "p2"])
    path = real.start(mhome)
    assert path is not None
    try:
        call = caller(mhome)
        out = call("p1", "approvals_pending")["result"]
        assert out["mode"] == real.MULTIPLEX and [a["request_id"] for a in out["pending"]] == ["r1"]
        assert "r2" not in json.dumps(out)
        r = call("p1", "approvals_answer", session_key=P2, request_id="r2", choice="once")
        assert r["ok"] is False and r["error"]["code"] == "forbidden"
        assert fake.resolved == []                        # the gateway never saw the foreign answer
        r = call("p1", "approvals_answer", session_key=P1, request_id="r1", choice="deny")
        assert r["ok"] and r["result"]["resolved"] == 1
        assert fake.resolved == [(P1, "deny", "r1")]
    finally:
        real.stop()


# ---- 5. doctor ----------------------------------------------------------------------------------
def test_doctor_reports_one_line_for_a_multiplexer(mhome, capsys):
    from hermcp import doctor
    gateway_state(mhome, ["default", "p1", "p2"])
    fake = FakeBridge(owner(mhome, "default"))
    try:
        doctor.run()
    finally:
        fake.close()
    out = capsys.readouterr().out
    assert "[ok] approvals bridge: shared multiplex socket ok (gateway pid 4242, serves 3 profiles)" in out
    assert "not running" not in out and "stale socket" not in out


def test_doctor_warns_about_a_stale_per_profile_socket_and_never_deletes_it(mhome, capsys):
    from hermcp import doctor
    gateway_state(mhome, ["default", "p1", "p2"])
    fake = FakeBridge(owner(mhome, "default"))
    stale = stale_socket(owner(mhome, "p2"))
    try:
        doctor.run()
    finally:
        fake.close()
    out = capsys.readouterr().out
    assert "[warn] approvals bridge [p2] stale socket file" in out and "delete that file" in out
    assert stale.exists()                                     # doctor reports, never deletes


def test_doctor_stays_per_profile_without_a_multiplexer(mhome, capsys):
    from hermcp import doctor
    own = FakeBridge(owner(mhome, "p1"))
    try:
        doctor.run()
    finally:
        own.close()
    out = capsys.readouterr().out
    assert "[info] approvals bridge [default]: not running" in out
    assert "[ok] approvals bridge [p1]: gateway pid 4242" in out
    assert "shared multiplex socket" not in out


def test_doctor_reports_a_live_per_home_socket_next_to_the_multiplexer(mhome, capsys):
    from hermcp import doctor
    gateway_state(mhome, ["default", "p1", "p2"])
    root, own = FakeBridge(owner(mhome, "default")), FakeBridge(owner(mhome, "p1"))
    try:
        doctor.run()
    finally:
        own.close()
        root.close()
    out = capsys.readouterr().out
    assert "[ok] approvals bridge: shared multiplex socket ok" in out
    assert "[info] approvals bridge [p1]: its own per-home gateway socket (pid 4242)" in out


# ---- 6. inbox hook attribution ------------------------------------------------------------------
HOOK_PRE = dict(command="git push --force", description="Force-push", pattern_key="git_push",
                pattern_keys=["git_push"], session_key=P1, surface="telegram", profile=None)


def poll(home, **kw):
    from hermcp.adapters.inbox_store import get_inbox
    return get_inbox(load_settings({"HERMES_HOME": str(home)})).poll(**kw)


def events(home, **kw):
    return poll(home, **kw)["events"]


@pytest.fixture(autouse=True)
def clean_seen():
    from hermcp import approvals_hook as hook
    hook._seen.clear()
    yield
    hook._seen.clear()


def test_a_multiplexed_event_is_attributed_to_the_session_key_profile(mhome, monkeypatch):
    """The multiplex gateway runs in the default home, so the home alone would say 'default' for all."""
    from hermcp import approvals_hook as hook
    monkeypatch.setattr(hook, "_profile", lambda: "default")
    assert hook._event_profile({"session_key": P2}) == "p2"
    assert hook._event_profile({"session_key": MAIN}) == "default"
    assert hook._event_profile({"session_key": MAIN_TILDE}) == "main"
    assert hook._event_profile({"session_key": ODD}) == "default"      # unattributable -> the home
    hook.handle_pre_approval_request(**HOOK_PRE)
    assert [e["profile"] for e in events(mhome)] == ["p1"]
    assert poll(mhome, profiles=["p1"])["events"] != []
    assert poll(mhome, profiles=["default"])["events"] == []
    assert poll(mhome, profiles=["p2"])["events"] == []


def test_a_per_home_gateway_keeps_its_own_profile_even_though_the_key_says_agent_main(mhome, monkeypatch):
    """A standalone named-profile gateway builds `agent:main:…` keys: the home must win."""
    from hermcp import approvals_hook as hook
    monkeypatch.setattr(hook, "_profile", lambda: "researcher")
    assert hook._event_profile({"session_key": MAIN}) == "researcher"
    assert hook._event_profile({"session_key": ""}) == "researcher"


def test_the_profile_still_fails_closed_without_a_home_or_a_namespace(mhome, monkeypatch):
    from hermcp import approvals_hook as hook
    monkeypatch.setattr(hook, "_profile", lambda: None)
    assert hook._event_profile({"session_key": ODD}) is None
    hook.handle_pre_approval_request(**{**HOOK_PRE, "session_key": ODD})
    rows = events(mhome)
    assert rows and rows[0]["profile"] is None
    assert poll(mhome, profiles=["p1"])["events"] == []


# ---- 7. orchestration_overview counts ----------------------------------------------------------
def row(key: str, rid: str) -> dict:
    return {"session_key": key, "request_id": rid, "command": f"run {rid}", "description": "d",
            "pattern_key": "rm", "surface": "telegram", "allowed_choices": ["once"], "timeout_seconds": 60}


def overview(home: Path, key: str = "adm") -> dict:
    r = caller(home)(key, "orchestration_overview", skills=False)
    assert r["ok"], r
    return r["result"]["approvals_pending"]


def test_entries_for_profile_applies_the_session_key_namespace():
    rows = [row(MAIN, "m"), row(MAIN_TILDE, "t"), row(P1, "a"), row(P2, "b"), row(ODD, "c"), row("agent:a~b:x:y", "d")]
    assert [e["session_key"] for e in bridge.entries_for_profile(rows, "default")] == [MAIN]
    assert [e["session_key"] for e in bridge.entries_for_profile(rows, "main")] == [MAIN_TILDE]
    assert [e["session_key"] for e in bridge.entries_for_profile(rows, "p1")] == [P1]
    assert bridge.entries_for_profile(rows, "p3") == []          # unattributable and lookalike rows stay out


def test_orchestration_overview_counts_each_profile_on_the_shared_socket(mhome):
    """The overview is the last consumer that assumed one socket per home.

    It must not report the WHOLE shared queue as the default profile's count, nor call the other
    profiles "unavailable" while their approvals are one socket away.
    """
    gateway_state(mhome, ["default", "p1", "p2"])
    fake = FakeBridge(owner(mhome, "default"),
                      pending=[row(MAIN, "r-main"), row(P1, "r-p1"), row(P2, "r-p2"), row(ODD, "r-odd")])
    try:
        assert overview(mhome) == {"default": 1, "p1": 1, "p2": 1}
        assert fake.ops().count("list") == 3                     # every row was counted off the shared socket
    finally:
        fake.close()


def test_orchestration_overview_leaves_a_per_home_socket_unfiltered(mhome):
    """A live per-profile socket (separate gateway) is counted raw: no filtering, no new row semantics.

    An unattributable `cli:…` key is another profile's own socket's business there, so it counts.
    """
    gateway_state(mhome, ["default", "p1"])
    root, own = FakeBridge(owner(mhome, "default"), pending=[row(MAIN, "r-main")]), \
        FakeBridge(owner(mhome, "p1"), pending=[row(P1, "r-p1"), row(ODD, "r-odd")])
    try:
        assert overview(mhome) == {"default": 1, "p1": 2}
    finally:
        own.close()
        root.close()


def test_orchestration_overview_skips_profiles_the_gateway_does_not_serve(mhome):
    """Unchanged: a profile outside `served_profiles` gets no row at all (no socket is probed)."""
    gateway_state(mhome, ["default"])
    fake = FakeBridge(owner(mhome, "default"), pending=[row(MAIN, "r-main")])
    try:
        assert overview(mhome) == {"default": 1}
    finally:
        fake.close()


def test_orchestration_overview_still_hides_the_section_from_a_default_bound_key(shared, mhome):
    """The section stays hidden for a restricted key allowed the default profile; only the wording is new."""
    out = bound_default(mhome)("orchestration_overview", skills=False)["result"]["approvals_pending"]
    assert out.startswith("not visible:")
    assert "bound to the default profile (shared bridge)" not in out
    assert "approvals_pending" in out                            # points at the tool that does filter
