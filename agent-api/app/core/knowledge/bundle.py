"""OKF knowledge bundle reader/writer.

An OKF bundle is a directory of markdown files with YAML frontmatter. This module
owns the on-disk layout and the read/write primitives; recall/indexing lives in
:mod:`app.services.semantic_memory` (bank ``kb``).

Layout (all paths relative to the bundle root, default ``data/knowledge``)::

    index.md                     ← links every section + recent concepts (regenerated)
    log.md                       ← chronological change log (OKF reserved name)
    known-issues/<slug>.md       ← type: KnownIssue
    log-patterns/<slug>.md       ← type: LogPattern
    services/<slug>.md           ← type: Service  (link hub)
    skills/<name>/SKILL.md       ← type: Skill    (the SkillManager tree)

Every concept doc is::

    ---
    type: KnownIssue          # OKF: the only required field
    title: ...
    description: ...
    tags: [a, b]
    source: agent
    confidence: 0.87
    timestamp: 2026-07-16T12:00:00Z
    ---

    # Section
    ...markdown body, may link to [other concepts](../services/foo.md)...
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# section directory → OKF ``type`` for docs written there.
SECTION_TYPES: Dict[str, str] = {
    "known-issues": "KnownIssue",
    "log-patterns": "LogPattern",
    "services": "Service",
}

_LOG_FILE = "log.md"
_INDEX_FILE = "index.md"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def slugify(text: str) -> str:
    """Filesystem-safe, stable slug for a concept title (max ~80 chars)."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:80].rstrip("-")) or "untitled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── retrievability lint ──────────────────────────────────────────────────────
# Recall is FTS-only over the ``kb`` bank (no vector fallback), so a concept's
# findability by a *reworded* query depends entirely on its title terms plus its
# tags (document-side synonyms, weighted into search_vector at rank A/B). A doc
# with no tags and a thin title is effectively unfindable by paraphrase — the
# exact failure mode the FTS-only retrieval benchmark flagged. These checks are
# advisory: they surface such docs (warn on write, report on scan) but never
# block a write.
_MIN_DESCRIPTION_CHARS = 12
_MIN_TITLE_CHARS = 6


def lint_frontmatter(
    frontmatter: Dict[str, Any], *, min_tags: Optional[int] = None
) -> List[str]:
    """Return a list of retrievability issues for one concept's frontmatter.

    Empty list = the doc is well-authored for FTS recall. ``min_tags`` defaults
    to ``settings.knowledge_min_tags`` (fall back to 3 if settings unavailable).
    """
    if min_tags is None:
        try:
            from app.config import settings
            min_tags = int(getattr(settings, "knowledge_min_tags", 3))
        except Exception:  # noqa: BLE001 — a bad/absent setting must not break lint
            min_tags = 3

    fm = frontmatter or {}
    issues: List[str] = []

    title = str(fm.get("title") or "").strip()
    if len(title) < _MIN_TITLE_CHARS:
        issues.append(f"title too short ({len(title)} chars) — hurts FTS ranking")

    desc = str(fm.get("description") or "").strip()
    if len(desc) < _MIN_DESCRIPTION_CHARS:
        issues.append(
            "description missing or too short — the description leg of the FTS "
            "vector (weight B) is empty"
        )

    raw_tags = fm.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    if min_tags > 0 and len(tags) < min_tags:
        issues.append(
            f"only {len(tags)} tag(s), want >= {min_tags} — tags are the "
            "paraphrase-synonym mechanism for FTS-only recall"
        )

    return issues


class KnowledgeBundle:
    """Read/write an OKF knowledge bundle rooted at *root*.

    All writes are serialized by an instance lock and are idempotent by slug —
    writing a concept whose slug already exists updates it in place (refreshing
    ``timestamp``) rather than creating a duplicate.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    # ── paths ────────────────────────────────────────────────────────────────

    def section_dir(self, section: str) -> Path:
        return self.root / section

    def concept_path(self, section: str, slug: str) -> Path:
        return self.section_dir(section) / f"{slug}.md"

    # ── write ────────────────────────────────────────────────────────────────

    def write_concept(
        self,
        *,
        section: str,
        title: str,
        body: str,
        doc_type: Optional[str] = None,
        description: str = "",
        tags: Optional[List[str]] = None,
        source: str = "agent",
        confidence: Optional[float] = None,
        extra_frontmatter: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or update one OKF concept doc. Returns ``{path, slug, created}``.

        ``doc_type`` defaults to the section's canonical OKF type. Best-effort:
        raises only on genuinely unexpected IO errors (callers wrap in their own
        best-effort guard).
        """
        doc_type = doc_type or SECTION_TYPES.get(section, "Concept")
        slug = slugify(title)
        path = self.concept_path(section, slug)

        frontmatter: Dict[str, Any] = {
            "type": doc_type,
            "title": (title or "").strip()[:200],
        }
        if description:
            frontmatter["description"] = description.strip()[:500]
        if tags:
            frontmatter["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        frontmatter["source"] = source
        if confidence is not None:
            frontmatter["confidence"] = round(float(confidence), 3)
        if extra_frontmatter:
            for k, v in extra_frontmatter.items():
                frontmatter.setdefault(k, v)
        frontmatter["timestamp"] = _now_iso()

        # Retrievability lint — advisory, never blocks the write. Surfaces docs
        # that FTS-only recall will struggle to find by a reworded query.
        for issue in lint_frontmatter(frontmatter):
            logger.warning(
                "KnowledgeBundle: retrievability lint %s/%s — %s", section, slug, issue
            )

        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            created = not path.exists()
            path.write_text(self._render_doc(frontmatter, body), encoding="utf-8")
            self._append_log(
                f"{'created' if created else 'updated'} {section}/{slug} "
                f"({doc_type}, source={source})"
            )
            self._regenerate_indexes()

        logger.info(
            "KnowledgeBundle: %s concept %s/%s",
            "created" if created else "updated", section, slug,
        )
        return {"path": str(path), "slug": slug, "created": created, "section": section}

    @staticmethod
    def _render_doc(frontmatter: Dict[str, Any], body: str) -> str:
        fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{fm}\n---\n\n{(body or '').strip()}\n"

    # ── read ─────────────────────────────────────────────────────────────────

    def read_concept(self, path: Path) -> Optional[Dict[str, Any]]:
        """Parse one OKF doc into ``{frontmatter, body, path}`` (None if invalid)."""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("KnowledgeBundle: read failed for %s (%s)", path, exc)
            return None
        m = _FRONTMATTER_RE.match(text)
        if not m:
            return None
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as exc:
            logger.warning("KnowledgeBundle: bad frontmatter in %s (%s)", path, exc)
            return None
        if not isinstance(fm, dict):
            return None
        return {"frontmatter": fm, "body": m.group(2).strip(), "path": Path(path)}

    def list_concepts(self, section: Optional[str] = None) -> List[Path]:
        """Return concept doc paths, optionally within one section.

        Only ``.md`` files inside the concept sections are returned; the reserved
        ``index.md`` / ``log.md`` and the ``skills/`` tree are excluded (skills are
        managed by SkillManager and indexed separately as the skill listing).
        """
        sections = [section] if section else list(SECTION_TYPES.keys())
        out: List[Path] = []
        for sec in sections:
            d = self.section_dir(sec)
            if not d.exists():
                continue
            for p in sorted(d.glob("*.md")):
                if p.name in (_INDEX_FILE, _LOG_FILE):
                    continue
                out.append(p)
        return out

    def lint(self, *, min_tags: Optional[int] = None) -> List[Dict[str, Any]]:
        """Scan every concept and return retrievability issues per doc.

        Returns a list of ``{section, slug, path, issues}`` for docs with at
        least one issue (well-authored docs are omitted). Feeds a nightly/CI
        retrievability check — the write-time warning only catches new writes,
        so this covers git-shipped / human-authored docs too.
        """
        out: List[Dict[str, Any]] = []
        for section in SECTION_TYPES:
            for path in self.list_concepts(section):
                doc = self.read_concept(path)
                if not doc:
                    out.append({"section": section, "slug": path.stem,
                                "path": str(path), "issues": ["unparseable OKF doc"]})
                    continue
                issues = lint_frontmatter(doc.get("frontmatter") or {}, min_tags=min_tags)
                if issues:
                    out.append({"section": section, "slug": path.stem,
                                "path": str(path), "issues": issues})
        return out

    def concept_fields_for_index(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Structured fields for weighted FTS indexing into the ``kb`` bank.

        Returns ``{title, tags, description, body, flat}``. ``flat`` is the
        displayable content stored in the ``content`` column (title+desc+body,
        as before); ``title``/``tags``/``description`` drive the weighted
        ``search_vector`` (title+tags = weight A, description = B). Tags are the
        document-side synonym mechanism for paraphrase recall, so they must be
        carried here even though they are not part of ``flat``.
        """
        fm = doc.get("frontmatter") or {}
        title = str(fm.get("title") or "").strip()
        desc = str(fm.get("description") or "").strip()
        body = str(doc.get("body") or "").strip()
        raw_tags = fm.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        parts = [p for p in (title, desc, body) if p]
        return {
            "title": title,
            "tags": tags,
            "description": desc,
            "body": body,
            "flat": "\n".join(parts),
        }

    def concept_text_for_index(self, doc: Dict[str, Any]) -> str:
        """Flat displayable text stored in the ``kb`` memory bank (title+desc+body)."""
        return str(self.concept_fields_for_index(doc)["flat"])

    # ── index.md / log.md maintenance ─────────────────────────────────────────

    def _append_log(self, message: str) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            log_path = self.root / _LOG_FILE
            header = "" if log_path.exists() else "# Change log\n\n"
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{header}- {_now_iso()} — {message}\n")
        except OSError as exc:
            logger.debug("KnowledgeBundle: log append failed (%s)", exc)

    def _regenerate_indexes(self) -> None:
        """Rewrite each section ``index.md`` and the root ``index.md`` (link graph)."""
        try:
            root_lines = ["# Knowledge bundle", "",
                          "Open Knowledge Format (OKF) bundle — durable operational "
                          "knowledge, portable as plain files.", ""]
            for section, doc_type in SECTION_TYPES.items():
                concepts = self.list_concepts(section)
                if not concepts:
                    continue
                # Section index
                sec_lines = [f"# {section} ({doc_type})", ""]
                for p in concepts:
                    doc = self.read_concept(p)
                    title = ""
                    if doc:
                        title = str((doc["frontmatter"] or {}).get("title") or p.stem)
                    sec_lines.append(f"- [{title}]({p.name})")
                (self.section_dir(section) / _INDEX_FILE).write_text(
                    "\n".join(sec_lines) + "\n", encoding="utf-8"
                )
                # Root index entry
                root_lines.append(f"## {section}")
                for p in concepts:
                    doc = self.read_concept(p)
                    title = str((doc["frontmatter"] or {}).get("title") or p.stem) if doc else p.stem
                    root_lines.append(f"- [{title}]({section}/{p.name})")
                root_lines.append("")
            skills_dir = self.root / "skills"
            if skills_dir.exists():
                root_lines.append("## skills")
                root_lines.append("- [skills/](skills/) — invocable runbooks (SKILL.md)")
                root_lines.append("")
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / _INDEX_FILE).write_text("\n".join(root_lines) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.debug("KnowledgeBundle: index regenerate failed (%s)", exc)


# Process-wide default bundle (rooted at ``settings.knowledge_dir``).
_default_bundle: Optional[KnowledgeBundle] = None
_default_lock = threading.Lock()


def get_default_bundle() -> KnowledgeBundle:
    """Return the shared bundle rooted at ``settings.knowledge_dir``."""
    global _default_bundle
    if _default_bundle is None:
        with _default_lock:
            if _default_bundle is None:
                from app.config import settings
                _default_bundle = KnowledgeBundle(Path(settings.knowledge_dir))
    return _default_bundle


# ── recall indexing (bundle concept → ``kb`` memory bank) ────────────────────

async def index_concept_into_kb(
    bundle: KnowledgeBundle, path: Path, *, bank: str = "kb"
) -> Optional[int]:
    """Index one OKF concept into a memory *bank* (default ``kb``) for recall.

    Idempotent: ``semantic_memory.remember`` dedupes on (bank, repo, content
    sha), so re-indexing an unchanged doc is a no-op and an edited doc updates
    the same row. Title/tags/description are passed through so the FTS vector is
    weighted (tags = paraphrase synonyms). *bank* is overridable so eval suites
    can index into an isolated corpus (e.g. ``kb_eval``).
    """
    doc = bundle.read_concept(path)
    if not doc:
        return None
    from app.services.semantic_memory import semantic_memory

    fields = bundle.concept_fields_for_index(doc)
    content = str(fields["flat"])
    if not content.strip():
        return None
    fm = doc.get("frontmatter") or {}
    try:
        importance = float(fm.get("confidence")) if fm.get("confidence") is not None else 0.6
    except (TypeError, ValueError):
        importance = 0.6
    return await semantic_memory.remember(
        content,
        source=str(fm.get("source") or "kb"),
        importance=importance,
        veracity=1.0,
        bank=bank,
        title=fields["title"],
        tags=fields["tags"],
        description=fields["description"],
    )


async def reconcile_kb_index(
    bundle: Optional[KnowledgeBundle] = None, *, bank: str = "kb"
) -> int:
    """Rebuild the ``kb`` bank from the bundle. Returns count indexed.

    The ``kb`` bank is **derived data**: the OKF files on disk are the source of
    truth. Changing the indexed text (e.g. adding tag weighting) orphans old
    rows, and deleted docs would leave stale rows behind — so this drops every
    row in *bank* first, then re-indexes every current concept. Run at startup so
    human-authored / git-shipped docs are recalled even if they were never
    written through the app. Best-effort; never raises.
    """
    bundle = bundle or get_default_bundle()
    n = 0
    try:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text as _sql_text
        async with AsyncSessionLocal() as session:
            await session.execute(
                _sql_text("DELETE FROM semantic_memory WHERE bank = :bank"),
                {"bank": bank},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — a failed purge must not abort reindex
        logger.warning("reconcile_kb_index: bank purge failed (%s)", exc)
    try:
        for p in bundle.list_concepts():
            try:
                if await index_concept_into_kb(bundle, p, bank=bank) is not None:
                    n += 1
            except Exception as exc:  # noqa: BLE001 — one bad doc must not abort
                logger.debug("reconcile_kb_index: skip %s (%s)", p, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reconcile_kb_index failed (%s)", exc)
    return n


# ── one-time skills relocation into the bundle ───────────────────────────────

def migrate_legacy_skills(legacy_dir: str = "data/skills") -> bool:
    """Move a legacy ``data/skills`` tree under the bundle (``skills_dir``).

    Idempotent and defensive: only runs when the configured ``skills_dir`` is the
    new bundle default, the legacy dir exists, and the target is absent/empty.
    Returns True if a move happened.
    """
    from app.config import settings

    new = Path(settings.skills_dir)
    old = Path(legacy_dir)
    try:
        if new.resolve() == old.resolve():
            return False
        if not old.exists() or not any(old.iterdir()):
            return False
        if new.exists() and any(new.iterdir()):
            return False  # target already populated — don't clobber

        import shutil
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.exists():
            new.rmdir()  # empty (checked above) — remove so move renames cleanly
        shutil.move(str(old), str(new))
        logger.info("KnowledgeBundle: migrated skills %s → %s", old, new)
        return True
    except Exception as exc:  # noqa: BLE001 — migration must never block startup
        logger.warning("KnowledgeBundle: skills migration skipped (%s)", exc)
        return False
