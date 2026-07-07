"""Chat session API routes — persistent, resumable conversations (migration 022).

Sessions hold metadata; messages are returned only when a single session is
fetched (lazy hydration).
"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.deps import get_session_repo
from app.infrastructure.persistence import SessionRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Request/response models ──────────────────────────────────────────────────

class SessionCreate(BaseModel):
    title: Optional[str] = "New chat"
    workflow_name: Optional[str] = None
    model: Optional[str] = None


class SessionPatch(BaseModel):
    title: Optional[str] = None
    archived: Optional[bool] = None
    is_important: Optional[bool] = None


class CompactRequest(BaseModel):
    keep_recent: int = 6


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreate,
    repo: SessionRepository = Depends(get_session_repo),
):
    """Create a new (empty) chat session and return it."""
    return await repo.create_session(
        title=body.title or "New chat",
        workflow_name=body.workflow_name,
        model=body.model,
    )


@router.get("", response_model=List[dict])
async def list_sessions(
    include_archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    repo: SessionRepository = Depends(get_session_repo),
):
    """List session metadata (no messages), most-recently-active first."""
    return await repo.list_sessions(include_archived=include_archived, limit=limit)


@router.get("/{session_id}", response_model=dict)
async def get_session(
    session_id: str,
    repo: SessionRepository = Depends(get_session_repo),
):
    """Fetch one session with its hydrated message history."""
    data = await repo.get_session(session_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return data


@router.patch("/{session_id}", response_model=dict)
async def patch_session(
    session_id: str,
    body: SessionPatch,
    repo: SessionRepository = Depends(get_session_repo),
):
    """Rename, archive/unarchive, or pin/unpin a session."""
    changed = False
    if body.title is not None:
        changed |= await repo.rename(session_id, body.title)
    if body.archived is not None:
        changed |= await repo.set_archived(session_id, body.archived)
    if body.is_important is not None:
        changed |= await repo.set_important(session_id, body.is_important)
    # A metadata patch never needs the turn history hydrated — skip that query.
    data = await repo.get_session(session_id, include_messages=False)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return data


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    repo: SessionRepository = Depends(get_session_repo),
):
    """Delete a session and all its messages (cascade)."""
    if not await repo.delete_session(session_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return None


# ── Extras (export + compact) ────────────────────────────────────────────────

@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = Query("md", pattern="^(md|json|html)$"),
    repo: SessionRepository = Depends(get_session_repo),
):
    """Export a conversation as markdown, JSON, or HTML."""
    data = await repo.get_session(session_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    messages = data.get("messages", [])
    title = data.get("title") or "Chat"

    if format == "json":
        return PlainTextResponse(
            json.dumps(data, indent=2, default=str),
            media_type="application/json",
        )
    if format == "html":
        rows = "".join(
            f"<div class='msg {m['role']}'><b>{m['role']}</b>"
            f"<pre>{_escape_html(m['content'])}</pre></div>\n"
            for m in messages
        )
        html = (
            f"<!doctype html><meta charset='utf-8'><title>{_escape_html(title)}</title>"
            f"<h1>{_escape_html(title)}</h1>\n{rows}"
        )
        return PlainTextResponse(html, media_type="text/html")

    # markdown (default)
    lines = [f"# {title}", ""]
    for m in messages:
        lines.append(f"**{m['role']}**:")
        lines.append("")
        lines.append(m["content"])
        lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@router.post("/{session_id}/compact", response_model=dict)
async def compact_session(
    session_id: str,
    body: CompactRequest,
    repo: SessionRepository = Depends(get_session_repo),
):
    """LLM-summarize older messages into one system note, keeping the recent tail."""
    data = await repo.get_session(session_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    messages = data.get("messages", [])
    keep = max(0, body.keep_recent)
    if len(messages) <= keep:
        return {"compacted": 0, "message_count": len(messages)}

    to_summarize = messages[:-keep] if keep > 0 else messages
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
    prompt = (
        "Summarize the following conversation excerpt into a concise note that "
        "preserves decisions, findings, identifiers, and open questions. Write "
        "3-8 bullet points, no preamble.\n\n" + transcript[:20000]
    )
    try:
        from app.crawler.call_llm import call_llm
        summary, _, _, _ = await call_llm(prompt, tier="search", use_cache=False)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, never 500 the UI
        logger.warning("compact_session: LLM summarize failed (%s)", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Summarization model unavailable",
        )

    result = await repo.replace_with_summary(
        session_id, summary=f"[Earlier conversation summary]\n{summary.strip()}", keep_recent=keep
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return result


def _escape_html(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
