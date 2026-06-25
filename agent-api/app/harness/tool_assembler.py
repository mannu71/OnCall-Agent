"""Tool assembly — the harness's "action space" builder.

Assembles the agent's base tool set from the workflow's tool/CloudWatch/code/DB
config: MCP tools, CloudWatch tools (with a cheap STS credential pre-flight that
aborts before the LLM is invoked if the session token is expired), code-crawler
tools, DB-schema lookup tools, then prunes the result with the relevance router.

Extracted verbatim (behaviour-preserving) from ``ReactStrategy.execute``. The
``_expired_creds_msg`` is returned to the caller rather than early-returning from
here, so the strategy keeps ownership of the user-facing result envelope.

Subagent-delegate and edit tools are added by the caller *after* the LLM is
built (they need the model), so they are intentionally NOT part of this base set.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.redact import redact


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
) -> Tuple[List[Any], Optional[str]]:
    """Build the base tool set. Returns ``(tools, expired_creds_msg)``.

    When ``expired_creds_msg`` is non-None the caller should short-circuit with a
    credential-refresh message instead of invoking the LLM.
    """
    from app.workflow.strategies.react.tool_setup import setup_tools

    tools = await setup_tools(tools_config, mcp_manager, execution_id)

    expired_creds_msg: Optional[str] = None

    # Query-aware gate: a purely conversational turn (e.g. "Hi") needs no logs, so
    # skip binding CloudWatch tools — this also skips the STS credential pre-flight
    # that would otherwise abort the whole turn on an expired token. Real queries
    # still bind the tools (and still get the expired-creds guard).
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

            # Pre-flight: validate credentials before invoking the LLM.
            # A cheap STS call costs nothing vs. a full agent loop.
            try:
                import boto3
                from botocore.exceptions import ClientError as _BotoClientError
                from app.core.thread_pools import run_in_aws_pool

                _sts_kwargs: Dict[str, Any] = {"region_name": cw_region}
                if cw_creds.get("aws_profile"):
                    _sts_session = boto3.Session(
                        profile_name=cw_creds["aws_profile"], region_name=cw_region
                    )
                    _sts_client = _sts_session.client("sts")
                else:
                    if cw_creds.get("access_key_id"):
                        _sts_kwargs["aws_access_key_id"] = cw_creds["access_key_id"]
                        _sts_kwargs["aws_secret_access_key"] = cw_creds.get("secret_access_key", "")
                        if cw_creds.get("session_token"):
                            _sts_kwargs["aws_session_token"] = cw_creds["session_token"]
                    _sts_client = boto3.client("sts", **_sts_kwargs)
                await run_in_aws_pool(_sts_client.get_caller_identity)
            except _BotoClientError as _sts_err:
                _ec = _sts_err.response.get("Error", {}).get("Code", "")
                if _ec == "ExpiredTokenException" or "ExpiredToken" in str(_sts_err):
                    expired_creds_msg = (
                        "AWS credentials are expired. Please refresh your AWS session token "
                        "in the LLM configuration settings and retry the workflow."
                    )
                    logger_instance.warning(
                        "ReactStrategy: AWS credentials expired — aborting before LLM invocation (exec=%s)",
                        execution_id,
                        extra={"execution_id": execution_id},
                    )
            except Exception as _sts_probe_err:
                logger_instance.debug(
                    "ReactStrategy: STS credential pre-flight skipped (%s)",
                    redact(str(_sts_probe_err)),
                    extra={"execution_id": execution_id},
                )

            if not expired_creds_msg:
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

    if expired_creds_msg:
        # Short-circuit: caller will return the refresh message. Tools built so
        # far are irrelevant.
        return tools, expired_creds_msg

    if code_analyzer_config:
        try:
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
                "ReactStrategy: failed to build Crawler tools (non-fatal): %s",
                redact(str(_cr_err)),
                extra={"execution_id": execution_id},
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

    return tools, expired_creds_msg


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
       code-edit tool — unchanged behaviour.
    2. **Deep-agent capabilities** (gated by the agent profile flags resolved from
       ``agent_config``): planning (write_todos), virtual filesystem (fs_*), and
       generalized named subagents (delegate_to_<name>). All OFF by default, so an
       agent that opts into none of them gets the exact previous tool set.

    Returns the same list for call-site convenience.
    """
    if code_analyzer_config:
        # On-demand subagent delegation (depth-1). The sub-agent is built from a
        # snapshot WITHOUT this tool, so it cannot fan out further.
        try:
            from app.workflow.strategies.react.subagent import build_delegate_tool
            tools.append(
                build_delegate_tool(
                    llm, list(tools), agent_config, parent_execution_id=execution_id,
                )
            )
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

    # On the deepagents harness, planning (write_todos via TodoListMiddleware) and
    # the filesystem are deepagents built-ins, so we skip our overlapping fs_*/
    # write_todos tools (avoids duplicate tools + a write_todos name clash). The
    # legacy harness still gets them here.
    try:
        from app.harness.spec_factory import resolve_harness
        _deepagents = resolve_harness(agent_config) == "deepagents"
    except Exception:  # noqa: BLE001
        _deepagents = False

    if _flags.get("planning") and not _deepagents:
        try:
            from app.workflow.strategies.react.planning_tools import build_planning_tools
            tools.extend(build_planning_tools(execution_id))
        except Exception as _pe:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: planning tools skipped (%s)", _pe)

    # Scratch filesystem (legacy harness only — deepagents provides its own).
    if not _deepagents:
        try:
            from app.core.vfs import build_vfs_tools, bind_session
            bind_session(execution_id)
            tools.extend(build_vfs_tools(execution_id))
        except Exception as _fe:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: vfs tools skipped (%s)", _fe)

    if _flags.get("subagents"):
        try:
            from app.workflow.strategies.react.subagent_factory import build_subagent_tools
            tools.extend(
                build_subagent_tools(
                    llm, list(tools), agent_config, _flags["subagents"],
                    parent_execution_id=execution_id,
                )
            )
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
