# `docs/prep/` — pre-implementation research drafts

These are the research notes written **before** hermcp was implemented (October 2026). They are kept
for provenance: they record what was read in Hermes core and which design was chosen, and the
shipped docs grew out of them.

They are **not** documentation of the shipped behaviour. Each one has a shipped successor that is
kept current, and where the two disagree the shipped doc is right:

| Draft | Status | Shipped doc |
|---|---|---|
| [`approvals.md`](approvals.md) | historical | [`docs/approvals.md`](../approvals.md) — how approvals work inside Hermes, run-scoped and cross-session |
| [`cli-parity.md`](cli-parity.md) | historical (German original) | [`docs/cli-parity.md`](../cli-parity.md) — the English table; [`docs/CLI_INVENTORY.md`](../CLI_INVENTORY.md) — the command inventory |
| [`oauth.md`](oauth.md) | historical | [`docs/oauth.md`](../oauth.md) — OAuth 2.1 for Claude.ai connectors |

Nothing here is an install, configuration or behaviour reference, and no shipped doc links into this
folder. The numbers in these drafts (tool counts, catalog sizes) are the October 2026 snapshot; the
current ones are generated from the live registry in [`docs/tool-surface.md`](../tool-surface.md).
