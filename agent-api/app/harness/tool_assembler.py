"""Tool assembly — the harness's "action space" builder.

Assembles the agent's base tool set from the workflow's tool/CloudWatch/code/DB
config: MCP tools, CloudWatch tools, code-crawler/codegraph tools, DB-schema
lookup tools, then prunes the result with progressive tool disclosure.

Every builder follows ONE uniform pattern: try to build it, and if it fails
(missing config, expired credentials, a dead connection, whatever), skip that
builder, note *why* in ``degraded``, and keep going — never abort the whole
turn over one tool family. This mirrors the reference ReAct implementation
(no builder-specific preflight kills a turn there either; a failed tool just
returns an error the model adapts to). The turn is only short-circuited before
the LLM is invoked when there is truly nothing the agent could do: zero tools
built at all, or the model's own credentials are confirmed dead (checked via
the same cheap STS probe, and only when something already degraded — no need
to pay for an extra AWS round-trip on the common, everything-built-fine path).

Extracted (and later generalized) from ``ReactStrategy.execute``. The
``expired_creds_msg`` is returned to the caller rather than early-returning
from here, so the strategy keeps ownership of the user-facing result envelope.

Subagent-delegate and edit tools are added by the caller *after* the LLM is
built (they need the model), so they are intentionally NOT part of this base set.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.redact import redact


async def _sts_expired(
    creds: Dict[str, Any],
    region: str,
    *,
    logger_instance: Any,
    execution_id: Optional[str],
    label: str,
) -> bool:
    """Cheap ``get_caller_identity`` probe. True ONLY on a confirmed
    ``ExpiredTokenException`` — any other probe failure (network blip, no
    creds configured, missing botocore) is "cannot confirm expiry", never
    treated as a reason to abort a turn.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError as _BotoClientError
        from app.core.thread_pools import run_in_aws_pool

        _sts_kwargs: Dict[str, Any] = {"region_name": region}
        if creds.get("aws_profile"):
            _sts_session = boto3.Session(
                profile_name=creds["aws_profile"], region_name=region
            )
            _sts_client = _sts_session.client("sts")
        else:
            if creds.get("access_key_id"):
                _sts_kwargs["aws_access_key_id"] = creds["access_key_id"]
                _sts_kwargs["aws_secret_access_key"] = creds.get("secret_access_key", "")
                if creds.get("session_token"):
                    _sts_kwargs["aws_session_token"] = creds["session_token"]
            _sts_client = boto3.client("sts", **_sts_kwargs)
        await run_in_aws_pool(_sts_client.get_caller_identity)
        return False
    except _BotoClientError as _sts_err:  # noqa: F821 — bound above unless import itself failed
        _ec = _sts_err.response.get("Error", {}).get("Code", "")
        if _ec == "ExpiredTokenException" or "ExpiredToken" in str(_sts_err):
            logger_instance.warning(
                "ReactStrategy: %s AWS credentials expired (exec=%s)",
                label, execution_id, extra={"execution_id": execution_id},
            )
            return True
        return False
    except Exception as _sts_probe_err:  # noqa: BLE001 — a probe failure is never fatal
        logger_instance.debug(
            "ReactStrategy: %s STS credential pre-flight skipped (%s)",
            label, redact(str(_sts_probe_err)), extra={"execution_id": execution_id},
        )
        return False


async def assemble_base_tools(
    *,
    tools_config: List[Dict[str, Any]],
    mcp_manager: Any,
    execution_id: Optional[str],
    cloudwatch_config: Optional[Dict[str, Any]],
    code_analyzer_config: Optional[Dict[str, Any]],
    db_server_map: Dict[str, Any],
    user_query: str,
    logger_instance: Any,
    crawler_model_id: Optional[str] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], Optional[str], List[str]]:
    """Build the base tool set. Returns ``(tools, expired_creds_msg, degraded)``.

    ``degraded`` lists short, agent-facing notes for any builder that could not
    run this turn (e.g. "CloudWatch tools are unavailable — AWS credentials
    expired"). The caller should prepend these to the turn's query so the model
    knows what's missing instead of discovering it by a tool call failing.

    ``expired_creds_msg`` is non-None only when NOTHING could be built, or the
    model's own credentials are confirmed dead — the caller should short-circuit
    with a credential-refresh message instead of invoking the LLM in that case.
    ``llm_config`` (the resolved main-model config, same shape as CloudWatch's
    resolved credentials — see ``app.workflow.llm_config.LLMConfig.to_dict``)
    is optional so existing/test callers that don't have it degrade gracefully
    too: without it, a dead LLM credential surfaces at the first model call
    instead of pre-flight, same as before this generic-degrade fix existed.
    """
    from app.workflow.strategies.react.tool_setup import setup_tools

    tools = await setup_tools(tools_config, mcp_manager, execution_id)
    degraded: List[str] = []

    # Query-aware gate: a purely conversational turn (e.g. "Hi") needs no logs, so
    # skip binding CloudWatch tools entirely — not a degradation, just nothing to do.
    from app.core.intent import is_conversational
    if cloudwatch_config and is_conversational(user_query):
        logger_instance.info(
            "ReactStrategy: conversational turn — skipping CloudWatch tool binding (exec=%s)",
            execution_id,
            extra={"execution_id": execution_id},
        )
        cloudwatch_config = None

    if cloudwatch_config:
        try:
            from app.core.aws_credentials import resolve_aws_credentials
            from app.workflow.tools.cloudwatch_agent_tools import build_cloudwatch_agent_tools

            cw_creds, cw_region = await resolve_aws_credentials(
                aws_profile=cloudwatch_config.get("aws_profile"),
                aws_region=cloudwatch_config.get("aws_region", "us-east-1"),
            )

            if await _sts_expired(
                cw_creds, cw_region,
                logger_instance=logger_instance, execution_id=execution_id, label="CloudWatch",
            ):
                degraded.append(
                    "CloudWatch/log tools are unavailable this turn (AWS credentials "
                    "expired — refresh them in the LLM configuration settings)."
                )
            else:
                tools.extend(
                    build_cloudwatch_agent_tools(
                        region=cw_region,
                        credentials=cw_creds,
                        log_groups=cloudwatch_config.get("log_groups"),
                        severity_excludes=cloudwatch_config.get("severity_excludes"),
                    )
                )
        except Exception as _cw_err:
            logger_instance.warning(
                "ReactStrategy: failed to build CloudWatch tools (non-fatal): %s",
                redact(str(_cw_err)),
                extra={"execution_id": execution_id},
            )
            degraded.append("CloudWatch tools failed to initialize this turn (see server logs).")

    if code_analyzer_config:
        backend = (code_analyzer_config.get("backend") or "code_crawler")
        try:
            if backend == "codegraph":
                # Native C engine, driven in-process over stdio (no MCP-server
                # registration). Tools come out as codegraph__<tool> and pass
                # through tool disclosure below like any other MCP tool set.
                from app.workflow.tools.codegraph_tools import build_codegraph_tools

                tools.extend(
                    await build_codegraph_tools(
                        mcp_manager, repos=code_analyzer_config.get("repos")
                    )
                )
                logger_instance.info(
                    "ReactStrategy: code-analyzer backend=codegraph",
                    extra={"execution_id": execution_id},
                )
            else:
                from app.workflow.tools.code_analyzer_tools import build_crawler_tools

                tools.extend(
                    build_crawler_tools(
                        repos=code_analyzer_config.get("repos"),
                        default_model_id=crawler_model_id,
                    )
                )
                if crawler_model_id:
                    logger_instance.info(
                        "ReactStrategy: crawler tools using wired model=%s",
                        crawler_model_id,
                        extra={"execution_id": execution_id},
                    )
        except Exception as _cr_err:
            logger_instance.warning(
                "ReactStrategy: failed to build code-analyzer tools (backend=%s, non-fatal): %s",
                backend,
                redact(str(_cr_err)),
                extra={"execution_id": execution_id},
            )
            degraded.append(
                f"Code-analysis tools (backend={backend}) failed to initialize this turn."
            )

    # Bounded, cached DB schema lookup tools — added only when a database node is
    # wired. Lets the agent find the right table with a small filtered lookup
    # instead of dumping the whole schema into context.
    if db_server_map and mcp_manager:
        try:
            from app.workflow.tools.db_schema_tools import build_db_schema_tools

            tools.extend(build_db_schema_tools(db_server_map, mcp_manager))
        except Exception as _db_err:
            logger_instance.warning(
                "ReactStrategy: failed to build DB schema tools (non-fatal): %s",
                redact(str(_db_err)),
                extra={"execution_id": execution_id},
            )
            degraded.append("Database schema lookup tools failed to initialize this turn.")

    # ── Generic go/no-go check — runs once, after every builder, never per-tool ──
    # special logic. Short-circuit before the LLM only when there is truly
    # nothing the agent could do with this turn.
    expired_creds_msg: Optional[str] = None
    if not tools:
        expired_creds_msg = (
            "No tools could be initialized for this turn"
            + (f": {' '.join(degraded)}" if degraded else ".")
        )
    elif degraded and llm_config is not None:
        # Only worth the extra STS round-trip when something already failed —
        # this tells us whether it's an account-wide credential problem (abort
        # now with a clear message, since nothing else will work either) or
        # narrowly scoped to the tool(s) that failed (degrade and continue with
        # what's available).
        llm_creds = {
            "aws_profile":       llm_config.get("aws_profile"),
            "access_key_id":     llm_config.get("access_key_id"),
            "secret_access_key": llm_config.get("secret_access_key"),
            "session_token":     llm_config.get("session_token"),
        }
        llm_region = llm_config.get("region") or "us-east-1"
        if await _sts_expired(
            llm_creds, llm_region,
            logger_instance=logger_instance, execution_id=execution_id, label="Model",
        ):
            expired_creds_msg = (
                "AWS credentials are expired. Please refresh your AWS session token "
                "in the LLM configuration settings and retry the workflow."
            )

    if expired_creds_msg:
        # Short-circuit: caller will return the refresh message. Tools built so
        # far are irrelevant.
        return tools, expired_creds_msg, degraded

    # Progressive tool disclosure: when an MCP server exposes a large open-ended
    # tool set, defer it behind search_tools/call_tool so the AGENT discovers and
    # invokes tools on demand — instead of keyword-pruning the catalog (which
    # silently dropped tools the agent needed). Core families + operator-pinned
    # tools stay directly bound; small toolsets pass through unchanged. This
    # supersedes the old tool_router.filter_tools prune.
    if tools:
        try:
            from app.harness.tool_disclosure import apply_tool_disclosure

            tools = apply_tool_disclosure(tools, logger_instance=logger_instance)
        except Exception as _td_err:
            logger_instance.warning(
                "ReactStrategy: tool disclosure skipped (non-fatal): %s",
                redact(str(_td_err)),
                extra={"execution_id": execution_id},
            )

    return tools, expired_creds_msg, degraded


def add_extension_tools(
    *,
    tools: List[Any],
    llm: Any,
    agent_config: Dict[str, Any],
    code_analyzer_config: Optional[Dict[str, Any]],
    execution_id: Optional[str],
    logger_instance: Any,
) -> List[Any]:
    """Append the LLM-dependent extension tools (delegate + edit), in place.

    Two groups are appended here:

    1. **Code path** (only when code tools are present): the depth-1
       ``delegate_investigation`` tool (built from a snapshot of the current
       tools, so it never includes itself or the edit tool) and the gated
       code-edit tool. ``delegate_investigation`` is built by
       ``subagent_factory.build_generic_delegate_tool`` — the same engine as
       every other delegation tool (unified LLM resolution / tool scoping).
    2. **Deep-agent capabilities** (gated by the agent profile flags resolved from
       ``agent_config``): planning (write_todos), virtual filesystem (fs_*), and
       generalized named subagents (delegate_to_<name>). All OFF by default, so an
       agent that opts into none of them gets the exact previous tool set.

    Returns the same list for call-site convenience.
    """
    if code_analyzer_config:
        # On-demand subagent delegation (depth-1). The sub-agent is built from a
        # snapshot WITHOUT this tool, so it cannot fan out further. Built on the
        # same subagent_factory engine as the named delegate_to_<name> tools.
        try:
            from app.workflow.strategies.react.subagent_factory import build_generic_delegate_tool
            _generic = build_generic_delegate_tool(
                llm, list(tools), agent_config, parent_execution_id=execution_id,
            )
            if _generic is not None:
                tools.append(_generic)
        except Exception as _de:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: delegate tool skipped (%s)", _de)

        # Code-edit tool (apply fixes). Gated as 'ask' by the permission layer, so
        # every edit needs operator approval.
        try:
            from app.workflow.strategies.react.edit_tools import build_edit_tools
            tools.extend(build_edit_tools())
        except Exception as _ee:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: edit tool skipped (%s)", _ee)

    # ── Deep-agent capabilities (profile-gated, off by default) ───────────────
    try:
        from app.harness.spec_factory import resolve_profile_fields
        _flags = resolve_profile_fields(agent_config)
    except Exception:  # noqa: BLE001
        _flags = {"planning": False, "filesystem": False, "subagents": []}

    if _flags.get("planning"):
        try:
            from app.workflow.strategies.react.planning_tools import build_planning_tools
            tools.extend(build_planning_tools(execution_id))
        except Exception as _pe:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: planning tools skipped (%s)", _pe)

    # VFS tools are gated on the filesystem flag (AgentSpec.filesystem defaults
    # False — see resolve_profile_fields). Previously this flag was ignored and
    # fs_* tools were always added regardless of profile config.
    if _flags.get("filesystem"):
        try:
            from app.core.vfs import build_vfs_tools, bind_session
            bind_session(execution_id)
            tools.extend(build_vfs_tools(execution_id))
        except Exception as _fe:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: vfs tools skipped (%s)", _fe)

    if _flags.get("subagents"):
        try:
            from app.workflow.strategies.react.subagent_factory import (
                build_subagent_tools,
                build_delegate_parallel_tool,
            )
            _sub_defs = _flags["subagents"]
            _base_snapshot = list(tools)  # snapshot before appending delegation tools
            tools.extend(
                build_subagent_tools(
                    llm, _base_snapshot, agent_config, _sub_defs,
                    parent_execution_id=execution_id,
                )
            )
            _parallel = build_delegate_parallel_tool(
                llm, _base_snapshot, agent_config, _sub_defs,
                parent_execution_id=execution_id,
            )
            if _parallel is not None:
                tools.append(_parallel)
        except Exception as _se:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: subagent tools skipped (%s)", _se)

    # Sandboxed shell access (opt-in). Only added when the workflow turns it on
    # AND a sandbox backend is actually configured/available on the host — so a
    # toggle with no SANDBOX_BACKEND is a safe no-op. The run_command tool is
    # gated 'ask' by the policy engine, giving HITL + isolation defense-in-depth.
    if _flags.get("sandbox"):
        try:
            from app.core import sandbox as _sandbox
            if _sandbox.is_enabled():
                import tempfile
                _cwd = tempfile.mkdtemp(prefix=f"sbx-{execution_id or 'run'}-")
                _cmd_tool = _sandbox.build_sandboxed_command_tool(_cwd)
                if _cmd_tool is not None:
                    tools.append(_cmd_tool)
                    logger_instance.info(
                        "ReactStrategy: sandboxed run_command tool enabled (cwd=%s)", _cwd
                    )
            else:
                logger_instance.info(
                    "ReactStrategy: sandbox requested but no backend configured "
                    "(set SANDBOX_BACKEND) — skipping run_command tool"
                )
        except Exception as _sbe:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: sandbox tool skipped (%s)", _sbe)

    # Verify tool (edit→verify→fix loop). Added only when a verify_command is
    # configured AND a sandbox backend is available — so a command with no
    # SANDBOX_BACKEND is a safe no-op. Runs the fixed command against the real
    # repo dir; gated 'ask' by the policy engine (it executes code).
    _verify_cmd = _flags.get("verify_command")
    if _verify_cmd:
        try:
            from app.core import sandbox as _sandbox
            if _sandbox.is_enabled():
                from app.workflow.strategies.react.verify_tools import build_verify_tool
                _verify_tool = build_verify_tool(
                    _verify_cmd,
                    image=_flags.get("verify_image"),
                    timeout=_flags.get("verify_timeout"),
                )
                if _verify_tool is not None:
                    tools.append(_verify_tool)
                    logger_instance.info(
                        "ReactStrategy: run_verify tool enabled (command=%s)", _verify_cmd
                    )
            else:
                logger_instance.info(
                    "ReactStrategy: verify_command set but no sandbox backend configured "
                    "(set SANDBOX_BACKEND) — skipping run_verify tool"
                )
        except Exception as _vte:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: verify tool skipped (%s)", _vte)

    return tools
