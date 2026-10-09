"""Model provisioning: streaming download, sha256 pins, TLS context choice."""
import hashlib
import io
import os
import ssl
from dataclasses import replace

import pytest

from app.core.code_semantic import provision
from app.core.code_semantic.models import MODEL_REGISTRY, EmbeddingModelSpec

PAYLOAD = b"\x08\x01" + b"onnx-bytes" * 100


def _spec(**kw) -> EmbeddingModelSpec:
    base = EmbeddingModelSpec(
        key="test-model", hf_repo="org/test-model", files=["onnx/model.onnx"],
        onnx_file="model.onnx", dim=4, revision="abc123",
    )
    return replace(base, **kw)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def fake_hf(monkeypatch):
    calls = []
    fake_hf_body = {"body": PAYLOAD}

    def _urlopen(req, context=None, timeout=None):
        calls.append({"url": req.full_url, "context": context})
        return _Resp(fake_hf_body["body"])

    monkeypatch.setattr(provision.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(provision.time, "sleep", lambda _s: None)
    monkeypatch.setitem(MODEL_REGISTRY, "test-model", _spec())
    return calls, fake_hf_body


def test_download_streams_to_disk_and_uses_pinned_revision(tmp_path, fake_hf):
    calls, _ = fake_hf
    d = provision.ensure_model(str(tmp_path), "test-model", allow_download=True, insecure=False)
    path = os.path.join(d, "model.onnx")
    assert open(path, "rb").read() == PAYLOAD
    assert not os.path.exists(path + ".part")
    assert calls[0]["url"] == "https://huggingface.co/org/test-model/resolve/abc123/onnx/model.onnx"
    assert calls[0]["context"].verify_mode == ssl.CERT_REQUIRED


def test_sha256_mismatch_is_rejected_and_not_retried(tmp_path, fake_hf, monkeypatch):
    calls, _ = fake_hf
    monkeypatch.setitem(MODEL_REGISTRY, "test-model",
                        _spec(sha256={"onnx/model.onnx": "0" * 64}))
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        provision.ensure_model(str(tmp_path), "test-model", allow_download=True, insecure=False)
    assert len(calls) == 1
    assert not os.listdir(os.path.join(tmp_path, "test-model"))


def test_matching_sha256_is_accepted(tmp_path, fake_hf, monkeypatch):
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    monkeypatch.setitem(MODEL_REGISTRY, "test-model", _spec(sha256={"onnx/model.onnx": digest}))
    d = provision.ensure_model(str(tmp_path), "test-model", allow_download=True, insecure=False)
    provision.verify_model_files(str(tmp_path), MODEL_REGISTRY["test-model"])
    assert os.path.exists(os.path.join(d, "model.onnx"))


def test_html_error_page_is_rejected(tmp_path, fake_hf):
    _, body = fake_hf
    body["body"] = b"<!DOCTYPE html><html>blocked by proxy</html>" + b" " * 400
    with pytest.raises(RuntimeError, match="after 4 attempts"):
        provision.ensure_model(str(tmp_path), "test-model", allow_download=True, insecure=False)


def test_insecure_context_only_when_opted_in():
    assert provision._ssl_context(insecure=False).verify_mode == ssl.CERT_REQUIRED
    assert provision._ssl_context(insecure=True).verify_mode == ssl.CERT_NONE


def test_vendored_arctic_files_match_their_pins():
    root = os.path.join(os.path.dirname(__file__), "..", "vendor", "models")
    spec = MODEL_REGISTRY["snowflake-arctic-embed-s"]
    if not provision.is_provisioned(root, spec):
        pytest.skip("vendored model not present in this checkout")
    provision.verify_model_files(root, spec)


def test_tampered_file_fails_verification(tmp_path):
    spec = _spec(sha256={"onnx/model.onnx": hashlib.sha256(PAYLOAD).hexdigest()})
    d = tmp_path / spec.key
    d.mkdir()
    (d / "model.onnx").write_bytes(PAYLOAD + b"tampered")
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        provision.verify_model_files(str(tmp_path), spec)
