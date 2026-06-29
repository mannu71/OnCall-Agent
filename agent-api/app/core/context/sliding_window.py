"""Proactive sliding-window summarisation (plan §2.3).

Replaces the *reactive* compress-on-overflow path (3x LLM cost) with a
*proactive* sliding window: after every N turns, the oldest ReAct
iteration is collapsed into a single ``SystemMessage`` summary so the
input stays bounded without ever hitting Anthropic's overflow error.

Design constraints:

- **Recent tool results stay verbatim** (last ``keep_recent`` iterations)
  so the agent can reason about exact log lines, query rows, etc.
- **Summaries are cheap** — built by an injected callable so strategies
  can wire in the two-tier router (``NodeRole.FORMAT`` -> Haiku).
- **Idempotent** — calling ``maybe_summarise`` repeatedly is safe; if
  no work is needed the original list is returned unchanged.

Strategies adopt incrementally; the helper has no side effects beyond
calling the injected ``summarise_fn``.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# Async function that takes a list of messages and returns a 1-paragraph summary.
SummariseFn = Callable[[List[BaseMessage]], Awaitable[str]]


def _approx_tokens(messages: List[BaseMessage]) -> int:
    """Rough token estimate for the whole message list (char/4)."""
    total = 0
    for m in messages:
        c = m.content
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict):
                    total += len(block.get("text", ""))
                else:
                    total += len(str(block))
        else:
            total += len(str(c))
    return total // 4


async def maybe_summarise(
    messages: List[BaseMessage],
    *,
    summarise_fn: SummariseFn,
    keep_recent_turns: int = 2,
    every_n_turns: int = 4,
    context_window: int = 200_000,
    soft_pct: float = 0.60,
) -> List[BaseMessage]:
    """Return a possibly-summarised copy of *messages*.

    Triggers when **either**:
      - the message list contains >= ``every_n_turns`` ReAct iterations, or
      - the estimated token count exceeds ``soft_pct * context_window``.

    Behaviour:
      - The leading SystemMessage(s) are preserved untouched.
      - The last ``keep_recent_turns`` ReAct iterations stay verbatim.
      - Everything in between is collapsed into a single synthetic
        ``SystemMessage("Prior context summary: <one paragraph>")``.

    A "turn" is defined here as: one ``HumanMessage`` followed by its
    (optional) ``ToolMessage`` results and the next ``AIMessage`` reply.
    We anchor on ``HumanMessage`` because every ReAct iteration starts
    with one (either the user task or a synthesised observation prompt).
    """
    if not messages:
        return messages

    # Split out leading SystemMessage(s).
    head_system: List[BaseMessage] = []
    body: List[BaseMessage] = []
    for m in messages:
        if isinstance(m, SystemMessage) and not body:
            head_system.append(m)
        else:
            body.append(m)

    # Iteration anchors = HumanMessage indices in the body.
    anchors = [i for i, m in enumerate(body) if isinstance(m, HumanMessage)]
    n_turns = len(anchors)

    over_token_budget = _approx_tokens(body) > soft_pct * context_window
    enough_turns = n_turns >= every_n_turns

    if not (over_token_budget or enough_turns):
        return messages

    if n_turns <= keep_recent_turns:
        # Not enough turns to safely collapse anything.
        return messages

    # Split point: index of the (n - keep_recent_turns)th anchor.
    cut = anchors[n_turns - keep_recent_turns]
    older = body[:cut]
    recent = body[cut:]

    if not older:
        return messages

    try:
        summary_text = await summarise_fn(older)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sliding_window: summarise_fn raised (%s); leaving messages unchanged.",
            exc,
        )
        return messages

    summary_msg = SystemMessage(
        content=f"Prior context summary (covering {len(older)} earlier messages): {summary_text}"
    )

    logger.info(
        "sliding_window: collapsed %d msgs -> 1 summary "
        "(kept %d recent, %d turns total, ~%d tokens before)",
        len(older), len(recent), n_turns, _approx_tokens(body),
    )
    return [*head_system, summary_msg, *recent]
