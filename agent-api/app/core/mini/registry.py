"""Registry of small local models ("mini models").

A :class:`MiniModelSpec` says exactly which files to load and how to trust them.
Every spec must be **pinned**: either an immutable ``revision`` (a commit SHA)
or a ``sha256`` for every file, and its license must be on the allow-list. A
spec that fails :meth:`MiniModelSpec.validate` cannot be registered.

The registry starts empty on purpose. Models are added only after they win the
M0 bake-off in ``evals/mini`` (see the mini-model implementation plan).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple

#: Roles a mini model can fill. Each maps to one ``MINI_<ROLE>_MODEL`` setting.
ROLES: Tuple[str, ...] = ("router", "pii", "injection", "code_embed", "recall_embed", "rerank")

#: Tasks a spec can implement (the method it serves on :class:`MiniModel`).
TASKS: Tuple[str, ...] = ("classify", "extract", "embed", "rerank")

#: Licenses acceptable for a shipped model. Anything else needs a deliberate
#: decision (e.g. Llama 4 Community for Prompt Guard 2) and an explicit entry.
ALLOWED_LICENSES: Tuple[str, ...] = ("apache-2.0", "mit", "bsd-3-clause")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class MiniModelSpec:
    """Immutable description of one local model and how to verify it."""

    #: Stable local id; also the on-disk directory name under the models root.
    key: str
    #: One of :data:`TASKS`.
    task: str
    #: HuggingFace repo the files come from.
    hf_repo: str
    #: Repo-relative files; flattened to basenames on disk.
    files: Tuple[str, ...]
    #: Basename of the ONNX graph (post-flatten).
    onnx_file: str
    #: SPDX-style license id, lower case.
    license: str
    #: Commit SHA (40 hex) preferred; ``main`` only with full sha256 pins.
    revision: str = "main"
    #: Expected sha256 per remote file.
    sha256: Mapping[str, str] = field(default_factory=dict)
    #: Truncation length in tokens.
    max_length: int = 512
    #: Labels for ``classify`` specs, in model output order.
    labels: Tuple[str, ...] = ()
    #: Share of the mini thread budget this model may use (1 = one thread).
    threads: int = 1
    #: Free-text provenance (benchmark source, bake-off result).
    notes: str = ""

    def validate(self) -> None:
        """Raise ``ValueError`` unless the spec is pinned, licensed and well-formed."""
        if self.task not in TASKS:
            raise ValueError(f"{self.key}: unknown task {self.task!r}")
        if self.license.lower() not in ALLOWED_LICENSES:
            raise ValueError(
                f"{self.key}: license {self.license!r} is not on the allow-list "
                f"{ALLOWED_LICENSES}; add it deliberately if approved"
            )
        if not self.files or self.onnx_file not in {f.rsplit('/', 1)[-1] for f in self.files}:
            raise ValueError(f"{self.key}: onnx_file must be one of the listed files")
        pinned_by_revision = bool(_SHA_RE.match(self.revision))
        missing = [f for f in self.files if f not in self.sha256]
        if not pinned_by_revision and missing:
            raise ValueError(
                f"{self.key}: revision {self.revision!r} is not a commit SHA, so every "
                f"file needs a sha256 pin; missing {missing}"
            )
        bad = [f for f, h in self.sha256.items() if not _SHA256_RE.match(h)]
        if bad:
            raise ValueError(f"{self.key}: malformed sha256 for {bad}")
        if self.task == "classify" and not self.labels:
            raise ValueError(f"{self.key}: classify specs must declare labels")
        if self.threads < 1:
            raise ValueError(f"{self.key}: threads must be >= 1")


_REGISTRY: Dict[str, MiniModelSpec] = {}


def register(spec: MiniModelSpec) -> MiniModelSpec:
    """Validate and add *spec*; re-registering the same key replaces it."""
    spec.validate()
    _REGISTRY[spec.key] = spec
    return spec


def get_spec(key: str) -> Optional[MiniModelSpec]:
    return _REGISTRY.get(key)


def all_specs() -> Dict[str, MiniModelSpec]:
    return dict(_REGISTRY)


def model_for_role(role: str) -> Optional[MiniModelSpec]:
    """The spec configured for *role*, or ``None`` when the role is off.

    A role is on only when mini models are enabled globally and its
    ``mini_<role>_model`` setting names a registered spec.
    """
    if role not in ROLES:
        raise ValueError(f"unknown mini-model role {role!r}")
    from app.config import settings

    if not getattr(settings, "mini_models_enabled", False):
        return None
    key = (getattr(settings, f"mini_{role}_model", "") or "").strip()
    return _REGISTRY.get(key) if key else None
