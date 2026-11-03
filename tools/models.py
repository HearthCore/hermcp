"""models toolset: current model, providers/models, set model, aliases, fallbacks, auxiliary."""
from __future__ import annotations

from ..adapters import hconfig as H
from ..adapters.common import call_inproc, ensure_hermes_importable, hermes_scope
from ..registry import AdapterError, ToolError
from . import reg


def _model_block(profile):
    cur = H.get_value(profile, "model")["value"]
    return cur if isinstance(cur, dict) else {"default": cur}


@reg.tool("models_current", toolset="models", profile_aware=True, core=True,
          description="Current primary model/provider, aliases and fallback chain of a profile.")
def models_current(ctx):
    block = _model_block(ctx.profile)
    return {"model": {k: v for k, v in block.items() if k != "aliases"}, "aliases": block.get("aliases", {}),
            "fallbacks": H.raw_get(ctx.profile, "fallback_providers") or []}


@reg.tool("models_providers", toolset="models",
          description="List known inference providers with label and whether credentials are configured.")
def models_providers(ctx):
    ensure_hermes_importable()
    try:
        from hermes_cli.models import list_available_providers
    except ImportError as exc:
        raise AdapterError(f"hermes_cli.models unavailable: {exc}") from exc
    with hermes_scope("default"):
        return {"providers": call_inproc(list_available_providers)}


@reg.tool("models_list", toolset="models", profile_aware=True,
          description=("Model ids offered by a provider (live list if reachable, else the static catalog; refresh=true "
                       "forces a live query)."),
          properties={"provider": {"type": "string"}, "refresh": {"type": "boolean", "default": False},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}},
          required=["provider"])
def models_list(ctx, provider, refresh=False, limit=100):
    ensure_hermes_importable()
    try:
        from hermes_cli.models import provider_model_ids
    except ImportError as exc:
        raise AdapterError(f"hermes_cli.models unavailable: {exc}") from exc
    with hermes_scope(ctx.profile):
        ids = call_inproc(provider_model_ids, provider, force_refresh=refresh)
    return {"provider": provider, "count": len(ids), "models": ids[:limit]}


@reg.tool("models_set", toolset="models", access="write", profile_aware=True, core=True,
          description=("Set the primary model (and optionally the provider) of a profile; they are written as two "
                       "separate config changes. Takes effect for new sessions. Changes what the agent runs on and what "
                       "it costs."),
          properties={"model": {"type": "string"}, "provider": {"type": "string"}}, required=["model"])
def models_set(ctx, model, provider=None):
    steps = ([("model.provider", provider)] if provider else []) + [("model.default", model)]
    if ctx.dry_run:
        return ctx.plan("set primary model", changes=[H.plan_set(ctx.profile, k, v) for k, v in steps])
    return {"applied": True, "changes": [H.apply_set(ctx.profile, k, v, False) for k, v in steps]}


@reg.tool("models_alias_set", toolset="models", access="write", profile_aware=True,
          description="Create/update a model alias (name -> provider/model).",
          properties={"alias": {"type": "string", "pattern": "^[A-Za-z0-9._-]+$"}, "target": {"type": "string"}},
          required=["alias", "target"])
def models_alias_set(ctx, alias, target):
    key = f"model.aliases.{alias}"
    return ctx.plan("set alias", **H.plan_set(ctx.profile, key, target)) if ctx.dry_run \
        else H.apply_set(ctx.profile, key, target, False)


@reg.tool("models_alias_remove", toolset="models", access="write", profile_aware=True,
          description="Remove a model alias.", properties={"alias": {"type": "string", "pattern": "^[A-Za-z0-9._-]+$"}},
          required=["alias"])
def models_alias_remove(ctx, alias):
    key = f"model.aliases.{alias}"
    if H.get_value(ctx.profile, key)["exists"] is False:
        raise ToolError("not_found", f"no alias {alias!r}")
    return ctx.plan("remove alias", key=key) if ctx.dry_run else H.apply_unset(ctx.profile, key)


@reg.tool("models_fallbacks", toolset="models", profile_aware=True,
          description="List the global fallback chain (same as `hermes fallback list`): providers tried in order when the primary "
                      "model fails. Unpinned agent jobs, sessions and the main agent use it; cron jobs with their own "
                      "provider/model/base_url do not (see cron_list). Read-only.")
def models_fallbacks(ctx):
    return {"fallbacks": H.raw_get(ctx.profile, "fallback_providers") or []}


@reg.tool("models_fallback_add", toolset="models", access="admin", profile_aware=True, requires_approvals=True,
          description="Append a provider+model to the global fallback chain (optionally at a position). This changes "
                      "what every unpinned agent and cron job falls back to when the primary fails. Use dry_run first. "
                      "Needs the `approvals` scope.",
          properties={"provider": {"type": "string"}, "model": {"type": "string"}, "base_url": {"type": "string"},
                      "position": {"type": "integer", "minimum": 0}},
          required=["provider", "model"])
def models_fallback_add(ctx, provider, model, base_url=None, position=None):
    entry = {"provider": provider, "model": model, **({"base_url": base_url} if base_url else {})}

    def mut(chain):
        chain = list(chain or [])
        if any(e.get("provider") == provider and e.get("model") == model for e in chain):
            return chain
        chain.insert(len(chain) if position is None else min(position, len(chain)), entry)
        return chain
    return H.modify(ctx.profile, "fallback_providers", mut, ctx.dry_run)


@reg.tool("models_fallback_remove", toolset="models", access="admin", profile_aware=True, requires_approvals=True,
          description="Remove a fallback entry by index or by provider+model; `clear: true` removes the whole chain, "
                      "after which unpinned jobs have no backup provider. Use dry_run first. Needs the `approvals` scope.",
          properties={"index": {"type": "integer", "minimum": 0}, "provider": {"type": "string"},
                      "model": {"type": "string"}, "clear": {"type": "boolean"}})
def models_fallback_remove(ctx, index=None, provider=None, model=None, clear=False):
    if not (clear or index is not None or (provider and model)):
        raise ToolError("invalid_arguments", "give index, provider+model, or clear=true")

    def mut(chain):
        chain = list(chain or [])
        if clear:
            return []
        if index is not None:
            if index >= len(chain):
                raise ToolError("not_found", f"no fallback at index {index}")
            chain.pop(index)
            return chain
        return [e for e in chain if not (e.get("provider") == provider and e.get("model") == model)]
    return H.modify(ctx.profile, "fallback_providers", mut, ctx.dry_run)


@reg.tool("models_auxiliary", toolset="models", profile_aware=True,
          description=("Auxiliary (side-task) model settings such as vision, compression or title. Omit `task` for all "
                       "tasks. Credentials are redacted."), properties={"task": {"type": "string"}})
def models_auxiliary(ctx, task=None):
    return H.get_value(ctx.profile, f"auxiliary.{task}" if task else "auxiliary")


@reg.tool("models_auxiliary_set", toolset="models", access="write", profile_aware=True,
          description=("Set one auxiliary (side-task) setting, for example task=compression field=model value=<model "
                       "id>. field=api_key must be a ${NAME} reference; store the secret itself with secret_set."),
          properties={"task": {"type": "string", "pattern": "^[A-Za-z0-9_]+$"},
                      "field": {"type": "string", "enum": ["provider", "model", "base_url", "timeout", "api_key"]},
                      "value": {"type": "string"}},
          required=["task", "field", "value"])
def models_auxiliary_set(ctx, task, field, value):
    if field == "api_key" and not value.startswith("${"):
        raise ToolError("rejected", "api_key must be an env reference like ${NAME}; set the secret with secret_set")
    key = f"auxiliary.{task}.{field}"
    return ctx.plan("set auxiliary", **H.plan_set(ctx.profile, key, value)) if ctx.dry_run \
        else H.apply_set(ctx.profile, key, value, False)
