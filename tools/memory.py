"""memory toolset: provider status/switch, learned-node listing. `memory reset` is dangerous-only."""
from __future__ import annotations

from ..adapters.common import cli_text
from . import reg
from ._cli import run_write


@reg.tool("memory_status", toolset="memory", profile_aware=True, core=True,
          description="Show the active memory provider configuration of a profile.")
def memory_status(ctx):
    prof = None if ctx.profile == "default" else ctx.profile
    return {"profile": ctx.profile, "output": cli_text(["memory", "status"], prof, 30).strip()[-4000:]}


@reg.tool("memory_journey_list", toolset="memory", profile_aware=True,
          description="List learned skill/memory node ids of a profile as text (read-only).")
def memory_journey_list(ctx):
    prof = None if ctx.profile == "default" else ctx.profile
    return {"profile": ctx.profile, "output": cli_text(["journey", "list"], prof, 30).strip()[-8000:]}


@reg.tool("memory_off", toolset="memory", access="write", profile_aware=True,
          description="Disable the external memory provider (built-in memory only). Takes effect after a "
                      "gateway restart.")
def memory_off(ctx):
    return run_write(ctx, "disable external memory provider", ["memory", "off"])


@reg.tool("memory_reset", toolset="memory", access="admin", profile_aware=True, dangerous=True,
          description="ERASE all built-in memory (MEMORY.md and USER.md) of a profile. Irreversible. "
                      "Needs the key's `dangerous` scope and confirm=true; dry_run shows the command.",
          properties={"confirm": {"type": "boolean", "default": False}})
def memory_reset(ctx, confirm=False):
    if not confirm and not ctx.dry_run:
        ctx.dry_run = True
        out = run_write(ctx, "erase built-in memory", ["memory", "reset", "--yes"])
        out["needed"] = "confirm=true"
        return out
    return run_write(ctx, "erase built-in memory", ["memory", "reset", "--yes"])
