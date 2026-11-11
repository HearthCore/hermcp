# Contributing

Thanks for helping. Please open an issue before larger changes so we can agree on direction.

## Development setup

hermcp runs inside the Hermes Agent virtualenv (it ships `starlette`, `uvicorn`, `mcp`).
The tests import the plugin as the package `hermcp`, so the directory must be named `hermcp`:

```bash
git clone https://github.com/HearthCore/hermcp.git ~/.hermes/plugins/hermcp
cd ~/.hermes/plugins
<hermes-venv>/bin/python -m pytest hermcp/tests -q
ruff check hermcp
```

Tests use a temporary `HERMES_HOME` and a fake `hermes` binary; they never touch your real install.

## Rules of the road

- Never return secrets from a tool, never write them to the audit log.
- New tools: pick the lowest sufficient `access` level, a toolset, and add tests covering
  scope filtering and `dry_run` where applicable. Regenerate the catalog:
  `python -m hermcp docs`.
- Do not modify Hermes core; import it or call the CLI behind an adapter.
- Keep changes focused; add or adjust tests; `pytest` and `ruff check` must pass.
- **Update `CHANGELOG.md` in every pull request.** Add one line per user-visible change under
  `## [Unreleased]`, in the matching group (`Added`, `Changed`, `Fixed`, `Documentation`, `Tests`),
  and link the commit that makes the change (`[`abc1234`](https://github.com/HearthCore/hermcp/commit/abc1234)`)
  plus the pull request (`[#NN](https://github.com/HearthCore/hermcp/pull/NN)`) once it has a number.
  Describe the effect in plain words, not the diff. Pure refactors with no visible effect may skip
  the entry; say so in the PR description. When a release is tagged, rename `[Unreleased]` to the
  version and date and open a fresh `[Unreleased]` above it.
- Be kind in reviews (see CODE_OF_CONDUCT.md).
