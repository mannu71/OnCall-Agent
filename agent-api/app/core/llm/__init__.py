"""Shared DB-resolved LLM call utility.

Exposes :func:`call_llm` — a thin wrapper over the project's transport layer
that resolves its model/provider from the DB-configured LLM (Settings page,
"crawler" gateway role), with a Postgres prompt cache. Used across the app
(graders, supervision, memory extraction, session summaries, semantic memory).
"""
from app.core.llm.call_llm import call_llm

__all__ = ["call_llm"]
