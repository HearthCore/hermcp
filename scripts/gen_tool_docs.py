#!/usr/bin/env python3
"""Render docs/tools.html — the tool catalog an MCP client actually sees.

Everything here is derived from the live registry, so the page cannot drift from the
server: run `python -m hermcp docs` (or `hermes hermcp docs`) after changing tools, or
let `serve` regenerate it at startup.

Usage: python scripts/gen_tool_docs.py [--out PATH]
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))  # allow `hermcp` imports when running from plugins/

from hermcp import __version__  # noqa: E402
from hermcp.app import build_registry  # noqa: E402
from hermcp.scopes import ACCESS_LEVELS  # noqa: E402
from hermcp.server import INSTRUCTIONS  # noqa: E402

BADGE = {
    "read": ("#1f6feb", "#0b1c2c"),
    "write": ("#d29922", "#2b2205"),
    "admin": ("#da3633", "#2d0f10"),
}


def _esc(v: object) -> str:
    return html.escape(str(v), quote=True)


def render(out: Path) -> Path:
    reg = build_registry()
    tools = sorted(reg.tools.values(), key=lambda t: (t.toolset, t.name))
    by_ts: dict[str, list] = {}
    for t in tools:
        by_ts.setdefault(t.toolset, []).append(t)

    counts = {lv: sum(1 for t in tools if t.access == lv) for lv in ACCESS_LEVELS}
    core = sum(1 for t in tools if t.core)

    parts: list[str] = []
    A = parts.append
    A("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    A("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    A("<title>hermcp — tool catalog</title><style>")
    A("""
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c232c;--line:#30363d;--fg:#e6edf3;
--dim:#8b949e;--accent:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:22px 26px 14px;border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:19px;letter-spacing:.3px}
h1 span{color:var(--accent)}
.sub{color:var(--dim);font-size:12px}
.wrap{padding:18px 26px 60px;max-width:1180px}
.grid{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 4px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;
padding:8px 12px;font-size:12px;color:var(--dim)}
.stat b{color:var(--fg);font-size:15px;display:block}
.guidance{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:12px 14px;margin:16px 0 6px;white-space:pre-wrap;font-size:13px}
input#q{width:100%;margin:18px 0 8px;padding:10px 12px;border-radius:8px;border:1px solid var(--line);
background:var(--panel);color:var(--fg);font:inherit}
input#q:focus{outline:2px solid var(--accent);outline-offset:1px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.9px;color:var(--dim);
margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h2 em{color:var(--fg);font-style:normal;text-transform:none;letter-spacing:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:12px 14px;margin:8px 0}
.card:hover{border-color:#3d454f}
.name{font-size:14px;font-weight:700;color:var(--accent);word-break:break-all}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 8px}
.b{font-size:10px;text-transform:uppercase;letter-spacing:.7px;padding:2px 7px;border-radius:999px;
border:1px solid var(--line);color:var(--dim)}
.desc{margin:0 0 8px;white-space:pre-wrap;color:#c9d1d9}
details summary{cursor:pointer;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
pre{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px;
overflow:auto;font-size:12px;margin:8px 0 0;color:#c9d1d9}
footer{color:var(--dim);font-size:11px;padding:0 26px 40px;max-width:1180px}
.hide{display:none}
""")
    A("</style></head><body>")
    A("<header><h1>herm<span>cp</span> — tool catalog</h1>")
    A(f"<div class='sub'>What an MCP client sees. Generated from the live registry (hermcp {_esc(__version__)}). "
      "Regenerate: <code>python -m hermcp docs</code></div></header>")
    A("<div class='wrap'>")
    A("<div class='grid'>")
    A(f"<div class='stat'><b>{len(tools)}</b>tools total</div>")
    A(f"<div class='stat'><b>{core}</b>advertised directly</div>")
    for lv in ("read", "write", "admin"):
        A(f"<div class='stat'><b>{counts[lv]}</b>{lv}</div>")
    A(f"<div class='stat'><b>{len(by_ts)}</b>toolsets</div>")
    A("</div>")
    A("<h2>Server guidance (the <code>instructions</code> string sent on initialize)</h2>")
    A(f"<div class='guidance'>{_esc(INSTRUCTIONS)}</div>")
    A("<h2>How the surface is split</h2>")
    A("<div class='guidance'>Core tools are advertised at <code>tools/list</code> — the model sees their"
      " names, descriptions and schemas immediately and calls them directly. Every other tool lives in"
      " the catalog: the model reaches it with <code>hermes_search_tools</code> → "
      "<code>hermes_describe_tool</code> → <code>hermes_call</code>. The same scope check runs on all"
      " three paths, so <code>hermes_call</code> cannot reach a tool the key may not use. Tools a key"
      " may not use are hidden from <code>tools/list</code> entirely.</div>")
    A("<input id='q' placeholder='Filter: name, toolset, access, description…' autocomplete='off'>")

    for ts in sorted(by_ts):
        items = by_ts[ts]
        A(f"<h2>{_esc(ts)} <em>({len(items)})</em></h2>")
        for t in items:
            fg, _bg = BADGE.get(t.access, ("#8b949e", "#161b22"))
            haystack = " ".join([t.name, ts, t.access, t.description,
                                 "core" if t.core else ""]).lower()
            badges = [f"<span class='b' style='color:{fg};border-color:{fg}'>{_esc(t.access)}</span>"]
            if t.core:
                badges.append("<span class='b' style='color:#3fb950;border-color:#3fb950'>core</span>")
            if t.profile_aware:
                badges.append("<span class='b'>profile-aware</span>")
            if t.secrets:
                badges.append("<span class='b' style='color:#d29922;border-color:#d29922'>write-only secret</span>")
            if t.requires_approvals:
                badges.append("<span class='b' style='color:#da3633;border-color:#da3633'>needs approvals scope</span>")
            if t.requires_inbox_push:
                badges.append("<span class='b' style='color:#da3633;border-color:#da3633'>needs inbox_push scope</span>")
            if t.meta:
                badges.append("<span class='b'>meta</span>")
            schema = json.dumps(t.input_schema(), indent=2, ensure_ascii=False)
            A(f"<div class='card' data-h='{_esc(haystack)}'>")
            A(f"<div class='name'>{_esc(t.name)}</div>")
            A(f"<div class='badges'>{''.join(badges)}</div>")
            A(f"<p class='desc'>{_esc(t.description)}</p>")
            A("<details><summary>input schema</summary>")
            A(f"<pre>{_esc(schema)}</pre></details></div>")

    A("<footer>hermcp keeps this page in sync with the code. If it looks stale, the registry changed "
      "without a regeneration — <code>python -m hermcp docs</code> fixes that.</footer>")
    A("</div><script>")
    A("""
const q=document.getElementById('q');
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();
document.querySelectorAll('.card').forEach(c=>{
const hit=!v||c.dataset.h.includes(v);c.classList.toggle('hide',!hit);});});
""")
    A("</script></body></html>")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


if __name__ == "__main__":
    target = ROOT / "docs" / "tools.html"
    if "--out" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--out") + 1])
    path = render(target)
    print(f"wrote {path} ({path.stat().st_size} bytes)")
