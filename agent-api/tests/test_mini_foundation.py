"""Mini-model foundation: pinned registry, CPU budget, bounded spawn pool."""
import asyncio
import os

import pytest

from app.core.mini import budget, pool, registry
from app.core.mini.registry import MiniModelSpec

SHA = "a" * 64


def _spec(**kw):
    base = dict(key="router-test", task="classify", hf_repo="org/m",
                files=("onnx/model.onnx", "tokenizer.json"), onnx_file="model.onnx",
                license="apache-2.0", sha256={"onnx/model.onnx": SHA, "tokenizer.json": SHA},
                labels=("lookup", "investigate"))
    base.update(kw)
    return MiniModelSpec(**base)


# ── registry ────────────────────────────────────────────────────────────────
def test_fully_hashed_spec_validates():
    _spec().validate()


def test_commit_sha_revision_allows_missing_hashes():
    _spec(revision="0123456789abcdef0123456789abcdef01234567", sha256={}).validate()


@pytest.mark.parametrize("kw, msg", [
    ({"sha256": {"onnx/model.onnx": SHA}}, "needs a sha256 pin"),
    ({"license": "llama4"}, "allow-list"),
    ({"license": "cc-by-nc-4.0"}, "allow-list"),
    ({"task": "generate"}, "unknown task"),
    ({"labels": ()}, "declare labels"),
    ({"onnx_file": "other.onnx"}, "onnx_file"),
    ({"sha256": {"onnx/model.onnx": "xyz", "tokenizer.json": SHA}}, "malformed"),
])
def test_unpinned_or_unlicensed_specs_are_rejected(kw, msg):
    with pytest.raises(ValueError, match=msg):
        _spec(**kw).validate()


def test_roles_are_off_unless_enabled_and_registered(monkeypatch):
    from app.config import settings

    spec = registry.register(_spec())
    try:
        monkeypatch.setattr(settings, "mini_router_model", "router-test")
        monkeypatch.setattr(settings, "mini_models_enabled", False)
        assert registry.model_for_role("router") is None
        monkeypatch.setattr(settings, "mini_models_enabled", True)
        assert registry.model_for_role("router") is spec
        monkeypatch.setattr(settings, "mini_router_model", "unknown")
        assert registry.model_for_role("router") is None
        with pytest.raises(ValueError):
            registry.model_for_role("summarize")
    finally:
        registry._REGISTRY.pop("router-test", None)


def test_mini_models_are_off_by_default():
    from app.config import Settings

    assert Settings.model_fields["mini_models_enabled"].default is False


# ── budget ──────────────────────────────────────────────────────────────────
def test_cgroup_v2_quota_is_read(tmp_path):
    f = tmp_path / "cpu.max"
    f.write_text("200000 100000\n")
    assert budget.cgroup_cpu_limit(v2_path=str(f)) == 2.0
    f.write_text("max 100000\n")
    assert budget.cgroup_cpu_limit(v2_path=str(f)) is None


def test_cgroup_v1_quota_is_read(tmp_path):
    q, p = tmp_path / "q", tmp_path / "p"
    q.write_text("150000")
    p.write_text("100000")
    assert budget.cgroup_cpu_limit(v2_path=str(tmp_path / "none"),
                                   v1_quota=str(q), v1_period=str(p)) == 1.5
    q.write_text("-1")
    assert budget.cgroup_cpu_limit(v2_path=str(tmp_path / "none"),
                                   v1_quota=str(q), v1_period=str(p)) is None


def test_inference_threads_reserves_cores():
    assert budget.inference_threads(reserved=1, cpus=4) == 3
    assert budget.inference_threads(reserved=2, cpus=2) == 1
    assert budget.available_cpus() >= 1


# ── pool ────────────────────────────────────────────────────────────────────
@pytest.fixture
def mini_pool():
    p = pool.MiniPool(workers=1, threads_per_worker=1, queue_limit=2, default_timeout=10.0)
    yield p
    p.shutdown()


async def test_pool_runs_in_a_separate_process(mini_pool):
    assert await mini_pool.run("router", os.getpid, fallback=lambda: -1) != os.getpid()
    assert await mini_pool.run("router", pool._ping, "hi", fallback=lambda: "fb") == "hi"
    stats = mini_pool.stats()["router"]
    assert stats["calls"] == 2 and stats["ok"] == 2 and "p50_ms" in stats


async def test_timeout_returns_fallback_and_keeps_slot_until_done(mini_pool):
    await mini_pool.run("pii", pool._ping, 1, fallback=lambda: None)  # warm the worker
    out = await mini_pool.run("pii", pool._sleep, 1.0, fallback=lambda: "regex", timeout=0.1)
    assert out == "regex"
    assert mini_pool.in_flight == 1          # abandoned call still occupies a slot
    await asyncio.sleep(1.3)
    assert mini_pool.in_flight == 0
    assert mini_pool.stats()["pii"]["fallback_timeout"] == 1


async def test_queue_full_falls_back_immediately(mini_pool):
    await mini_pool.run("injection", pool._ping, 1, fallback=lambda: None)
    slow = [asyncio.create_task(mini_pool.run("injection", pool._sleep, 0.5,
                                              fallback=lambda: "fb")) for _ in range(2)]
    await asyncio.sleep(0.05)
    assert await mini_pool.run("injection", pool._ping, 1, fallback=lambda: "busy") == "busy"
    assert await asyncio.gather(*slow) == [0.5, 0.5]
    assert mini_pool.stats()["injection"]["fallback_queue_full"] == 1


async def test_worker_errors_and_crashes_fall_back_and_recover(mini_pool):
    assert await mini_pool.run("rerank", pool._sleep, "not-a-number",
                               fallback=lambda: "fb") == "fb"
    assert await mini_pool.run("rerank", os._exit, 1, fallback=lambda: "crashed") == "crashed"
    assert await mini_pool.run("rerank", pool._ping, "back", fallback=lambda: "fb") == "back"
    stats = mini_pool.stats()["rerank"]
    assert stats["fallback_error"] == 2 and stats["ok"] == 1


def test_get_pool_sizes_from_settings(monkeypatch):
    from app.config import settings

    pool.shutdown_pool()
    monkeypatch.setattr(settings, "mini_pool_workers", 2)
    monkeypatch.setattr(settings, "mini_reserved_cpus", 1)
    monkeypatch.setattr(budget, "available_cpus", lambda: 5)
    p = pool.get_pool()
    try:
        assert p.workers == 2 and p.threads_per_worker == 2
        assert pool.get_pool() is p
    finally:
        pool.shutdown_pool()
