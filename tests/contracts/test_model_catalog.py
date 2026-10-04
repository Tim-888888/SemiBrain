import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from semibrain_agent import configuration
from semibrain_agent.model_catalog import text_models
from semibrain_agent.provider import (
    ModelError,
    ProviderAdapter,
    profile_for,
    profile_from_snapshot,
)
from semibrain_contracts.configuration import ROLES


@pytest.fixture
def catalog(monkeypatch):
    values = {
        "primary-pro": {"context_window_tokens": 1000000},
        "alternate-fast": {"provider": "proxy", "reasoning_effort": "none", "context_window_tokens": 128000},
        "alternate-thinking": {"provider": "proxy", "reasoning_effort": "low", "encrypted_reasoning": True,
                               "context_window_tokens": 128000},
        "alternate-neutral": {"provider": "proxy", "reasoning_effort": None, "context_window_tokens": 128000},
    }
    monkeypatch.setenv("SEMIBRAIN_TEXT_MODEL_PROFILES", json.dumps(values))
    monkeypatch.setenv("SEMIBRAIN_LLM_BASE_URL", "https://primary.invalid/v1")
    monkeypatch.setenv("SEMIBRAIN_LLM_API_KEY", "primary-test-secret")
    monkeypatch.setenv("SEMIBRAIN_TEXT_PROXY_BASE_URL", "https://alternate.invalid/v1")
    monkeypatch.setenv("SEMIBRAIN_TEXT_PROXY_API_KEY", "alternate-test-secret")
    return values


@pytest.mark.parametrize("role", ROLES)
def test_every_role_resolves_selected_model_route_and_capabilities(monkeypatch, catalog, role):
    for model, expected in catalog.items():
        profile = profile_for(role, model=model)
        snapshot = profile.snapshot()
        restored = profile_from_snapshot(snapshot)
        transport = ProviderAdapter(restored)
        alternate = expected.get("provider") == "proxy"
        assert transport.base == ("https://alternate.invalid/v1" if alternate else "https://primary.invalid/v1")
        assert transport.key == ("alternate-test-secret" if alternate else "primary-test-secret")
        assert restored.model == model and restored.role == role
        for key, value in expected.items():
            assert getattr(restored, key) == value
        assert all(hidden not in json.dumps(snapshot) for hidden in
                   ("secret", "credential_prefix", "_BASE_URL", ".invalid"))


def test_default_and_legacy_snapshots_keep_primary_route_and_baseline(monkeypatch, catalog):
    monkeypatch.setenv("SEMIBRAIN_LLM_DEFAULT_MODEL", "existing-default")
    monkeypatch.delenv("SEMIBRAIN_LLM_INVESTIGATOR_MODEL", raising=False)
    old = profile_for().snapshot()
    assert "provider" not in old and profile_from_snapshot(old).credential_prefix == "SEMIBRAIN_LLM"
    signature = configuration.baseline()["baseline"]
    monkeypatch.setenv("SEMIBRAIN_TEXT_MODEL_PROFILES", "{}")
    assert configuration.baseline()["baseline"] == signature
    assert profile_for().snapshot() == old


def test_reviewed_selection_pins_provider_and_does_not_reinterpret_old_runs(monkeypatch, catalog):
    base = configuration.baseline()
    settings = deepcopy(base["settings"])
    settings["models"]["rag"] = "alternate-thinking"
    store = SimpleNamespace(agent_configuration=Mock(), agent_configuration_versions=Mock())
    store.agent_configuration.find_one.return_value = {"revision": 5, "active_version": "published"}
    store.agent_configuration_versions.find_one.return_value = {"_id": "published", "settings": settings,
                                                               "published_at": "checked"}
    monkeypatch.setattr(configuration, "db", lambda: store)
    config = configuration.snapshot()
    assert config["profiles"]["rag"]["provider"] == "proxy"
    assert config["profiles"]["rag"]["encrypted_reasoning"]
    assert set(catalog) <= set(configuration.model_choices())
    catalog["alternate-thinking"].update(provider="other", reasoning_effort="none", context_window_tokens=64000)
    monkeypatch.setenv("SEMIBRAIN_TEXT_MODEL_PROFILES", json.dumps(catalog))
    old = configuration.profile("rag", config)
    assert old.credential_prefix == "SEMIBRAIN_TEXT_PROXY" and old.reasoning_effort == "low"
    assert old.context_window_tokens == 128000
    assert configuration.profile("investigator", config).credential_prefix == "SEMIBRAIN_LLM"


@pytest.mark.parametrize("bad", [
    "[]", "not json", '{"bad":{"api_key":"never-leak-this"}}',
    '{"bad":{"base_url":"https://never-leak-this.invalid"}}',
    '{"bad":{"credential_prefix":"SEMIBRAIN_LLM"}}',
    '{"bad":{"provider":"../other"}}', '{"bad":{"tool_calling":"false"}}',
    '{"bad":{"context_window_tokens":0}}', '{"bad":{"protocol":"unknown"}}',
])
def test_invalid_catalog_fails_without_echoing_credentials(monkeypatch, bad):
    monkeypatch.setenv("SEMIBRAIN_TEXT_MODEL_PROFILES", bad)
    with pytest.raises(ModelError, match="^MODEL_CATALOG_INVALID$"):
        text_models()


def test_invalid_context_capacity_and_missing_provider_do_not_fall_back(monkeypatch, catalog):
    catalog["alternate-thinking"]["context_headroom_tokens"] = 128000
    monkeypatch.setenv("SEMIBRAIN_TEXT_MODEL_PROFILES", json.dumps(catalog))
    with pytest.raises(ModelError, match="MODEL_CATALOG_INVALID"):
        profile_for(model="alternate-thinking")
    monkeypatch.delenv("SEMIBRAIN_TEXT_PROXY_API_KEY")
    with pytest.raises(ModelError, match="MODEL_ROUTE_UNAVAILABLE"):
        ProviderAdapter(profile_for(model="alternate-fast"))


def test_vision_route_is_unchanged(catalog):
    profile = profile_for("vision")
    assert profile.credential_prefix == "SEMIBRAIN_VISION" and profile.image_input
