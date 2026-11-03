"""Helpers for subprocess-backed tools."""
from __future__ import annotations

from typing import Any

from ..adapters.common import cli_json, cli_text, plan_cli


def add_opts(argv: list[str], **opts: Any) -> list[str]:
    """Append `--flag value` pairs. Names map `foo_bar` -> `--foo-bar`; True -> bare flag; lists repeat;
    None/False are skipped. A value of "" is passed (used by Hermes to clear a field)."""
    for name, val in opts.items():
        flag = "--" + name.rstrip("_").replace("_", "-")
        if val is None or val is False:
            continue
        if val is True:
            argv.append(flag)
        elif isinstance(val, (list, tuple)):
            for v in val:
                argv += [flag, str(v)]
        else:
            argv += [flag, str(val)]
    return argv


def run_write(ctx, summary: str, argv: list[str], *, json_out: bool = False, timeout: float = 60,
              stdin: str | None = None) -> dict[str, Any]:
    """Execute (or, for dry_run, describe) a mutating `hermes` command."""
    if ctx.dry_run:
        return plan_cli(ctx, summary, argv)
    prof = None if ctx.profile == "default" else ctx.profile
    if stdin is not None:
        from ..adapters.common import run_hermes
        from ..registry import ToolError
        cp = run_hermes(argv, prof, timeout, stdin=stdin)
        if cp.returncode != 0:
            raise ToolError("cli_error", (cp.stderr.strip() or cp.stdout.strip())[-1500:], exit_code=cp.returncode)
        out = cp.stdout
        return {"applied": True, "output": out.strip()[-4000:]}
    if json_out:
        return {"applied": True, "result": cli_json(argv, prof, timeout)}
    return {"applied": True, "output": cli_text(argv, prof, timeout).strip()[-4000:]}
