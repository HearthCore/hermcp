"""`hermes hermcp …` / `python -m hermcp …`: serve, keys, doctor."""
from __future__ import annotations

import argparse
import json
import sys
import time

from .keys import KeyError_, KeyStore, parse_expiry
from .scopes import ScopeError, Scopes
from .settings import load_settings, ConfigError


def _csv(v: str | None, default: list[str]) -> list[str]:
    return [p.strip() for p in v.split(",") if p.strip()] if v else list(default)


def setup_parser(p: argparse.ArgumentParser) -> None:
    sub = p.add_subparsers(dest="hermcp_cmd")
    s = sub.add_parser("serve", help="run the MCP server (default 127.0.0.1:9230)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--log-level", default="info")
    sub.add_parser("doctor", help="check the installation")
    stp = sub.add_parser("selftest", help="compare hermcp answers with fresh-process references (read-only)")
    stp.add_argument("--profiles", help="comma list (default: all)")
    stp.add_argument("--json", action="store_true", help="machine-readable rows")
    stp.add_argument("--skip-slow", action="store_true", help="skip checks that run slow hermes CLI commands")
    sub.add_parser("status", help="who owns the server: instance lock, port, systemd unit")
    st = sub.add_parser("stop", help="stop the instance that holds the lock (asks systemd otherwise)")
    st.add_argument("--force", action="store_true", help="signal a systemd-managed pid directly")
    ins = sub.add_parser("install-service", help="install the systemd unit (does not start it)")
    ins.add_argument("--enable-now", action="store_true", help="also enable + start it")
    ins.add_argument("--out", default="/etc/systemd/system/hermcp.service")
    ins.add_argument("--no-auto-restart", action="store_true",
                     help="do not install the units that restart hermcp after a Hermes update")
    ins.add_argument("--dry-run", action="store_true", help="print what would be written, change nothing")
    d = sub.add_parser("docs", help="regenerate docs/tools.html from the live registry")
    d.add_argument("--out", help="output path (default: docs/tools.html next to the package)")
    o = sub.add_parser("oauth", help="OAuth client for Claude.ai connectors").add_subparsers(dest="oauth_cmd")
    oi = o.add_parser("init", help="create the static client_id/client_secret (secret shown once)")
    oi.add_argument("--client-id", default="hermcp-claude")
    oi.add_argument("--rotate", action="store_true", help="new secret; invalidates all issued tokens")
    o.add_parser("status", help="client + token counts")
    o.add_parser("revoke-all", help="invalidate every issued access/refresh token")
    k = sub.add_parser("keys", help="manage API keys").add_subparsers(dest="keys_cmd")
    c = k.add_parser("create", help="create a key (token is shown once)")
    c.add_argument("--name", required=True)
    c.add_argument("--owner", default="")
    c.add_argument("--access", default="read", choices=["read", "write", "admin"])
    c.add_argument("--toolsets", help="comma list or * (default *)")
    c.add_argument("--profiles", help="comma list or * (default *)")
    c.add_argument("--profile", help="bind the key to ONE profile (read or write inside that profile only; no admin, "
                                     "no dangerous/inbox_push). See `keys policy` for what such a key may call")
    c.add_argument("--deny-tools", help="comma list of tool names")
    c.add_argument("--approvals", action="store_true", help="allow the approvals toolset")
    c.add_argument("--secrets-write", action="store_true", help="allow write-only secret tools")
    c.add_argument("--allow-dangerous", action="store_true",
                   help="allow exfiltration/irreversible tools (memory reset, profile delete, …)")
    c.add_argument("--inbox-push", action="store_true", help="allow delivering events into the inbox (inbox_push, POST /inbox)")
    c.add_argument("--approvals-always", action="store_true",
                   help="allow answering an approval with `always` (permanent rule rewrite; needs access=admin)")
    c.add_argument("--expires", help="30d / 12h / ISO date")
    k.add_parser("list", help="list keys")
    k.add_parser("policy", help="table of what a profile-bound key may call (no key is created)")
    for name, h in (("show", "show one key"), ("revoke", "disable a key"), ("rotate", "issue a new secret")):
        x = k.add_parser(name, help=h)
        x.add_argument("key", help="key id or name")
        if name == "revoke":
            x.add_argument("--delete", action="store_true", help="remove instead of disabling")

    pk = sub.add_parser("profile-keys", help="per-profile api_server keys: status, setup, rotate")
    pks = pk.add_subparsers(dest="profile_keys_cmd")
    ps = pks.add_parser("status", help="which key each profile resolves and whether the api_server accepts it")
    ps.add_argument("--profiles", help="comma list (default: every profile)")
    ps.add_argument("--no-probe", action="store_true", help="skip the live probe (offline)")
    ps.add_argument("--json", action="store_true", help="machine-readable output")
    pu = pks.add_parser("setup", help="give profiles their own key (interactive; writes profile .env files)")
    pu.add_argument("--profiles", help="comma list (default: every profile missing a key or sharing one)")
    pu.add_argument("--yes", action="store_true", help="non-interactive: apply --generate/--copy-from to all targets")
    pu.add_argument("--generate", action="store_true", help="generate a new key per target (with --yes)")
    pu.add_argument("--copy-from", help="copy the key of `global` or another profile")
    pu.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    pu.add_argument("--no-probe", action="store_true", help="skip the live probe")
    pt = pks.add_parser("rotate", help="replace a profile's api_server key (new key, verify by probe)")
    pt.add_argument("profile", nargs="?", help="profile name")
    pt.add_argument("--all", action="store_true", help="rotate every known profile")
    pt.add_argument("--yes", action="store_true", help="required to write (a timestamped backup is written first)")
    pt.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    pt.add_argument("--no-probe", action="store_true", help="skip the live probe")


def _fmt_ts(t) -> str:
    return "-" if not t else time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def _docs(args) -> int:
    from .server import INSTRUCTIONS  # noqa: F401  (import proves the module loads)
    from pathlib import Path
    import importlib.util
    script = Path(__file__).resolve().parent / "scripts" / "gen_tool_docs.py"
    spec = importlib.util.spec_from_file_location("hermcp_gen_tool_docs", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    target = Path(args.out) if args.out else Path(__file__).resolve().parent / "docs" / "tools.html"
    print(f"wrote {mod.render(target)}")
    return 0


def _keys(args, ks: KeyStore) -> int:
    cmd = args.keys_cmd
    if cmd == "policy":
        from . import pinning
        from .tools import load_all
        from .registry import install_meta_tools
        reg = load_all()
        if "hermes_search_tools" not in reg.tools:
            install_meta_tools(reg)
        print(f"{'tool':<26}{'access':<7}{'bound key':<18}why")
        for r in pinning.table(reg.tools.values()):
            print(f"{r['tool']:<26}{r['access']:<7}{r['bound_key']:<18}{r['why']}")
        return 0
    if cmd == "create":
        if args.profile:
            if args.profiles:
                print("error: use either --profile or --profiles", file=sys.stderr)
                return 1
            if args.access == "admin" or args.allow_dangerous or args.inbox_push:
                print("error: a key bound to one profile is read or write only (no admin, --allow-dangerous or --inbox-push)",
                      file=sys.stderr)
                return 1
            from .adapters.common import list_profile_names
            if args.profile not in list_profile_names():
                print(f"error: profile {args.profile!r} does not exist (known: {', '.join(list_profile_names())})",
                      file=sys.stderr)
                return 1
            args.profiles = args.profile
        sc = Scopes(access=args.access, toolsets=_csv(args.toolsets, ["*"]), profiles=_csv(args.profiles, ["*"]),
                    deny_tools=_csv(args.deny_tools, []), approvals=args.approvals,
                    secrets_write=args.secrets_write, dangerous=args.allow_dangerous,
                    inbox_push=args.inbox_push, approvals_always=args.approvals_always)
        rec, token = ks.create(args.name, sc, owner=args.owner, expires=parse_expiry(args.expires))
        print(f"created key {rec.id} ({rec.name})  scopes={json.dumps(rec.scopes.to_dict())}")
        print(f"TOKEN (shown once): {token}")
    elif cmd == "list":
        for r in ks.list():
            sc = r.scopes
            flag = "DISABLED" if r.disabled else ("EXPIRED" if r.expired() else "active")
            print(f"{r.id}  {r.name:<20} {sc.access:<5} ts={','.join(sc.toolsets)} profiles={','.join(sc.profiles)} "
                  f"last_used={_fmt_ts(r.last_used)} {flag}")
    elif cmd == "show":
        print(json.dumps(ks.get(args.key).public(), indent=2))
    elif cmd == "revoke":
        r = ks.revoke(args.key, delete=args.delete)
        print(f"{'deleted' if args.delete else 'revoked'} {r.id} ({r.name})")
    elif cmd == "rotate":
        r, token = ks.rotate(args.key)
        print(f"rotated {r.id} ({r.name})\nTOKEN (shown once): {token}")
    else:
        print("usage: keys {create,list,show,revoke,rotate,policy}", file=sys.stderr)
        return 2
    return 0


def _oauth(args) -> int:
    from . import oauth
    s = load_settings()
    cmd = args.oauth_cmd
    if cmd == "init":
        made = oauth.init_client(s.data, args.client_id, rotate=args.rotate)
        if made is None:
            print("a client already exists; use --rotate to replace it (invalidates all tokens)", file=sys.stderr)
            return 1
        cid, secret = made
        print(f"client_id:     {cid}\nclient_secret: {secret}   (shown once; only a salted hash is stored)")
        print(f"server URL:    {s.public_url or '<set HERMCP_PUBLIC_URL, e.g. https://host/mcp>'}")
        print("restart hermcp so it serves the OAuth endpoints")
    elif cmd == "status":
        c = oauth.load_client(s.data)
        print(f"client:      {c['client_id'] if c else 'not initialised (hermes hermcp oauth init)'}")
        print(f"public url:  {s.public_url or 'UNSET (HERMCP_PUBLIC_URL needed)'}")
        print(f"redirects:   {', '.join(s.oauth_redirect_uris)}")
        if c and s.public_origin:
            p = oauth.HermcpOAuthProvider(s, KeyStore(s.data / 'keys.json'))
            print(f"tokens:      {p.stats()}")
    elif cmd == "revoke-all":
        p = oauth.HermcpOAuthProvider(s, KeyStore(s.data / "keys.json"))
        print(f"revoked {p.revoke_all()} token(s)")
    else:
        print("usage: oauth {init,status,revoke-all}", file=sys.stderr)
        return 2
    return 0


def _serve(args) -> int:
    import uvicorn
    from .app import create_app
    from .lock import EX_TEMPFAIL, InstanceLock, busy_report
    s = load_settings()
    host = args.host or s.host
    port = args.port or s.port
    lock = InstanceLock(s.data, host, port)
    try:
        meta = lock.acquire()
    except BlockingIOError:
        info = busy_report(s.data, host, port)
        hold = info["holder"]
        print("hermcp: refusing to start a second instance on "
              f"{host}:{port}", file=sys.stderr)
        if hold.get("pid"):
            started = hold.get("started")
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "?"
            print(f"  running: pid {hold['pid']}, role {hold.get('role', '?')}, started {when}",
                  file=sys.stderr)
        print(f"  lock:    {info['lock']}", file=sys.stderr)
        print(f"  port:    bound={info['port']['bound']} healthy={info['port']['healthy']}",
              file=sys.stderr)
        print(f"  next:    {info['next_step']}", file=sys.stderr)
        return EX_TEMPFAIL
    print(f"hermcp: instance lock held (pid {meta['pid']}, role {meta['role']}), "
          f"serving on {host}:{port}")
    # Preflight: the lock is ours, but the port may be held by a foreign/older process that
    # predates the lock file. Bind failure would exit 1 and make systemd flap; convert it into
    # the documented EX_TEMPFAIL (75) so RestartPreventExitStatus keeps the unit parked.
    from .lock import port_owner
    probe = port_owner(host, port)
    if probe["bound"]:
        lock.release()
        print(f"hermcp: port {host}:{port} is already bound by another process "
              f"(healthy={probe['healthy']})", file=sys.stderr)
        print("  this is not ours — inspect it, then free the port:", file=sys.stderr)
        print(f"  ss -tlnp | grep {port}", file=sys.stderr)
        print("  hermes hermcp status", file=sys.stderr)
        return EX_TEMPFAIL
    # Keep docs/tools.html in step with the registry on every start. Never fatal.
    try:
        _docs(argparse.Namespace(out=None))
    except Exception as exc:  # pragma: no cover - docs must never block serving
        print(f"note: could not refresh docs/tools.html: {exc}", file=sys.stderr)
    if args.host:
        s.host = args.host
    if args.port:
        s.port = args.port
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding {s.host} — expose only behind TLS/reverse proxy", file=sys.stderr)
    try:
        uvicorn.run(create_app(s).app, host=s.host, port=s.port, log_level=args.log_level,
                    proxy_headers=s.trust_proxy,
                    forwarded_allow_ips="127.0.0.1" if s.trust_proxy else None)
    finally:
        lock.release()
    return 0


def _status(args) -> int:
    from .lock import InstanceLock, port_owner
    import shutil
    import subprocess as sp
    s = load_settings()
    hold = InstanceLock.holder(s.data)
    owner = port_owner(s.host, s.port)
    unit = "hermcp.service"
    unit_state = "not installed"
    if shutil.which("systemctl"):
        cp = sp.run(["systemctl", "is-active", unit], capture_output=True, text=True)
        state = (cp.stdout or cp.stderr).strip()
        en = sp.run(["systemctl", "is-enabled", unit], capture_output=True, text=True)
        if "could not be found" not in (state + en.stdout + en.stderr):
            unit_state = f"{state} / {(en.stdout or en.stderr).strip()}"
    print("hermcp status")
    print(f"  bind:      {s.host}:{s.port}")
    print(f"  port:      bound={owner['bound']} healthy={owner['healthy']}")
    if hold:
        started = hold.get("started")
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "?"
        print(f"  instance:  pid {hold.get('pid')} role={hold.get('role')} started={when}"
              f"{' STALE' if hold.get('stale') else ''}")
        print(f"  argv:      {hold.get('argv', '?')}")
    else:
        print("  instance:  none (no lock holder)")
    print(f"  lock file: {InstanceLock.holder.__module__ and s.data / 'serve.lock'}")
    print(f"  systemd:   {unit} = {unit_state}")
    if hold and hold.get("role") == "systemd":
        print("  verdict:   systemd owns the server — manage it with systemctl, do not start a second one")
    elif hold and hold.get("role") == "standalone":
        print("  verdict:   a standalone instance owns the server. To move to systemd:")
        print("             hermes hermcp stop && sudo systemctl enable --now hermcp")
    elif owner["bound"] and not hold:
        print("  verdict:   port is bound but no lock metadata — a foreign/older process, inspect it")
    elif not owner["bound"]:
        print("  verdict:   nothing running; `hermes hermcp serve` or `sudo systemctl start hermcp`")
    return 0


def _install_service(args) -> int:
    """Install the systemd unit. Refuses to orphan a running standalone instance."""
    import shutil
    import subprocess as sp
    from pathlib import Path
    from .lock import InstanceLock
    s = load_settings()
    pkg = Path(__file__).resolve().parent
    src = pkg / "deploy" / "hermcp.service"
    if not src.exists():
        src = pkg / "deploy" / "hermcp.service.template"
    if not src.exists():
        print(f"template missing: {src}", file=sys.stderr)
        return 1
    from .restart_units import install_restart_units, plan_restart_units
    dest = Path(args.out)
    if args.dry_run:
        print(f"[dry-run] would write {dest}")
        if args.no_auto_restart:
            print("[dry-run] auto-restart: disabled by --no-auto-restart")
        else:
            units, msgs = plan_restart_units()
            for m in msgs:
                print(f"[dry-run] {m}")
            for name, utext in units.items():
                print(f"[dry-run] would write {dest.parent / name}:\n{utext}")
        return 0
    hold = InstanceLock.holder(s.data)
    if args.enable_now and hold and hold.get("role") == "standalone":
        print("Refusing to enable-now: a standalone instance is running "
              f"(pid {hold.get('pid')}).", file=sys.stderr)
        print("Do this instead:", file=sys.stderr)
        print("  hermes hermcp stop          # end the hand-started instance", file=sys.stderr)
        print(f"  sudo systemctl enable --now {dest.stem}", file=sys.stderr)
        print("(a systemd-managed pid is fine: use `sudo systemctl restart hermcp`)",
              file=sys.stderr)
        return 2
    text = src.read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    print(f"installed {dest}")
    restart_path = dest.parent / "hermcp-restart.path"
    if args.no_auto_restart:
        print("auto-restart: disabled by --no-auto-restart")
    else:
        for m in install_restart_units(dest.parent)[1]:
            print(m)
    with_restart = not args.no_auto_restart and restart_path.exists()
    if not shutil.which("systemctl"):
        print("systemctl not found — unit written but not registered", file=sys.stderr)
        return 1
    sp.run(["systemctl", "daemon-reload"], check=False)
    print("daemon-reload done")
    if args.enable_now:
        cp = sp.run(["systemctl", "enable", "--now", dest.stem], capture_output=True, text=True)
        print((cp.stdout or "") + (cp.stderr or ""))
        rc = cp.returncode
        if rc == 0 and with_restart:
            cp = sp.run(["systemctl", "enable", "--now", restart_path.name], capture_output=True, text=True)
            print((cp.stdout or "") + (cp.stderr or ""))
            rc = cp.returncode
        return rc
    print(f"next: sudo systemctl enable --now {dest.stem}")
    if with_restart:
        print(f"      sudo systemctl enable --now {restart_path.name}   # restart hermcp after Hermes updates")
    return 0


def _stop(args) -> int:
    from .lock import InstanceLock
    s = load_settings()
    hold = InstanceLock.holder(s.data)
    if not hold or not hold.get("pid"):
        print("no instance lock holder — nothing to stop")
        return 0
    if hold.get("stale"):
        print(f"lock is stale (pid {hold['pid']} is gone); it will be reused on next start")
        return 0
    if hold.get("role") == "systemd" and not args.force:
        print(f"refusing: pid {hold['pid']} is managed by systemd. Use:")
        print("  sudo systemctl stop hermcp")
        print("(or --force to signal it anyway, which bypasses systemd restart policy)")
        return 2
    import os
    import signal
    pid = int(hold["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"pid {pid} already gone")
        return 0
    for _ in range(50):
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print(f"stopped pid {pid} (role {hold.get('role')})")
            return 0
    print(f"pid {pid} still alive after 10s — send SIGKILL manually if appropriate", file=sys.stderr)
    return 1


def dispatch(args) -> int:
    cmd = getattr(args, "hermcp_cmd", None)
    try:
        if cmd == "serve":
            return _serve(args)
        if cmd == "keys":
            return _keys(args, KeyStore(load_settings().data / "keys.json"))
        if cmd == "oauth":
            return _oauth(args)
        if cmd == "profile-keys":
            from .profile_keys import cli_profile_keys
            return cli_profile_keys(args)
        if cmd == "doctor":
            from .doctor import run
            return run()
        if cmd == "selftest":
            from .selftest import run as selftest_run
            return selftest_run(_csv(args.profiles, []) or None, args.json, args.skip_slow)
        if cmd == "docs":
            return _docs(args)
        if cmd == "status":
            return _status(args)
        if cmd == "stop":
            return _stop(args)
        if cmd == "install-service":
            return _install_service(args)
    except (KeyError_, ScopeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"error: invalid configuration: {exc}\nrun `hermes hermcp doctor` for the full check", file=sys.stderr)
        return 1
    print("usage: hermes hermcp {serve,keys,oauth,profile-keys,doctor,selftest,docs,status,stop,install-service}",
          file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hermcp")
    setup_parser(p)
    return dispatch(p.parse_args(argv))
