"""tools toolset: list / enable / disable tools and toolsets per platform (via `hermes tools`)."""
from __future__ import annotations

from ..adapters.common import cli_text
from ._cli import run_write
from . import reg

PLATFORM = {"type": "string", "pattern": "^[a-z0-9_-]{1,40}$", "default": "cli",
            "description": "Platform: cli, telegram, discord, api_server, ..."}
NAMES = {"type": "array", "items": {"type": "string", "pattern": "^[A-Za-z0-9_.:-]{1,100}$"}, "minItems": 1,
         "description": "Toolset names (e.g. web) or MCP tools as server:tool"}


@reg.tool("tools_list", toolset="tools", profile_aware=True, core=True,
          description=("Show all tools/toolsets and whether each is enabled for a platform (default cli; use api_server "
                       "or telegram for those surfaces). Free text."),
          properties={"platform": PLATFORM})
def tools_list(ctx, platform="cli"):
    p = None if ctx.profile == "default" else ctx.profile
    return {"platform": platform, "listing": cli_text(["tools", "list", "--platform", platform], p, 60).strip()[-15000:]}


@reg.tool("tools_summary", toolset="tools", profile_aware=True,
          description="Enabled tools per platform (`hermes tools --summary`).")
def tools_summary(ctx):
    p = None if ctx.profile == "default" else ctx.profile
    return {"summary": cli_text(["tools", "--summary"], p, 60).strip()[-15000:]}


def _toggle(name, verb, desc):
    @reg.tool(name, toolset="tools", access="write", profile_aware=True, description=desc,
              properties={"names": NAMES, "platform": PLATFORM}, required=["names"])
    def _fn(ctx, names, platform="cli"):
        return run_write(ctx, f"{verb} {', '.join(names)} on {platform}",
                         ["tools", verb, *names, "--platform", platform])
    return _fn


_toggle("tools_enable", "enable", ("Enable toolsets or MCP tools for a platform; affects which tools the agent can use in new "
                                   "sessions."))
_toggle("tools_disable", "disable", ("Disable toolsets or MCP tools for a platform; affects which tools the agent can use in new "
                                     "sessions."))
