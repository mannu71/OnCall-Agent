"""Model-invoked skill loading (stage two of two-stage skill disclosure).

Stage one is a compact per-turn *listing* of available skills (name +
description), assembled by ``SkillManager.build_listing`` and injected into the
user turn by ``context_builder.build_recall_query``. This module owns stage two:

  * :func:`build_skill_tool` — a pinned ``skill`` tool the model calls to load a
    listed skill's full runbook body into the conversation on demand. The body
    comes back as the tool result, framed so the agent follows it.
  * :func:`expand_slash_command` — deterministic ``/<skill-name> [args]``
    expansion for user chat messages, so a typed slash command invokes the
    skill's runbook without waiting on the model to call the tool.

Both resolve skills through the shared ``SkillManager`` and honour per-agent
scoping (the Agent node's Skills picker). Errors are returned as strings (never
raised) so a bad skill name never crashes the agent loop — the same convention
``tool_disclosure.call_tool`` uses.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Matches a leading "/skill-name optional args…" (DOTALL so multi-line args are
# captured). The name is a slug: starts alnum, then word chars or hyphens.
_SLASH_RE = re.compile(r"^\s*/([A-Za-z0-9][\w-]*)\b[ \t]*(.*)$", re.DOTALL)


def expand_slash_command(
    user_query: str,
    allowed: Optional[Set[str]] = None,
) -> Optional[Tuple[str, str]]:
    """Expand a ``/<skill-name> [args]`` user message into its runbook.

    Returns ``(expanded_query, resolved_skill_name)`` on a match, else ``None``
    (the message is left untouched — it may legitimately start with a path or an
    unknown token). ``disable_model_invocation`` is intentionally IGNORED here:
    that flag only blocks *model* invocation; an explicit user slash-command is
    a deliberate request. ``allowed`` (when non-empty) scopes resolution to the
    agent's picked skills.
    """
    if not user_query:
        return None
    m = _SLASH_RE.match(user_query)
    if not m:
        return None
    name, args = m.group(1), (m.group(2) or "").strip()

    try:
        from app.core.skills import get_default_skill_manager
        mgr = get_default_skill_manager()
        skill = mgr.resolve_command(name)
    except Exception as exc:  # noqa: BLE001 — resolution never blocks a run
        logger.warning("skill_tools: slash resolution failed for %r (%s)", name, exc)
        return None

    if skill is None:
        return None
    if allowed and skill.name not in allowed:
        # The command names a real skill this agent isn't scoped to — leave the
        # message unchanged rather than silently invoking an off-limits skill.
        return None

    expanded = mgr.build_invocation_message(skill, user_instruction=args)
    return expanded, skill.name


def build_skill_tool(
    *,
    allowed_skills: Optional[Set[str]] = None,
    execution_id: Optional[str] = None,
    logger_instance: Optional[Any] = None,
    invoked_sink: Optional[List[str]] = None,
) -> Any:
    """Return the pinned ``skill`` tool that loads a skill's runbook on demand.

    ``allowed_skills``: when non-empty, only these skill names are invocable
    (per-agent scoping). ``invoked_sink``: an optional mutable list the tool
    appends each successfully-loaded skill name to, so the run can surface which
    skills actually fired (the UI badge) — shared across subagent snapshots
    because they reuse the same tool object.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    log = logger_instance or logger

    class SkillInput(BaseModel):
        skill: str = Field(
            description=(
                'The skill name exactly as shown in the "Available skills" list, '
                'e.g. "log-error-triage". A leading "/" is tolerated.'
            )
        )
        args: str = Field(
            default="",
            description="Optional free-text arguments / context for the skill.",
        )

    def _available_names() -> List[str]:
        try:
            from app.core.skills import get_default_skill_manager
            mgr = get_default_skill_manager()
            return [
                s["name"]
                for s in mgr.list_skills()
                if not s.get("disable_model_invocation")
                and (not allowed_skills or s["name"] in allowed_skills)
            ]
        except Exception:  # noqa: BLE001
            return []

    async def _invoke_skill(skill: str, args: str = "") -> str:
        name = (skill or "").strip().lstrip("/")
        if not name:
            return "[skill error] No skill name given. Pick one from the Available skills list."
        try:
            from app.config import settings
            from app.core.skills import get_default_skill_manager
            mgr = get_default_skill_manager()
            resolved = mgr.resolve_command(name)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the loop
            log.warning("skill_tools: resolve failed for %r (%s)", name, exc)
            return f"[skill error] Could not load skill '{name}': {exc}"

        avail = _available_names()
        if resolved is None:
            hint = f" Available: {', '.join(avail)}." if avail else ""
            return f"[skill error] No skill named '{name}'.{hint}"
        if allowed_skills and resolved.name not in allowed_skills:
            hint = f" Available to this agent: {', '.join(avail)}." if avail else ""
            return f"[skill error] Skill '{resolved.name}' is not available to this agent.{hint}"
        if resolved.disable_model_invocation:
            return (
                f"[skill error] Skill '{resolved.name}' cannot be invoked by the model "
                "(it is user-command only)."
            )

        body = mgr.build_invocation_message(resolved, user_instruction=args or "")
        cap = int(getattr(settings, "skill_body_inject_chars", 8000))
        if len(body) > cap:
            body = body[:cap].rstrip() + "\n\n…(runbook truncated — continues on disk)"

        if invoked_sink is not None and resolved.name not in invoked_sink:
            invoked_sink.append(resolved.name)
        log.info(
            "skill_tools: loaded skill '%s' (exec=%s)", resolved.name, execution_id,
            extra={"execution_id": execution_id},
        )

        desc = (resolved.description or "").strip()
        header = f'Loaded skill "{resolved.name}"'
        if desc:
            header += f" — {desc}"
        return (
            f"{header}\n\n"
            f'<skill_instructions name="{resolved.name}">\n'
            f"{body}\n"
            "</skill_instructions>\n\n"
            "You have now loaded this skill. Follow its instructions step by step for "
            "the user's current task, using your other tools as the skill directs. Do "
            "not invoke this skill again this turn."
        )

    return StructuredTool.from_function(
        coroutine=_invoke_skill,
        name="skill",
        description=(
            "Load a skill — a proven, reusable runbook — and follow it. The available "
            "skills are listed in the 'Available skills' block of the user message, each "
            "with a name and when to use it. When a skill matches the user's request this "
            "is a BLOCKING REQUIREMENT: invoke it with skill=\"<name>\" BEFORE doing any "
            "other work on the task, then follow the returned instructions step by step. "
            "Never mention a skill without invoking it. A user message that starts with "
            "'/<skill-name>' is a request to invoke that skill."
        ),
        args_schema=SkillInput,
    )


__all__ = ["build_skill_tool", "expand_slash_command"]
