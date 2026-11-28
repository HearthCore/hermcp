#!/usr/bin/env python3
"""Render docs/tools.html — the tool catalog an MCP client actually sees.

Everything here is derived from the live registry, so the page cannot drift from the
server: run `python -m hermcp docs` (or `hermes hermcp docs`) after changing tools, or
let `serve` regenerate it at startup.

The same run also rewrites the marked catalog-count regions in README.md and
docs/tool-surface.md, which would otherwise drift the moment a tool is added.

Usage: python scripts/gen_tool_docs.py [--out PATH]
"""
from __future__ import annotations

import html
import json
import re
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


# ---- catalog counts in prose -----------------------------------------------------------------
# README.md and docs/tool-surface.md state the catalog numbers. Those are the one thing in the docs
# that drifts on its own: adding a tool changes them without anybody touching the docs. The marked
# regions here are therefore generated from the live registry, and `tests/test_doc_counts.py` fails
# when a file is stale. `python -m hermcp docs` rewrites both the page and the prose.
MARK_START = "<!-- tool-counts:%s -->"
MARK_END = "<!-- /tool-counts:%s -->"


def catalog_stats(reg=None) -> dict[str, int]:
    """The numbers the docs state, derived from `app.build_registry()`."""
    reg = reg if reg is not None else build_registry()
    tools = list(reg.tools.values())
    meta = [t for t in tools if t.meta]
    body = [t for t in tools if not t.meta]
    return {
        "total": len(tools),
        "catalog": len(body),
        "meta": len(meta),
        "toolsets": len({t.toolset for t in body}),
        "core": sum(1 for t in tools if t.core),
        "advertised": sum(1 for t in tools if t.core or t.meta),
    }


def _regions(s: dict[str, int]) -> dict[str, tuple[str, str, bool, list[str]]]:
    """region name -> (file, line prefix, inline, generated lines)."""
    return {
        "readme-status": (
            "README.md", "> ", False, [
                f"Today's catalog is **{s['total']} tools** — {s['catalog']} in {s['toolsets']} toolsets plus"
                f" {s['meta']} meta tools — of which",
                f"**{s['advertised']} are advertised in `tools/list`** (a key sees only what its scopes and"
                " opt-in flags allow);",
                "the rest is reachable through `hermes_search_tools` → `hermes_describe_tool` →"
                " `hermes_call`.",
            ]),
        "readme-features-tools": ("README.md", "", True, [f"{s['total']} tools, {s['toolsets']} toolsets"]),
        "readme-features-advertised": ("README.md", "", True, [f"{s['advertised']} advertised"]),
        "readme-surface": (
            "README.md", "", False, [
                f"**{s['total']} tools — {s['catalog']} in {s['toolsets']} toolsets plus {s['meta']} meta"
                f" tools — of which {s['advertised']} are advertised directly.**",
            ]),
        "readme-roadmap": ("README.md", "", True, [f"{s['catalog']} tools across {s['toolsets']} toolsets"]),
        "tool-surface": (
            "docs/tool-surface.md", "", False, [
                f"**{s['total']} tools in the catalog — {s['catalog']} in {s['toolsets']} toolsets plus"
                f" {s['meta']} meta tools — of which {s['advertised']} are advertised directly**"
                f" ({s['core']} core tools plus {s['meta']} meta tools; a key sees only what its scopes and"
                " opt-in flags allow).",
            ]),
    }


def sync_counts(root: Path = ROOT) -> list[Path]:
    """Rewrite every generated count region; return the files that actually changed."""
    regions = _regions(catalog_stats())
    by_file: dict[str, list[tuple[str, str, bool, list[str]]]] = {}
    for name, (rel, prefix, inline, lines) in regions.items():
        by_file.setdefault(rel, []).append((name, prefix, inline, lines))

    changed: list[Path] = []
    for rel, items in by_file.items():
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new = text
        for name, prefix, inline, lines in items:
            start, end = MARK_START % name, MARK_END % name
            body = lines[0] if inline else "\n".join(prefix + ln for ln in lines)
            repl = f"{start}{body}{end}" if inline else f"{start}\n{body}\n{prefix}{end}"
            pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
            new, n = pattern.subn(lambda _m, r=repl: r, new)
            if n != 1:
                raise ValueError(f"{rel}: expected exactly one {start} … {end} region, found {n}")
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(path)
    return changed


def _esc(v: object) -> str:
    return html.escape(str(v), quote=True)


def render_annotations(out: Path) -> Path:
    """Render docs/tool-annotations.md: every tool's class and hints, and where they disagree
    with the registry tier (the audit list). Derived from the same table `descriptor()` serves."""
    from hermcp import annotations as A

    reg = build_registry()
    tools = sorted(reg.tools.values(), key=lambda t: (t.toolset, t.name))
    parts: list[str] = []
    A_ = parts.append
    A_("# Tool annotations\n")
    A_("Generated from the live registry by `python -m hermcp docs` — do not edit by hand.\n")
    A_("Every tool carries a `title` and the four MCP `ToolAnnotations` hints "
       "(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). A client sees "
       "them at `tools/list`, and for the tools that are not advertised directly through "
       "`hermes_search_tools` / `hermes_describe_tool`.\n")
    A_("**Hints are advisory; scopes enforce.** The hints tell a client what kind of prompt to "
       "show before it calls a tool. What a key may actually call is decided by its scopes "
       "(`access`, `toolsets`, `deny_tools`, plus the explicit `approvals`, `dangerous`, "
       "`secrets_write`, `inbox_push` flags), and `tools/list` only advertises what that key may "
       "use. Never read a permission out of a hint.\n")
    A_("Class codes (the four booleans, so they cannot drift apart):\n")
    A_("| code | readOnly | destructive | idempotent | openWorld | meaning |")
    A_("|---|---|---|---|---|---|")
    meaning = {
        "R": "read-only, idempotent, own network",
        "R-o": "read-only, idempotent, reaches the internet",
        "C": "additive write (create/send/queue)",
        "C-i": "additive write, repeating it changes nothing",
        "C-o": "additive write that talks to the internet",
        "D": "destructive write (delete/overwrite/abort)",
        "D-i": "destructive write, repeating it is a no-op",
        "G": "generic dispatcher: conservative union of everything it can reach",
    }
    for code, (ro, dest, idem, ow) in A.CLASSES.items():
        A_(f"| `{code}` | {'yes' if ro else 'no'} | {'yes' if dest else 'no'} | "
           f"{'yes' if idem else 'no'} | {'yes' if ow else 'no'} | {meaning[code]} |")
    A_("")
    A_("## Where the hints disagree with the declared access\n")
    A_("The registry tags a tool `read` / `write` / `admin`; the hint says what the call really "
       "does. These are the tools where the two disagree — each one is deliberate, and each is "
       "gated by a scope (never by the annotation):\n")
    A_("| tool | declared access | class | why the hint differs |")
    A_("|---|---|---|---|")
    why = {
        "hermes_call": "access=read, but it dispatches any tool in the caller's scope, admin included",
        "inbox_push": "access=read, but it appends an event to another consumer's inbox (inbox_push scope)",
        "inbox_ack": "access=read, but it marks this consumer's inbox events read",
        "session_card_set": "access=read, but it registers or updates a session card",
        "audit_tail": "access=admin, but the call only reads the audit log",
        "keys_list": "access=admin, but the call only lists key names and scopes",
    }
    for t in tools:
        if (t.access == "read") != t.annotations.read_only:
            A_(f"| `{t.name}` | {t.access} | `{t.annotations.code}` | {why.get(t.name, '')} |")
    A_("")
    A_("## Every tool\n")
    A_("| tool | toolset | access | class | readOnly | destructive | idempotent | openWorld | title |")
    A_("|---|---|---|---|---|---|---|---|---|")
    for t in tools:
        a = t.annotations
        yes = lambda v: "yes" if v else "no"  # noqa: E731
        A_(f"| `{t.name}` | {t.toolset} | {t.access} | `{a.code}` | {yes(a.read_only)} | "
           f"{yes(a.destructive)} | {yes(a.idempotent)} | {yes(a.open_world)} | {a.title} |")
    A_("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


def render(out: Path) -> Path:
    reg = build_registry()
    stats = catalog_stats(reg)
    tools = sorted(reg.tools.values(), key=lambda t: (t.toolset, t.name))
    by_ts: dict[str, list] = {}
    for t in tools:
        by_ts.setdefault(t.toolset, []).append(t)

    counts = {lv: sum(1 for t in tools if t.access == lv) for lv in ACCESS_LEVELS}

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
    A(f"<div class='stat'><b>{stats['total']}</b>tools total</div>")
    A(f"<div class='stat'><b>{stats['advertised']}</b>advertised directly</div>")
    for lv in ("read", "write", "admin"):
        A(f"<div class='stat'><b>{counts[lv]}</b>{lv}</div>")
    A(f"<div class='stat'><b>{stats['toolsets']}</b>toolsets</div>")
    A(f"<div class='stat'><b>{stats['meta']}</b>meta tools</div>")
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
    ann = render_annotations(ROOT / "docs" / "tool-annotations.md")
    print(f"wrote {ann} ({ann.stat().st_size} bytes)")
    for changed_path in sync_counts():
        print(f"updated catalog counts in {changed_path.relative_to(ROOT.parent)}")
