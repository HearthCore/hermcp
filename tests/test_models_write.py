"""models toolset, write paths: what the agent runs on and what it falls back to.

`tests/test_phase2.py` covers the happy round-trips; this file pins the branches that decide whether a
write happens at all — the no-op duplicate, the refused selector, the out-of-range index and the
`${NAME}` rule for an auxiliary api_key.
"""
import pytest
import yaml

from hermcp.registry import Principal
from hermcp.scopes import Scopes
from hermcp.settings import load_settings


@pytest.fixture()
def call(home):
    from hermcp.tools import load_all
    reg = load_all()
    st = load_settings({"HERMES_HOME": str(home)})
    admin = Principal("t", "t", Scopes(access="admin", approvals=True, secrets_write=True))

    def _call(tool, /, **args):
        r = reg.invoke(tool, args, admin, st, None)
        assert r["ok"], r
        res = r["result"]
        if isinstance(res, dict):
            res.pop("_meta", None)
        return res

    def _err(tool, /, **args):
        r = reg.invoke(tool, args, admin, st, None)
        assert r["ok"] is False, r
        return r["error"]

    _call.err, _call.home = _err, home
    return _call


def cfg(call):
    return yaml.safe_load((call.home / "config.yaml").read_text())


# ---- models_set --------------------------------------------------------------------------------
def test_models_set_writes_provider_and_model_as_two_changes(call):
    r = call("models_set", model="m2", provider="litellm")
    assert r["applied"] is True
    assert [c["changes"][0]["path"] for c in r["changes"]] == ["model.provider", "model.default"]
    assert cfg(call)["model"]["provider"] == "litellm" and cfg(call)["model"]["default"] == "m2"


def test_models_set_dry_run_leaves_the_config_alone(call):
    before = (call.home / "config.yaml").read_text()
    r = call("models_set", model="m2", dry_run=True)
    assert r["dry_run"] is True and r["applied"] is False
    assert (call.home / "config.yaml").read_text() == before


# ---- aliases -----------------------------------------------------------------------------------
def test_models_alias_remove_refuses_an_unknown_alias(call):
    err = call.err("models_alias_remove", alias="nope")
    assert err["code"] == "not_found" and "nope" in err["message"]


def test_models_alias_remove_dry_run_keeps_the_alias(call):
    call("models_alias_set", alias="fast", target="litellm/x")
    r = call("models_alias_remove", alias="fast", dry_run=True)
    assert r["dry_run"] is True and cfg(call)["model"]["aliases"]["fast"] == "litellm/x"


# ---- fallback chain ----------------------------------------------------------------------------
def test_models_fallback_add_is_a_noop_for_an_entry_that_is_already_there(call):
    call("models_fallback_add", provider="litellm", model="m1")
    r = call("models_fallback_add", provider="litellm", model="m1")
    assert r["applied"] is False and r["note"] == "no change"
    assert [e["model"] for e in cfg(call)["fallback_providers"]] == ["m1"]


def test_models_fallback_remove_needs_a_selector(call):
    err = call.err("models_fallback_remove")
    assert err["code"] == "invalid_arguments" and "clear=true" in err["message"]


def test_models_fallback_remove_refuses_an_index_past_the_end(call):
    call("models_fallback_add", provider="litellm", model="m1")
    err = call.err("models_fallback_remove", index=3)
    assert err["code"] == "not_found" and "index 3" in err["message"]
    assert [e["model"] for e in cfg(call)["fallback_providers"]] == ["m1"]


def test_models_fallback_remove_by_index_keeps_the_others(call):
    call("models_fallback_add", provider="litellm", model="m1")
    call("models_fallback_add", provider="anthropic", model="m2")
    call("models_fallback_remove", index=0)
    assert [e["model"] for e in cfg(call)["fallback_providers"]] == ["m2"]


# ---- auxiliary ---------------------------------------------------------------------------------
def test_models_auxiliary_set_dry_run_changes_nothing(call):
    r = call("models_auxiliary_set", task="vision", field="model", value="v-model", dry_run=True)
    assert r["dry_run"] is True and r["applied"] is False
    assert "auxiliary" not in cfg(call)


def test_models_auxiliary_set_accepts_an_env_reference_for_api_key(call):
    r = call("models_auxiliary_set", task="vision", field="api_key", value="${VISION_API_KEY}")
    assert r["applied"] is True
    assert cfg(call)["auxiliary"]["vision"]["api_key"] == "${VISION_API_KEY}"
