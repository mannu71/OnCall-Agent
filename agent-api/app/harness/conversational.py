"""Conversational fast-path — a cheap reply for small talk / capability questions.

When a turn is purely conversational (a greeting, a thank-you, or a "what can you
do / what tools are available" meta-question — see
``app.core.intent.is_conversational``), it needs no logs, no code search, and no
DB. Routing it through the full agent build is wasteful: the model would still
receive the full system prompt PLUS every bound tool schema (codegraph, DB-schema,
vfs, planning, edit, delegate). That measured ~28K input tokens for a one-word "Hi".

This module answers such turns with a SINGLE model call against a tiny,
capability-aware system prompt and NO tools — cutting the prompt to a few hundred
tokens while still letting the model accurately answer "what can you do?". It
returns the same result envelope ``ReactStrategy.execute`` produces, so the caller
can return it as-is. On any error it raises, and the caller falls back to the full
path, so a real turn is never dropped.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core import privacy
from app.core.redact import redact


def _capability_lines(
    *, has_cloudwatch: bool, has_code_analyzer: bool, has_db: bool,
    skill_names: Optional[List[str]] = None,
) -> List[str]:
    """Bullet list of what the agent can actually do, given the wired nodes."""
    lines: List[str] = []
    if has_cloudwatch:
        lines.append(
            "- Investigate AWS CloudWatch logs, metrics, and alarms to root-cause incidents."
        )
    if has_code_analyzer:
        lines.append(
            "- Search and analyze code across the connected repositories — find definitions, "
            "trace call paths, and inspect files."
        )
    if has_db:
        lines.append(
            "- Look up database schemas and run read-only queries against the connected databases."
        )
    if skill_names:
        # Cap the enumerated names so a large library doesn't bloat the prompt.
        _shown = ", ".join(skill_names[:12])
        _more = "" if len(skill_names) <= 12 else f", and {len(skill_names) - 12} more"
        lines.append(
            "- Apply reusable skills / runbooks for common procedures "
            f"(e.g. {_shown}{_more})."
        )
    if not lines:
        lines.append("- Answer engineering and operational questions.")
    return lines


def _build_system_prompt(
    *, has_cloudwatch: bool, has_code_analyzer: bool, has_db: bool,
    skill_names: Optional[List[str]] = None,
) -> str:
    caps = "\n".join(
        _capability_lines(
            has_cloudwatch=has_cloudwatch,
            has_code_analyzer=has_code_analyzer,
            has_db=has_db,
            skill_names=skill_names,
        )
    )
    return (
        "You are a helpful assistant. The user's message is small talk or a question about "
        "your capabilities — reply directly and concisely, and do NOT call any tools.\n\n"
        "On a real request you can:\n"
        f"{caps}\n\n"
        "If the user asks what you can do or what tools are available, summarize the capabilities "
        "above in a friendly sentence or two. Otherwise just respond naturally to their message."
    )


async def conversational_reply(
    *,
    llm: Any,
    user_query: str,
    llm_config: Dict[str, Any],
    has_cloudwatch: bool,
    has_code_analyzer: bool,
    has_db: bool,
    stream_callback: Any,
    execution_id: Optional[str],
    logger_instance: Any,
    skill_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Answer a conversational turn with one tool-less model call.

    Caller MUST have bound the privacy session (``privacy.bind_session``) so the
    query is pseudonymized before Bedrock and the reply re-hydrated. Returns the
    strategy result envelope. Raises on model failure so the caller can fall back.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.harness.agent_runner import extract_text_content

    system_prompt = _build_system_prompt(
        has_cloudwatch=has_cloudwatch,
        has_code_analyzer=has_code_analyzer,
        has_db=has_db,
        skill_names=skill_names,
    )

    # Privacy boundary: swap PII for placeholders before the model sees the query;
    # the reply is re-hydrated below. No-op when pseudonymization is disabled.
    safe_query = privacy.pseudonymize(user_query, execution_id)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=safe_query)]

    input_tokens = 0
    output_tokens = 0
    answer = ""

    if stream_callback is not None:
        parts: List[str] = []
        final_chunk = None
        async for chunk in llm.astream(messages):
            piece = extract_text_content(getattr(chunk, "content", ""))
            if piece:
                parts.append(piece)
                await stream_callback.on_llm_token(piece)
            final_chunk = chunk if final_chunk is None else final_chunk + chunk
        answer = "".join(parts) or extract_text_content(
            getattr(final_chunk, "content", "")
        )
        usage = getattr(final_chunk, "usage_metadata", None) or {}
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
    else:
        resp = await llm.ainvoke(messages)
        answer = extract_text_content(getattr(resp, "content", ""))
        usage = getattr(resp, "usage_metadata", None) or {}
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0

    answer = privacy.rehydrate(answer, execution_id) or answer

    logger_instance.info(
        "ReactStrategy: conversational fast-path reply "
        "(input_tokens=%d output_tokens=%d exec=%s)",
        input_tokens, output_tokens, execution_id,
        extra={"execution_id": execution_id},
    )

    return {
        "type": "react",
        "user_query": user_query,
        "final_answer": answer,
        "structured_output": None,
        "output_mode": "text",
        "privacy_redactions": privacy.redaction_summary(execution_id),
        "todos": [],
        "selected_skills": [],
        "messages": [
            {"role": "user", "content": user_query},
            {"role": "assistant", "content": answer},
        ],
        "message_count": 2,
        "tool_calls": [],
        "model": llm_config.get("model", "unknown"),
        "provider": llm_config.get("provider", "unknown"),
        "supervisor_escalated": False,
        "supervisor_reason": None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
