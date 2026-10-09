"""Offline-first ONNX model provisioning.

The model dir is expected to be baked into the image or mounted on the data
volume. When it isn't, and ``code_semantic_allow_download`` is on,
:func:`ensure_model` fetches the spec's files from HuggingFace.

Integrity and transport:

* **TLS is verified.** Behind the corporate TLS-inspecting proxy, compose mounts
  the proxy CA and points ``SSL_CERT_FILE`` / ``REQUESTS_CA_BUNDLE`` at it, so a
  default context trusts the re-signed chain. An unverified context is only used
  when ``model_download_insecure`` is explicitly enabled, and it logs a warning.
* **Files are pinned.** Each spec names a ``revision`` (commit SHA or tag) and,
  where known, the ``sha256`` of every file. A pinned file whose hash does not
  match is rejected, so a changed or tampered upstream file can never load.
* **Downloads stream to disk.** Files are written in chunks to a ``.part`` file
  and hashed on the way, never buffered whole in memory, then atomically renamed.

Files are flattened to basenames on disk (``onnx/model_quantized.onnx`` →
``model_quantized.onnx``) so the ONNX graph and its ``.onnx_data`` sidecar sit
side by side, which onnxruntime requires.
"""
from __future__ import annotations

import hashlib
import logging
import os
import ssl
import time
import urllib.request
from typing import Optional

from app.core.code_semantic.models import EmbeddingModelSpec, get_model_spec

logger = logging.getLogger(__name__)

_HF_BASE = "https://huggingface.co/{repo}/resolve/{revision}/{path}"
# Smallest plausible real model file. config.json for bge is ~870 bytes, so the
# floor must stay low; a corp-proxy error page is caught by the HTML sniff below,
# not by size. (Just guards against a truly empty / a-few-bytes response.)
_MIN_VALID_BYTES = 200
_CHUNK = 1 << 20  # 1 MiB
#: Env vars that may point at a CA bundle, in priority order.
_CA_ENV_VARS = ("MODEL_DOWNLOAD_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "AWS_CA_BUNDLE")


def _looks_like_error_page(data: bytes) -> bool:
    """True if the payload is an HTML/proxy error page, not a model file."""
    head = data[:512].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def model_dir(models_root: str, spec: EmbeddingModelSpec) -> str:
    """Absolute directory for a model's flattened files."""
    return os.path.join(models_root, spec.key)


def is_provisioned(models_root: str, spec: EmbeddingModelSpec) -> bool:
    """True if every file in the spec is present and non-trivial on disk."""
    d = model_dir(models_root, spec)
    for local in spec.flat_files().values():
        p = os.path.join(d, local)
        if not (os.path.exists(p) and os.path.getsize(p) > _MIN_VALID_BYTES):
            return False
    return True


def file_sha256(path: str) -> str:
    """Hex sha256 of a file, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def verify_model_files(models_root: str, spec: EmbeddingModelSpec) -> None:
    """Raise if any pinned file on disk does not match its sha256.

    Files without a pin are skipped (logged once at download time instead).
    """
    d = model_dir(models_root, spec)
    for remote, local in spec.flat_files().items():
        expected = spec.sha256.get(remote)
        if not expected:
            continue
        actual = file_sha256(os.path.join(d, local))
        if actual != expected:
            raise RuntimeError(
                f"{spec.key}/{local} sha256 mismatch: expected {expected}, got {actual}"
            )


def _ca_bundle() -> Optional[str]:
    for var in _CA_ENV_VARS:
        path = (os.environ.get(var) or "").strip()
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return None


def _ssl_context(insecure: bool) -> ssl.SSLContext:
    """Verified TLS context (trusting a configured CA bundle), or opt-in unverified."""
    if insecure:
        logger.warning(
            "code_semantic: model downloads use UNVERIFIED TLS (model_download_insecure"
            "=true); pinned sha256 checks still apply"
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=_ca_bundle())


#: Download attempts per file (corp proxy drops large transfers mid-stream).
_DOWNLOAD_ATTEMPTS = 4


def _stream_to_file(
    url: str, dst: str, ctx: ssl.SSLContext, timeout: float, expected_sha256: Optional[str]
) -> int:
    """Stream *url* into ``dst + '.part'``, validate, then atomically rename.

    Returns the byte count. Raises on an error page, a too-small payload or a
    sha256 mismatch; the partial file is removed in every failure case.
    """
    tmp = dst + ".part"
    h = hashlib.sha256()
    size = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp, \
                open(tmp, "wb") as out:
            first = True
            for block in iter(lambda: resp.read(_CHUNK), b""):
                if first and _looks_like_error_page(block):
                    raise RuntimeError(f"{os.path.basename(dst)}: got an HTML page, not a model file")
                first = False
                h.update(block)
                out.write(block)
                size += len(block)
        if size < _MIN_VALID_BYTES:
            raise RuntimeError(f"{os.path.basename(dst)}: only {size} bytes — proxy error?")
        if expected_sha256 and h.hexdigest() != expected_sha256:
            raise RuntimeError(
                f"{os.path.basename(dst)}: sha256 mismatch "
                f"(expected {expected_sha256}, got {h.hexdigest()})"
            )
        os.replace(tmp, dst)
        return size
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _download_with_retries(
    url: str, dst: str, model_key: str, ctx: ssl.SSLContext, timeout: float,
    expected_sha256: Optional[str],
) -> None:
    """Fetch *url* to *dst*, retrying transient truncations / connection resets.

    The TLS-intercepting proxy here frequently drops large transfers mid-stream
    (``IncompleteRead`` / reset), so a single-shot read is unreliable for the
    33-160MB model graphs. A sha256 mismatch is not retried: it is not transient.
    """
    last_err: Optional[Exception] = None
    local = os.path.basename(dst)
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        t0 = time.time()
        try:
            size = _stream_to_file(url, dst, ctx, timeout, expected_sha256)
            logger.info(
                "code_semantic: fetched %s/%s (%d bytes in %.1fs, attempt %d)",
                model_key, local, size, time.time() - t0, attempt,
            )
            return
        except Exception as exc:  # noqa: BLE001 — retry any transient fetch error
            if "sha256 mismatch" in str(exc):
                raise
            last_err = exc
            logger.warning(
                "code_semantic: download of %s/%s failed (attempt %d/%d): %s",
                model_key, local, attempt, _DOWNLOAD_ATTEMPTS, exc,
            )
            if attempt < _DOWNLOAD_ATTEMPTS:
                time.sleep(2.0 * attempt)
    raise RuntimeError(
        f"Failed to download {local} for {model_key} after "
        f"{_DOWNLOAD_ATTEMPTS} attempts: {last_err}"
    )


def ensure_model(
    models_root: str,
    model_key: Optional[str] = None,
    allow_download: bool = False,
    timeout: float = 300.0,
    insecure: Optional[bool] = None,
) -> str:
    """Ensure the model's files exist under ``models_root``; return the model dir.

    Args:
        models_root: Base dir holding one subdir per model (e.g. ``/opt/models``).
        model_key: Registry key; defaults to the registry default.
        allow_download: When the files are missing, fetch them. If ``False`` and
            files are missing, raise.
        timeout: Per-file download timeout (seconds).
        insecure: Use unverified TLS. Defaults to ``settings.model_download_insecure``.

    Returns:
        Absolute path to the model dir (files guaranteed present on success).

    Raises:
        FileNotFoundError: files missing and ``allow_download`` is False.
        RuntimeError: a download failed, or a pinned file's sha256 did not match.
    """
    spec = get_model_spec(model_key)
    d = model_dir(models_root, spec)

    if is_provisioned(models_root, spec):
        return d

    if not allow_download:
        raise FileNotFoundError(
            f"Embedding model '{spec.key}' not provisioned under {d}. "
            f"Vendor {list(spec.flat_files().values())} into the image/volume, "
            f"or enable code_semantic_allow_download."
        )

    if insecure is None:
        try:
            from app.config import settings
            insecure = bool(getattr(settings, "model_download_insecure", False))
        except Exception:  # noqa: BLE001 — config import must never break provisioning
            insecure = False

    unpinned = [r for r in spec.files if r not in spec.sha256]
    if unpinned:
        logger.warning(
            "code_semantic: %s has no sha256 pin for %s (revision %s); integrity is "
            "not verified for those files", spec.key, unpinned, spec.revision,
        )

    os.makedirs(d, exist_ok=True)
    ctx = _ssl_context(insecure)
    for remote, local in spec.flat_files().items():
        dst = os.path.join(d, local)
        if os.path.exists(dst) and os.path.getsize(dst) > _MIN_VALID_BYTES:
            continue
        url = _HF_BASE.format(repo=spec.hf_repo, revision=spec.revision, path=remote)
        _download_with_retries(url, dst, spec.key, ctx, timeout, spec.sha256.get(remote))

    if not is_provisioned(models_root, spec):
        raise RuntimeError(f"Provisioning {spec.key} incomplete after download.")
    verify_model_files(models_root, spec)
    return d
