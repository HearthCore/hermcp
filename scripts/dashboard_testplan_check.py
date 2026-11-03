#!/usr/bin/env python3
"""Run the read/dry-run steps of docs/dashboard-control-testplan.md against any hermcp URL.

Never restarts anything: dashboard_restart is only called with dry_run=true or without confirm. The token comes from a
file or the HERMCP_TOKEN environment variable and is never printed.

  python scripts/dashboard_testplan_check.py --url http://127.0.0.1:9231/mcp --token-file /path/tok \\
      [--expect-mode systemd --expect-stage known_unit --expect-unit hermes-dashboard.service --expect-port 9119]
  Exit code 0 = every step passed, 1 = a step failed (the run stops at the first abort criterion).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

FAILED = False


def call(url: str, token: str, tool: str, args: dict | None = None) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=90) as r:
        msg = json.loads(r.read())
    text = msg["result"]["content"][0]["text"]
    return json.loads(text)


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILED
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILED = True
        print("ABORT: abort criterion hit, see docs/dashboard-control-testplan.md", file=sys.stderr)
        raise SystemExit(1)


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-file")
    ap.add_argument("--expect-mode", default="systemd")
    ap.add_argument("--expect-stage", default="known_unit")
    ap.add_argument("--expect-unit", default="hermes-dashboard.service")
    ap.add_argument("--expect-port", type=int, default=9119)
    ap.add_argument("--expect-command", default="systemctl restart hermes-dashboard.service",
                    help="substring expected in plan.commands[0]")
    ap.add_argument("--gateway-state", default=os.path.expanduser("~/.hermes/gateway_state.json"))
    a = ap.parse_args()
    token = os.environ.get("HERMCP_TOKEN") or (open(a.token_file).read().strip() if a.token_file else "")
    if not token:
        print("no token (HERMCP_TOKEN or --token-file)", file=sys.stderr)
        return 2

    def gw_pid():
        try:
            return json.load(open(a.gateway_state)).get("pid")
        except (OSError, ValueError):
            return None

    gw0 = gw_pid()
    # 1 status
    st1 = call(a.url, token, "dashboard_status")
    check("1 dashboard_status ok", st1.get("ok") is True, str(st1.get("error", ""))[:120])
    s = st1["result"]
    order = [x["stage"] for x in s["stages"]]
    check("1 stages in order", order == ["config", "hermes", "known_unit", "custom_unit", "process"], ",".join(order))
    check("1 mode/stage", (s["mode"], s["stage"]) == (a.expect_mode, a.expect_stage), f"{s['mode']}/{s['stage']}")
    if a.expect_mode == "systemd":
        check("1 unit", s["unit"]["unit"] == a.expect_unit, f"{s['unit']['unit']} ({s['unit']['scope']})")
    check("1 port", s["port"] == a.expect_port, str(s["port"]))
    check("1 http / is 200 or 302", s["http"].get("/") in (200, 302), str(s["http"].get("/")))
    check("1 http /api/status is 200", s["http"].get("/api/status") == 200, str(s["http"].get("/api/status")))
    check("1 _meta present", "_meta" in s)
    pid0 = s["pid"]
    check("1 dashboard pid alive", alive(pid0), str(pid0))
    # 2 dry_run
    r = call(a.url, token, "dashboard_restart", {"dry_run": True, "confirm": True})["result"]
    check("2 dry_run applied=false", r["applied"] is False and r["dry_run"] is True)
    cmd = (r["plan"]["commands"] or [""])[0]
    check("2 plan command", a.expect_command in cmd, cmd)
    check("2 side effects listed", bool(r["plan"]["side_effects"]))
    # 3 without confirm
    r = call(a.url, token, "dashboard_restart")["result"]
    check("3 no confirm -> refused", r["applied"] is False and r.get("needed") == "confirm=true")
    # 4 nothing changed
    st2 = call(a.url, token, "dashboard_status")["result"]
    check("4 dashboard pid unchanged", st2["pid"] == pid0, f"{pid0} -> {st2['pid']}")
    if gw0 is not None:
        check("4 gateway pid unchanged and alive", gw_pid() == gw0 and alive(gw0), str(gw0))
    print("ALL PASS (no restart was performed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
