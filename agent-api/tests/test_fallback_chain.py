"""Unit tests for resolve_llm_fallback_chain (pure, no DB)."""
import pytest

from app.core import model_throttle_tracker as throttle
from app.workflow.llm_config import (
    resolve_llm_fallback_chain,
    throttle_target_for,
    _bare_bedrock_model,
    _key_id_for,
)


@pytest.fixture(autouse=True)
def clean_tracker():
    throttle.reset()
    yield
    throttle.reset()


def _bedrock_primary():
    return {
        "provider": "bedrock",
        "model": "anthropic.claude-sonnet-4-6-20251001-v1:0",
        "region": "us-east-1",
        "access_key_id": "AKIAEXAMPLE1234",
        "secret_access_key": "secret",
    }


# ─── helpers ──────────────────────────────────────────────────────────────────


def test_bare_model_strips_inference_prefix():
    assert _bare_bedrock_model("us.anthropic.claude-x") == "anthropic.claude-x"
    assert _bare_bedrock_model("eu.amazon.titan") == "amazon.titan"
    assert _bare_bedrock_model("anthropic.claude-x") == "anthropic.claude-x"


def test_key_id_prefers_access_key_last4():
    assert _key_id_for({"access_key_id": "AKIA....WXYZ"}) == "...WXYZ"
    assert _key_id_for({"key_label": "backup"}) == "backup"
    assert _key_id_for({}) == "default"


# ─── chain construction ───────────────────────────────────────────────────────


def test_disabled_returns_primary_only():
    primary = _bedrock_primary()
    chain = resolve_llm_fallback_chain(primary, enabled=False)
    assert chain == [primary]


def test_primary_is_first():
    chain = resolve_llm_fallback_chain(
        _bedrock_primary(), enabled=True,
        fallback_regions=["us-east-1", "us-west-2"],
        fallback_models=["anthropic.claude-haiku-4-5-20251001-v1:0"],
    )
    assert chain[0]["region"] == "us-east-1"
    assert "sonnet" in chain[0]["model"]


def test_chain_includes_region_and_model_failover():
    chain = resolve_llm_fallback_chain(
        _bedrock_primary(), enabled=True,
        fallback_regions=["us-east-1", "us-west-2"],
        fallback_models=["anthropic.claude-haiku-4-5-20251001-v1:0"],
    )
    regions = {c["region"] for c in chain}
    models = {_bare_bedrock_model(c["model"]) for c in chain}
    assert "us-west-2" in regions               # region failover present
    assert any("haiku" in m for m in models)    # model failover present


def test_alt_credentials_added_same_model_region():
    alt = {"access_key_id": "AKIAOTHER0009", "secret_access_key": "s2", "key_label": "backup"}
    chain = resolve_llm_fallback_chain(
        _bedrock_primary(), enabled=True, alt_credentials=[alt],
        fallback_regions=[], fallback_models=[],
    )
    # primary + one alternate-credential candidate, same model/region.
    assert len(chain) == 2
    assert chain[1]["access_key_id"] == "AKIAOTHER0009"
    assert chain[1]["region"] == "us-east-1"


def test_chain_dedupes_identical_targets():
    chain = resolve_llm_fallback_chain(
        _bedrock_primary(), enabled=True,
        # primary region duplicated in fallback list → must not appear twice.
        fallback_regions=["us-east-1", "us-east-1", "us-west-2"],
        fallback_models=[],
    )
    keys = [throttle_target_for(c).as_key() for c in chain]
    assert len(keys) == len(set(keys))


def test_non_bedrock_skips_region_model_failover():
    primary = {"provider": "openai", "model": "gpt-x", "api_key": "k"}
    alt = {"api_key": "k2", "key_label": "b"}
    chain = resolve_llm_fallback_chain(primary, enabled=True, alt_credentials=[alt])
    # Only credential rotation, no region/model expansion for non-Bedrock.
    assert all(c["provider"] == "openai" for c in chain)
    assert all(c["model"] == "gpt-x" for c in chain)


def test_cooled_targets_sink_to_back():
    primary = _bedrock_primary()
    # Mark the us-west-2 region-failover target as throttled.
    west = {**primary, "model": _bare_bedrock_model(primary["model"]), "region": "us-west-2"}
    throttle.mark_throttled(throttle_target_for(west))

    chain = resolve_llm_fallback_chain(
        primary, enabled=True,
        fallback_regions=["us-east-1", "us-west-2"],
        fallback_models=["anthropic.claude-haiku-4-5-20251001-v1:0"],
    )
    # The cooled us-west-2 candidate must appear after all ready candidates.
    west_idx = next(i for i, c in enumerate(chain) if c["region"] == "us-west-2")
    ready_idxs = [i for i, c in enumerate(chain)
                  if not throttle.is_cooled(throttle_target_for(c))]
    assert west_idx > max(ready_idxs)
