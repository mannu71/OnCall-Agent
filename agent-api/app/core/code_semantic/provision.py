"""Offline-first ONNX model provisioning.

The model dir is expected to be baked into the image or mounted on the data
volume. When it isn't, :func:`ensure_model` fetches the spec's files straight
from HuggingFace over an *unverified* TLS context.

Why unverified: this environment sits behind a TLS-intercepting corporate proxy
whose CA is not in the trust store (the same reason ``AWS_SSL_VERIFY`` and npm's
``strict-ssl`` are disabled image-wide). Ordinary ``pip`` / ``huggingface_hub``
downloads fail here with ``CERTIFICATE_VERIFY_FAILED``; a raw ``urllib`` request
with ``ssl.CERT_NONE`` is the one path that gets the bytes. This mirrors the
validated in-container proof. Downloads are opt-in via
``code_semantic_allow_download`` so a locked-down deploy can require the files be
pre-vendored instead.

Files are flattened to basenames on disk (``onnx/model_quantized.onnx`` →
``model_quantized.onnx``) so the ONNX graph and its ``.onnx_data`` sidecar sit
side by side, which onnxruntime requires.
"""
from __future__ import annotations

import logging
import os
import ssl
import time
import urllib.request
from typing import Optional

from app.core.code_semantic.models import EmbeddingModelSpec, get_model_spec

logger = logging.getLogger(__name__)

_HF_BASE = "https://huggingface.co/{repo}/resolve/main/{path}"
# Smallest plausible real model file. config.json for bge is ~870 bytes, so the
# floor must stay low; a corp-proxy error page is caught by the HTML sniff below,
# not by size. (Just guards against a truly empty / a-few-bytes response.)
_MIN_VALID_BYTES = 200


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


def _unverified_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


#: Download attempts per file (corp proxy drops large transfers mid-stream).
_DOWNLOAD_ATTEMPTS = 4


def _download_with_retries(
    url: str, local: str, model_key: str, ctx: ssl.SSLContext, timeout: float
) -> bytes:
    """Fetch a URL fully, retrying transient truncations / connection resets.

    The TLS-intercepting proxy here frequently drops large transfers mid-stream
    (``IncompleteRead`` / reset), so a single-shot read is unreliable for the
    33-160MB model graphs. Retries with linear backoff; validates the payload
    isn't an HTML error page. Raises after the last attempt.
    """
    import time as _time

    last_err: Optional[Exception] = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        t0 = _time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            data = urllib.request.urlopen(req, context=ctx, timeout=timeout).read()
            if len(data) < _MIN_VALID_BYTES or _looks_like_error_page(data):
                raise RuntimeError(
                    f"{local} for {model_key} is not a valid model file "
                    f"({len(data)} bytes) — proxy error page?"
                )
            logger.info(
                "code_semantic: fetched %s/%s (%d bytes in %.1fs, attempt %d)",
                model_key, local, len(data), _time.time() - t0, attempt,
            )
            return data
        except Exception as exc:  # noqa: BLE001 — retry any transient fetch error
            last_err = exc
            logger.warning(
                "code_semantic: download of %s/%s failed (attempt %d/%d): %s",
                model_key, local, attempt, _DOWNLOAD_ATTEMPTS, exc,
            )
            if attempt < _DOWNLOAD_ATTEMPTS:
                _time.sleep(2.0 * attempt)
    raise RuntimeError(
        f"Failed to download {local} for {model_key} after "
        f"{_DOWNLOAD_ATTEMPTS} attempts: {last_err}"
    )


def ensure_model(
    models_root: str,
    model_key: Optional[str] = None,
    allow_download: bool = False,
    timeout: float = 300.0,
) -> str:
    """Ensure the model's files exist under ``models_root``; return the model dir.

    Args:
        models_root: Base dir holding one subdir per model (e.g.
            ``/app/data/models``).
        model_key: Registry key; defaults to the registry default.
        allow_download: When the files are missing, fetch them over an
            unverified TLS context. If ``False`` and files are missing, raise.
        timeout: Per-file download timeout (seconds).

    Returns:
        Absolute path to the model dir (files guaranteed present on success).

    Raises:
        FileNotFoundError: files missing and ``allow_download`` is False.
        RuntimeError: a download produced a suspiciously small file.
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

    os.makedirs(d, exist_ok=True)
    ctx = _unverified_ctx()
    for remote, local in spec.flat_files().items():
        dst = os.path.join(d, local)
        if os.path.exists(dst) and os.path.getsize(dst) > _MIN_VALID_BYTES:
            continue
        url = _HF_BASE.format(repo=spec.hf_repo, path=remote)
        data = _download_with_retries(url, local, spec.key, ctx, timeout)
        # Atomic-ish write so a partial download can't masquerade as provisioned.
        tmp = dst + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)

    if not is_provisioned(models_root, spec):
        raise RuntimeError(f"Provisioning {spec.key} incomplete after download.")
    return d
