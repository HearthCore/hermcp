"""Read-only view of what the gateway is running right now (who the `active_agents` are).

`gateway_state.json` carries only the NUMBER `active_agents`. The names live in the gateway process; Hermes exposes
them on its control socket (`<HERMES_HOME>/gateway.sock`) with the regular verb `active-work`, the same call
`hermes gateway restart` uses to refuse while sessions are live. Wire format (gateway/control_socket.py): one JSON
line in, one JSON line out, then the server closes; no token, the 0600 socket file is the access boundary.

    request : {"verb": "active-work", "id": 1, "protocol": 1[, "params": {"profile": "<name>"}]}\\n
    response: {"ok": true, "protocol": 1, "result": {"active_agents": N, "active_work": [...], "pid": P}}\\n

Every entry has `kind` (chat | cron | delegation | managed | api | deferred | side_agent); chat entries add
`session` (key `agent:<profile>:<platform>:<chat_type>:<id>`), `pid`, `elapsed_s`, `model`, `current_tool`, `idle_s`;
cron entries `job_id`, `elapsed_s`, `pid`, `external`, `wedged`, `restart_safe`; delegation entries a `goal` (cut to
80 chars by Hermes). managed / api / deferred carry only kind and pid.

Nothing here writes, and nothing raises: no socket, a socket of another user, a busy gateway or a malformed
answer all yield None, and the caller falls back to the plain count from the state file. Session keys contain chat
identities, so they are never returned: callers get `source` (platform) and a short `session_ref` hash instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
from pathlib import Path
from typing import Any

from ..settings import hermes_home

PROTOCOL = 1
SOCKET_NAME = "gateway.sock"
TIMEOUT_S = 2.0              # same as Hermes' own client
MAX_RESPONSE = 512 * 1024    # the server caps its answer at 512 KiB
TEXT_CAP = 80

KINDS = ("chat", "cron", "delegation", "managed", "api", "deferred", "side_agent")


def socket_path(home: Path | None = None) -> Path:
    return (home or hermes_home()) / SOCKET_NAME


def _usable_socket(path: Path) -> bool:
    """A real unix socket owned by us. A symlink or a foreign socket is not talked to."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid()


def _read_line(sock: socket.socket) -> bytes:
    buf = b""
    while len(buf) <= MAX_RESPONSE:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    return buf.split(b"\n", 1)[0]


def query(profile: str | None = None, home: Path | None = None, timeout: float = TIMEOUT_S) -> dict[str, Any] | None:
    """The `result` of the `active-work` verb ({"active_agents", "active_work", "pid"[, "profile"]}) or None."""
    path = socket_path(home)
    if not _usable_socket(path):
        return None
    payload: dict[str, Any] = {"verb": "active-work", "id": 1, "protocol": PROTOCOL}
    if profile:
        payload["params"] = {"profile": profile}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            raw = _read_line(sock)
        resp = json.loads(raw.decode("utf-8")) if raw else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    result = resp.get("result") if isinstance(resp, dict) and resp.get("ok") is True else None
    if not isinstance(result, dict) or not isinstance(result.get("active_work"), list):
        return None
    return result


# -- shaping -----------------------------------------------------------------------------------------------
def parse_session_key(key: Any) -> dict[str, str | None]:
    """`agent:<profile>:<platform>:<chat_type>:<id>` -> profile / platform / run_id. Nothing else leaves this function.

    `agent:main:` is the default profile; a profile literally named "main" is written `agent:main~:`."""
    out: dict[str, str | None] = {"profile": None, "source": None, "run_id": None, "ref": None}
    if not isinstance(key, str) or not key.startswith("agent:"):
        return out
    parts = key.split(":")
    if len(parts) >= 2 and parts[1]:
        prof = parts[1]
        out["profile"] = "default" if prof == "main" else ("main" if prof == "main~" else prof)
    if len(parts) >= 3 and parts[2]:
        out["source"] = parts[2][:40]
    tail = parts[-1] if len(parts) >= 5 else ""
    if tail.startswith("run_") and len(tail) <= 80:
        out["run_id"] = tail                      # api_server runs are keyed by their (public) run id
    out["ref"] = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return out


def _num(v: Any) -> float | None:
    return round(float(v), 1) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v: Any) -> str | None:
    return v[:TEXT_CAP] if isinstance(v, str) and v else None


def shape(entry: Any) -> dict[str, Any]:
    """One agent row for MCP clients: no session key, no free text beyond short, capped labels."""
    e = entry if isinstance(entry, dict) else {}
    kind = e.get("kind") if e.get("kind") in KINDS else "other"
    row: dict[str, Any] = {"kind": kind}
    if kind == "chat" or "session" in e:
        s = parse_session_key(e.get("session"))
        row.update(profile=s["profile"], source=s["source"], session_ref=s["ref"])
        if s["run_id"]:
            row["run_id"] = s["run_id"]
    if kind == "cron":
        row["job_id"] = _text(e.get("job_id"))
        row["source"] = "cron"
    if kind == "delegation":
        row["delegation_id"] = _text(e.get("delegation_id"))
        row["goal"] = _text(e.get("goal"))
    for key in ("pid",):
        if isinstance(e.get(key), int) and not isinstance(e.get(key), bool):
            row[key] = e[key]
    for key in ("elapsed_s", "idle_s"):
        if _num(e.get(key)) is not None:
            row[key] = _num(e[key])
    for key in ("model", "current_tool"):
        if _text(e.get(key)):
            row[key] = _text(e[key])
    for key in ("wedged", "restart_safe", "external"):
        if isinstance(e.get(key), bool):
            row[key] = e[key]
    return row


def snapshot(allowed_profiles: set[str] | None = None, profile: str | None = None, home: Path | None = None,
             timeout: float = TIMEOUT_S) -> dict[str, Any]:
    """The full answer of `agents_live` (also the restart preview).

    allowed_profiles: a profile-restricted key sees only rows it can attribute to one of its profiles; the rest is
    counted in `hidden`. profile: ask the gateway for one profile's work only."""
    res = query(profile=profile, home=home, timeout=timeout)
    if res is None:
        return {"available": False, "source": "none", "complete": False, "count": None, "agents": [],
                "reason": "gateway control socket not reachable (no gateway.sock, foreign owner, busy gateway or an "
                          "older Hermes); only the number `active_agents` in hermes_health is known"}
    rows = [shape(e) for e in res["active_work"]]
    total = res.get("active_agents") if isinstance(res.get("active_agents"), int) else len(rows)
    hidden = 0
    if allowed_profiles is not None:
        kept = [r for r in rows if r.get("profile") in allowed_profiles]
        hidden = len(rows) - len(kept)
        rows, total = kept, len(kept)
    by_kind: dict[str, int] = {}
    by_profile: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        if r.get("profile"):
            by_profile[r["profile"]] = by_profile.get(r["profile"], 0) + 1
    unlisted = max(0, total - len(rows)) if allowed_profiles is None else 0
    out: dict[str, Any] = {
        "available": True, "source": "gateway-control-socket",
        "complete": unlisted == 0 and hidden == 0,
        "gateway_pid": res.get("pid"), "active_agents": total, "count": len(rows),
        "by_kind": by_kind, "by_profile": by_profile, "agents": rows,
        "unlisted": unlisted,
        "note": ("`idle_s` is the time since the agent last reported progress; a high value on a running agent means "
                 "no progress signal, not necessarily a problem. Entries of kind api / managed / deferred carry no "
                 "session detail from the gateway."),
    }
    if allowed_profiles is not None:
        out["scoped_to"] = sorted(allowed_profiles)
        out["hidden"] = hidden
    if profile:
        out["profile"] = profile
    return out


def restart_preview(allowed_profiles: set[str] | None = None, home: Path | None = None) -> dict[str, Any]:
    """Compact block for the plan of a restart: who would be cut off, and whether it is knowable at all."""
    snap = snapshot(allowed_profiles=allowed_profiles, home=home)
    if not snap["available"]:
        return {"available": False, "reason": snap["reason"]}
    agents = snap["agents"]
    unsafe = [r for r in agents if r.get("restart_safe") is not True]
    return {"available": True, "active_agents": snap["active_agents"], "listed": len(agents),
            "unlisted": snap["unlisted"], "by_kind": snap["by_kind"], "by_profile": snap["by_profile"],
            "agents": agents,
            "would_interrupt": len(unsafe) + snap["unlisted"],
            "restart_safe": len(agents) - len(unsafe)}


IDLE_FLAG_S = 300     # `idle_5m` in hermes_status: an agent silent for this long deserves a look (not an error by itself)


def by_run_id(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Rows of an `agents_live` snapshot keyed by API run_id (only rows that carry one)."""
    return {r["run_id"]: r for r in (snap.get("agents") or []) if r.get("run_id")}


def health_digest(snap: dict[str, Any]) -> dict[str, Any]:
    """Counts for hermes_status from one snapshot: who runs, what kind, who has been silent for a while."""
    if not snap.get("available"):
        return {"available": False}
    rows = snap.get("agents") or []
    idle = [r for r in rows if isinstance(r.get("idle_s"), (int, float)) and r["idle_s"] >= IDLE_FLAG_S]
    wedged = [r for r in rows if r.get("wedged") is True]
    out: dict[str, Any] = {"available": True, "agents": snap.get("active_agents"), "by_kind": snap.get("by_kind") or {}}
    if snap.get("unlisted"):
        out["no_detail"] = snap["unlisted"]
    if idle:
        out["idle_5m"] = len(idle)
    if wedged:
        out["wedged"] = len(wedged)
    return out
