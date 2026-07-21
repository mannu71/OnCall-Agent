"""Model-invoked skill search + loading (stage two of skill disclosure).

Stage one is the *skill map* — an ultra-compact names-only index of available
skills, built by ``SkillManager.build_map`` and injected into the user turn by
``context_builder.build_recall_query``. Names alone cost near-nothing per turn;
the model resolves a name into a runbook through this module:

  * :func:`build_skill_search_tool` — a pinned ``search_skills`` tool that ranks
    the mapped skills against a plain-words query and returns the matches with
    their descriptions, so the model can pick the right name.
  * :func:`build_skill_tool` — a pinned ``skill`` tool the model calls to load a
    mapped skill's full runbook body into the conversation on demand. The body
    comes back as the tool result, framed so the agent follows it. Calling it
    directly off the map (skipping the search) is fine for an obvious match.
  * :func:`expand_slash_command` — deterministic ``/<skill-name> [args]``
    expansion for user chat messages, so a typed slash command invokes the
    skill's runbook without waiting on the model to call the tool.

Both resolve skills through the shared ``SkillManager`` and honour per-agent
scoping (the Agent node's Skills picker). Errors are returned as strings (never
raised) so a bad skill name never crashes the agent loop — the same convention
``tool_disclosure.call_tool`` uses.
"""
from __future__ import annotations

import contextvars
import logging
import re
from typing import Any, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Per-execution-context "already loaded this turn" set for the ``skill`` tool's
# re-invocation guard. The SAME skill tool object is shared by the parent agent
# and every subagent (subagents receive the parent's tool objects, scoped — see
# app.harness.subagent_factory._scope_tools), so a guard held in the tool's
# closure would leak across contexts: a skill loaded inside a subagent (whose
# messages the parent never sees) would make a later parent load return the
# "already loaded" stub, leaving the parent believing it has a runbook it does
# not. Keying the guard on a ContextVar fixes that — each agent run installs its
# own set via :func:`install_load_guard` (called from run_agent_once, the single
# entry point for both parent and child), so the sets are naturally isolated.
# Default ``None`` = no guard installed (standalone/direct tool use, e.g. tests);
# the tool then falls back to its own per-instance set.
_load_guard_ctx: contextvars.ContextVar[Optional[Set[str]]] = contextvars.ContextVar(
    "skill_load_guard", default=None
)


def install_load_guard(preloaded: Optional[List[str]] = None) -> contextvars.Token:
    """Install a fresh per-context skill-load guard set, returning the reset token.

    Call once at the start of every agent run (parent and each subagent) and
    ``reset_load_guard`` in a finally — this is what isolates one context's
    "already loaded" bookkeeping from another's. ``preloaded`` seeds the set with
    skills whose runbook is already in the context before the model's first call
    (deterministic ``/slash`` expansion); pass a STABLE snapshot, not the live
    invoked-sink, so a retried attempt doesn't inherit the previous attempt's
    model-loaded names (whose runbooks are gone from the rebuilt context).
    """
    return _load_guard_ctx.set(set(preloaded or ()))


def reset_load_guard(token: contextvars.Token) -> None:
    """Restore the previous skill-load guard (pair with :func:`install_load_guard`)."""
    try:
        _load_guard_ctx.reset(token)
    except (ValueError, LookupError):  # token from another context — best-effort
        pass

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

    The re-invocation guard (don't re-emit a runbook already in this context) is
    kept SEPARATE from ``invoked_sink``: it reads the per-context guard set
    installed by :func:`install_load_guard` (falling back to a private
    per-instance set when none is installed, e.g. direct/test use). Because that
    set is per-execution-context, a skill loaded in a subagent never stubs a
    load in the parent — the bug that arises from ``invoked_sink`` being both the
    badge log AND shared across contexts. See ``_load_guard_ctx``.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    log = logger_instance or logger
    # Fallback guard for when no ContextVar guard is installed (standalone tool
    # use / tests). In a real run, install_load_guard's per-context set takes
    # precedence, so parent and subagent stay isolated.
    _instance_guard: Set[str] = set()

    class SkillInput(BaseModel):
        skill: str = Field(
            description=(
                'The skill name exactly as shown in the "Skill map" block or in '
                'search_skills results, e.g. "log-error-triage". A leading "/" '
                "is tolerated."
            )
        )
        args: str = Field(
            default="",
            description="Optional free-text arguments / context for the skill.",
        )
        part: int = Field(
            default=1,
            description=(
                "Which part of a long runbook to load (1-based). Only needed when a "
                "previous load ended with a 'call skill(..., part=N)' notice because "
                "the runbook was too long to return at once; leave as 1 otherwise."
            ),
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

    async def _invoke_skill(skill: str, args: str = "", part: int = 1) -> str:
        name = (skill or "").strip().lstrip("/")
        if not name:
            return (
                "[skill error] No skill name given. Pick one from the Skill map, or "
                "call search_skills to find it."
            )
        try:
            part = int(part)
        except (TypeError, ValueError):
            part = 1
        if part < 1:
            part = 1
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

        # Re-invocation guard: if this skill's FIRST part was already loaded in
        # THIS context, don't re-emit its (capped) runbook — pure token waste, the
        # model already has the instructions above. A stub is returned instead.
        # Guard is per-context (see _load_guard_ctx) so a subagent's load never
        # stubs the parent. A part>=2 request is a deliberate continuation, not a
        # reload, so it is never stubbed.
        guard = _load_guard_ctx.get()
        if guard is None:
            guard = _instance_guard
        if part <= 1 and resolved.name in guard:
            return (
                f"[skill] '{resolved.name}' is already loaded in this context — its "
                "instructions are above. Continue following them; do not reload it."
            )

        full_body = mgr.build_invocation_message(resolved, user_instruction=args or "")
        cap = max(1, int(getattr(settings, "skill_body_inject_chars", 8000)))
        total_parts = max(1, (len(full_body) + cap - 1) // cap)
        if part > total_parts:
            return (
                f"[skill error] Skill '{resolved.name}' has only {total_parts} part(s); "
                f"part {part} does not exist. Load part {total_parts} or lower."
            )
        body = full_body[(part - 1) * cap: part * cap].rstrip()
        # Actionable continuation notice — the runbook lives in the manager, not on
        # any tool-reachable filesystem, so point the model at the next part call
        # rather than at "on disk" (which it cannot read).
        if total_parts > 1:
            if part < total_parts:
                body += (
                    f"\n\n…(part {part}/{total_parts} — call "
                    f'skill(skill="{resolved.name}", part={part + 1}) for the rest)'
                )
            else:
                body += f"\n\n…(part {part}/{total_parts} — end of runbook)"

        # Record the load: the guard (so part-1 reloads stub) and the badge sink
        # (so the UI shows the skill fired). Both are idempotent per name.
        guard.add(resolved.name)
        if invoked_sink is not None and resolved.name not in invoked_sink:
            invoked_sink.append(resolved.name)
        log.info(
            "skill_tools: loaded skill '%s' part %d/%d (exec=%s)",
            resolved.name, part, total_parts, execution_id,
            extra={"execution_id": execution_id},
        )

        desc = (resolved.description or "").strip()
        if part > 1:
            header = f'Continuation of skill "{resolved.name}" (part {part}/{total_parts})'
            footer = (
                "This is a continuation of the runbook above. Keep following it; load "
                "further parts only if a notice above asks you to."
            )
        else:
            header = f'Loaded skill "{resolved.name}"'
            if desc:
                header += f" — {desc}"
            footer = (
                "You have now loaded this skill. Follow its instructions step by step for "
                "the user's current task, using your other tools as the skill directs. Do "
                "not reload this skill; only fetch a further part if a notice above asks."
            )
        return (
            f"{header}\n\n"
            f'<skill_instructions name="{resolved.name}">\n'
            f"{body}\n"
            "</skill_instructions>\n\n"
            f"{footer}"
        )

    return StructuredTool.from_function(
        coroutine=_invoke_skill,
        name="skill",
        description=(
            "Load a skill — a proven, reusable runbook — and follow it. The skill names "
            "available to you are in the 'Skill map' block of the user message; use "
            "search_skills to find the right one when the map's names alone don't make "
            "it obvious. When a skill matches the user's request this is a BLOCKING "
            "REQUIREMENT: invoke it with skill=\"<name>\" BEFORE doing any other work on "
            "the task, then follow the returned instructions step by step. Never mention "
            "a skill without invoking it. A user message that starts with "
            "'/<skill-name>' is a request to invoke that skill."
        ),
        args_schema=SkillInput,
    )


def build_skill_search_tool(
    *,
    allowed_skills: Optional[Set[str]] = None,
    execution_id: Optional[str] = None,
    logger_instance: Optional[Any] = None,
) -> Any:
    """Return the pinned ``search_skills`` tool that finds skills by intent.

    Stage one of disclosure gives the model only skill NAMES (the map); this
    ranks them against a plain-words query and returns the matches with their
    descriptions and ``when_to_use`` text, so the model can pick which one to
    load with ``skill``. Searching is not invoking — there is no invoked-sink
    here; only an actual ``skill`` load counts as using a skill.

    ``allowed_skills``: when non-empty, only these skill names are searchable
    (per-agent scoping, same contract as :func:`build_skill_tool`).
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    log = logger_instance or logger

    try:
        from app.config import settings
        default_k = int(getattr(settings, "skill_search_k", 5))
    except Exception:  # noqa: BLE001 — a bad setting must not block tool binding
        default_k = 5
    # Clamp to the same [1, 10] range the handler enforces, so the advertised
    # default never promises more than a query can actually return.
    default_k = max(1, min(default_k, 10))
    # Cap on the names dumped in the no-match fallback — the map is budgeted, so
    # this list must be too, or a large library floods the tool result.
    _NO_MATCH_NAME_CAP = 25

    class SearchSkillsInput(BaseModel):
        query: str = Field(
            description=(
                "What you are trying to do, in plain words (e.g. 'triage errors in "
                "logs', 'an alarm is firing and I need to drill into it'). Returns "
                "the matching skills with their names and when to use them."
            )
        )
        limit: int = Field(
            default=default_k,
            description=f"Max number of skills to return (default {default_k}).",
        )

    async def _search_skills(query: str, limit: int = default_k) -> str:
        try:
            k = max(1, min(int(limit), 10))
        except (TypeError, ValueError):
            k = default_k
        try:
            from app.core.skills import get_default_skill_manager
            # No maybe_rescan(): this is the agent hot path, so it runs on the
            # cached skill set (refreshed by API writes) — same contract as the
            # ``skill`` tool and the map.
            mgr = get_default_skill_manager()
            effective = mgr.model_invocable_names(allowed_skills)
            # ``effective`` is the EXACT invocable set for this agent. Pass it
            # straight through: select_for_query treats an explicit empty set as
            # "nothing invocable" (returns []), distinct from None ("no scoping"),
            # so a scoped agent with no invocable skills never leaks the library.
            hits = mgr.select_for_query((query or "").strip(), k=k, allowed=effective)
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the loop
            log.warning(
                "skill_tools: skill search failed for %r (%s)", query, exc,
                extra={"execution_id": execution_id},
            )
            return f"[search_skills error] Skill search failed: {exc}"

        if not hits:
            ordered = sorted(effective)
            if not ordered:
                avail = " No skills are available."
            else:
                shown = ", ".join(ordered[:_NO_MATCH_NAME_CAP])
                extra = len(ordered) - _NO_MATCH_NAME_CAP
                if extra > 0:
                    shown += f" (+{extra} more)"
                avail = f" Available skills: {shown}."
            return (
                f"No skills matched '{query}'.{avail} Try broader words, or proceed "
                "without a skill — not every task has one."
            )

        lines = [f"{len(hits)} matching skill(s) for '{query}':", ""]
        for s in hits:
            desc = (s.description or "").strip().replace("\n", " ")
            if len(desc) > 200:
                desc = desc[:200] + "…"
            lines.append(f"• {s.name} — {desc}")
            wtu = (s.when_to_use or "").strip().replace("\n", " ")
            if wtu:
                lines.append(f"    when: {wtu}")
        lines.append("")
        lines.append(
            'Load one with skill(skill="<name>") and follow its instructions before '
            "doing other work on the task."
        )
        return "\n".join(lines)

    return StructuredTool.from_function(
        coroutine=_search_skills,
        name="search_skills",
        description=(
            "Find the skill that fits the task. The 'Skill map' block of the user "
            "message lists the skill names available to you but not what they do — "
            "search here by intent to get the matching names with descriptions, then "
            "load the right one with the skill tool. Search before concluding no skill "
            "applies; if a map name is already an obvious match, call skill directly."
        ),
        args_schema=SearchSkillsInput,
    )


__all__ = ["build_skill_tool", "build_skill_search_tool", "expand_slash_command"]
