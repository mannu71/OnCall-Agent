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

from typing import Any, Awaitable, Callable, Dict, List, NamedTuple, Optional, Tuple

from app.core.privacy.redact import redact


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
        from app.core.concurrency.thread_pools import run_in_aws_pool

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
        # Token unusable (expired OR otherwise invalid/unrecognized) → creds not
        # verified. NOT AccessDenied — that means the token is VALID but lacks a
        # permission, which is a real (answerable) result, not a creds failure.
        _bad = {
            "ExpiredToken", "ExpiredTokenException",
            "InvalidClientTokenId", "UnrecognizedClientException",
            "InvalidToken", "SignatureDoesNotMatch", "InvalidAccessKeyId",
        }
        if _ec in _bad or any(b in str(_sts_err) for b in ("ExpiredToken", "InvalidClientTokenId")):
            logger_instance.warning(
                "ReactStrategy: %s AWS credentials not verified (%s) (exec=%s)",
                label, _ec or "unknown", execution_id, extra={"execution_id": execution_id},
            )
            return True
        return False
    except Exception as _sts_probe_err:  # noqa: BLE001 — a probe failure is never fatal
        logger_instance.debug(
            "ReactStrategy: %s STS credential pre-flight skipped (%s)",
            label, redact(str(_sts_probe_err)), extra={"execution_id": execution_id},
        )
        return False


def _is_aws_server(name: str) -> bool:
    n = (name or "").lower()
    return "cloudwatch" in n or n.startswith("aws") or "aws-" in n or "aws_" in n


def _aws_creds_from_env(env: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Optional[str]]:
    """Extract boto-style creds + region from an MCP server's ``env`` dict.

    Mirrors exactly how the server's subprocess resolves credentials: an explicit
    ``AWS_PROFILE`` (preferred) or ``AWS_ACCESS_KEY_ID``/``SECRET``/``SESSION``
    keys, else nothing (``{}`` → the default boto3 chain, same as the subprocess
    with no AWS env set). Returns ``(creds, region)`` where an empty ``creds``
    means "probe the default chain".
    """
    env = env or {}
    creds: Dict[str, Any] = {}
    if env.get("AWS_PROFILE"):
        creds["aws_profile"] = env["AWS_PROFILE"]
    elif env.get("AWS_ACCESS_KEY_ID"):
        creds["access_key_id"] = env["AWS_ACCESS_KEY_ID"]
        creds["secret_access_key"] = env.get("AWS_SECRET_ACCESS_KEY", "")
        if env.get("AWS_SESSION_TOKEN"):
            creds["session_token"] = env["AWS_SESSION_TOKEN"]
    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    return creds, region


async def _probe_aws_creds(
    creds: Dict[str, Any], region: Optional[str], *,
    label: str, execution_id: Optional[str], logger_instance: Any,
) -> Optional[str]:
    """Probe one concrete AWS credential set. Returns a refresh message on a
    CONFIRMED expiry, else None (inconclusive never blocks the turn).

    ``creds`` may be empty (→ default boto3 chain). ``label`` names the backend
    for the user-facing message so a failure points at the right thing to fix.
    """
    import os

    region = region or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    if await _sts_expired(
        creds, region,
        logger_instance=logger_instance, execution_id=execution_id, label=label,
    ):
        where = creds.get("aws_profile")
        hint = (
            f"refresh it (e.g. `aws sso login --profile {where}`)"
            if where else
            "refresh the default AWS session (e.g. `aws sso login`)"
        )
        return (
            f"AWS credentials for {label} are expired, so live data cannot be "
            f"queried. No answer was generated to avoid guessing — {hint}, or set a "
            f"valid profile/keys in the configuration, and retry."
        )
    return None


# ---- Pluggable per-provider MCP credential verifiers ------------------------
# The barrier already verifies EVERY wired server generically BY CONNECTION: a
# provider whose connect authenticates (ADO PAT, Postgres DSN, GitHub token, …)
# fails the barrier when its creds are bad, and the gate reports it — no
# per-provider code, works for any server the user wires. A verifier below is
# needed ONLY for a provider whose CONNECTION can succeed while its downstream
# creds are stale, so the failure would otherwise surface mid-run instead of at
# the gate (AWS STS: token valid at the MCP handshake, then 401s on the first
# API call). Each verifier probes the SAME identity the server's subprocess
# uses, resolved from that server's own ``env``. Support another such provider
# by appending ONE entry — nothing in the gate is hardcoded to a server name.
class _McpCredVerifier(NamedTuple):
    name: str                                                       # provider id (dedup + logs)
    matches: Callable[[str], bool]                                  # server-name predicate
    extract: Callable[[Optional[Dict[str, Any]]], Tuple[Dict[str, Any], Optional[str]]]  # env → (creds, region)
    probe: Callable[..., Awaitable[Optional[str]]]                  # (creds, region, *, label, …) → msg|None


_MCP_CRED_VERIFIERS: List[_McpCredVerifier] = [
    _McpCredVerifier("aws", _is_aws_server, _aws_creds_from_env, _probe_aws_creds),
]


async def collect_backend_degradations(
    *,
    wired_mcp_servers: List[str],
    execution_id: Optional[str],
    logger_instance: Any,
) -> List[str]:
    """Pre-LLM credential probe that DEGRADES (never aborts) on stale creds.

    Returns a list of agent-facing degrade notes for any wired backend whose
    credentials are confirmed dead — the caller folds these into the turn's
    ``[System notice]`` so the model knows what's unavailable and picks tools
    accordingly. The model ALWAYS runs with the tools that ARE available; it is
    told explicitly what is not, rather than the whole turn being short-circuited
    (which is how an expired AWS session used to block a pure-database question).

    Scope note — this handles only the one case the generic machinery misses: a
    provider whose MCP CONNECTION can succeed while its downstream creds are stale
    (AWS STS: token valid at the handshake, then 401s on the first API call), so
    the failure would otherwise surface mid-run. Such providers register a
    :class:`_McpCredVerifier` (currently just AWS) that probes the SAME identity
    the server's subprocess uses, resolved from that server's own ``env``. Every
    other failure mode already degrades on its own:

    * A dead MCP connection is reported by the barrier (see the caller's
      ``_mcp_barrier_failed`` fold) — not handled here.
    * The native CloudWatch node is probed and stripped-with-a-note directly in
      :func:`assemble_base_tools`; re-probing it here would just duplicate that.
    * A provider whose connect authenticates (ADO PAT, Postgres DSN, …) fails the
      barrier when its creds are bad.

    A probe error is inconclusive and never yields a note.
    """
    notes: List[str] = []

    # Deep credential verification via the provider-verifier registry. Probe the
    # SAME creds each backend will actually use, deduped by (provider, creds,
    # region) so two servers sharing an identity probe once. A probe error is
    # inconclusive and yields no note.
    probed_signatures: set = set()

    async def _run(verifier: _McpCredVerifier, creds: Dict[str, Any],
                   region: Optional[str], label: str) -> Optional[str]:
        sig = (verifier.name, tuple(sorted((creds or {}).items())), region or "")
        if sig in probed_signatures:
            return None
        probed_signatures.add(sig)
        try:
            return await verifier.probe(
                creds, region, label=label,
                execution_id=execution_id, logger_instance=logger_instance,
            )
        except Exception as _probe_err:  # noqa: BLE001 — inconclusive never blocks
            logger_instance.debug(
                "ReactStrategy: pre-LLM %s probe for %s skipped (%s)",
                verifier.name, label, redact(str(_probe_err)),
                extra={"execution_id": execution_id},
            )
            return None

    # Every wired MCP server — the FIRST verifier that matches probes the
    # server's own env creds (the exact identity its subprocess runs with),
    # looked up from the MCP settings. A server matching no verifier is already
    # verified by its connection (the barrier) and needs nothing here.
    if any(any(v.matches(s) for v in _MCP_CRED_VERIFIERS) for s in wired_mcp_servers):
        try:
            from app.infrastructure.persistence import mcp_config_repository
        except Exception:  # noqa: BLE001 — no repo (tests) → skip deeper probe
            mcp_config_repository = None  # type: ignore[assignment]
        for name in wired_mcp_servers:
            verifier = next((v for v in _MCP_CRED_VERIFIERS if v.matches(name)), None)
            if verifier is None:
                continue  # connection was the verification
            env: Dict[str, Any] = {}
            if mcp_config_repository is not None:
                try:
                    db = await mcp_config_repository.get_by_name(name)
                    env = (db or {}).get("env") or {}
                except Exception as _cfg_err:  # noqa: BLE001 — inconclusive never blocks
                    logger_instance.debug(
                        "ReactStrategy: pre-LLM gate could not load env for '%s' (%s)",
                        name, redact(str(_cfg_err)), extra={"execution_id": execution_id},
                    )
                    continue
            creds, region = verifier.extract(env)
            msg = await _run(verifier, creds, region, f"the '{name}' MCP server")
            if msg:
                # `msg` is the abort-phrased probe result ("No answer was
                # generated…"); in the degrade path an answer WILL be produced,
                # so emit a self-contained note instead of embedding it.
                where = creds.get("aws_profile")
                hint = (
                    f"refresh with `aws sso login --profile {where}`"
                    if where else
                    "refresh with `aws sso login` or update the server's env"
                )
                notes.append(
                    f"The '{name}' MCP server's {verifier.name.upper()} credentials are "
                    f"expired — its tools may fail this turn ({hint})."
                )

    return notes


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
    from app.harness.tool_setup import setup_tools

    tools = await setup_tools(tools_config, mcp_manager, execution_id)
    degraded: List[str] = []

    # Query-aware gate: a purely conversational turn (e.g. "Hi") needs no logs, so
    # skip binding CloudWatch tools entirely — not a degradation, just nothing to do.
    from app.core.quality.intent import is_conversational
    if cloudwatch_config and is_conversational(user_query):
        logger_instance.info(
            "ReactStrategy: conversational turn — skipping CloudWatch tool binding (exec=%s)",
            execution_id,
            extra={"execution_id": execution_id},
        )
        cloudwatch_config = None

    if cloudwatch_config:
        try:
            from app.core.aws.aws_credentials import resolve_aws_credentials
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
        try:
            # Native codegraph C engine, driven in-process over stdio (no MCP-server
            # registration). Tools come out as codegraph__<tool> and pass through
            # tool disclosure below like any other MCP tool set. The generic
            # repo_grep/repo_read_file/repo_list_files tools ride alongside for
            # text search, file reads, and hashline-anchored edits.
            from app.workflow.tools.codegraph_tools import build_codegraph_tools
            from app.workflow.tools.repo_file_tools import build_repo_file_tools

            tools.extend(
                await build_codegraph_tools(
                    mcp_manager, repos=code_analyzer_config.get("repos")
                )
            )
            tools.extend(build_repo_file_tools(repos=code_analyzer_config.get("repos")))
            logger_instance.info(
                "ReactStrategy: code-analyzer backend=codegraph",
                extra={"execution_id": execution_id},
            )
        except Exception as _cr_err:
            logger_instance.warning(
                "ReactStrategy: failed to build code-analyzer tools (non-fatal): %s",
                redact(str(_cr_err)),
                extra={"execution_id": execution_id},
            )
            degraded.append(
                "Code-analysis tools failed to initialize this turn."
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
    #
    # tool_exposure_mode="window" (opt-in) replaces the binary defer-everything-
    # or-nothing threshold gate with a per-assembly budget on the non-core tail
    # (see app.harness.tool_exposure.ToolExposureManager) — the accuracy-safe
    # "10-15 active tools" range MCP tool-density research reports. Default
    # "legacy" keeps apply_tool_disclosure's behavior unchanged.
    if tools:
        try:
            from app.config import settings as _settings
            if getattr(_settings, "tool_exposure_mode", "legacy") == "window":
                from app.harness.tool_exposure import ToolExposureManager

                mgr = ToolExposureManager(tools, max_direct=_settings.tool_exposure_max)
                tools = mgr.window(user_query)
                logger_instance.info(
                    "ReactStrategy: tool exposure window — %d core, %d/%d "
                    "rankable tool(s) bound (+bridge=%d)",
                    mgr.core_count, min(mgr.rankable_count, mgr.max_direct),
                    mgr.rankable_count, len(mgr.bridge_names),
                    extra={"execution_id": execution_id},
                )
            else:
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
            from app.harness.subagent_factory import build_generic_delegate_tool
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
            from app.harness.edit_tools import build_edit_tools
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
            from app.harness.planning_tools import build_planning_tools
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
            from app.harness.subagent_factory import (
                build_subagent_tools,
                build_delegate_parallel_tool,
                build_delegate_batch_tool,
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
            # RAH-style batch fan-out (>5 items via a VFS file). Reads its
            # items_ref through fs_read, so it's only genuinely usable
            # alongside filesystem:true — degrades to a clear error string
            # (not a crash) if called without it.
            _batch_tool = build_delegate_batch_tool(
                llm, _base_snapshot, agent_config, _sub_defs,
                parent_execution_id=execution_id,
            )
            if _batch_tool is not None:
                tools.append(_batch_tool)

            # Strict scoping: a tool that belongs to a squad is reachable ONLY
            # through its delegate_to_<name> child (which captured _base_snapshot
            # above, so it keeps them), never directly by the parent. Strip the
            # squad-glob tools from the parent's own list. Wildcard ('*') defs are
            # skipped — they'd strip everything — and delegate_* tools are never
            # stripped.
            import fnmatch as _fnmatch
            from app.harness.subagent_factory import _coerce_tool_globs as _globs_of
            _squad_globs: List[str] = []
            for _d in _sub_defs:
                if not isinstance(_d, dict):
                    continue
                for _g in (_globs_of(_d.get("tools")) or []):
                    if _g and _g != "*":
                        _squad_globs.append(_g)
            if _squad_globs:
                def _is_squad_tool(_t: Any) -> bool:
                    _nm = getattr(_t, "name", "") or ""
                    if _nm.startswith("delegate_"):
                        return False
                    return any(_fnmatch.fnmatch(_nm, _g) for _g in _squad_globs)

                _stripped = [getattr(_t, "name", "") for _t in tools if _is_squad_tool(_t)]
                if _stripped:
                    tools[:] = [_t for _t in tools if not _is_squad_tool(_t)]
                    logger_instance.info(
                        "ReactStrategy: strict squad scoping removed %d tool(s) from "
                        "the main agent (reachable only via delegate_to_<name>): %s",
                        len(_stripped), _stripped,
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
                from app.harness.verify_tools import build_verify_tool
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

    # Automatic result offload (profile-gated on filesystem, off by default).
    # Applied LAST so it wraps every tool assembled above (base + code/edit +
    # planning + vfs + subagents + sandbox + verify) in one pass. Requires a
    # bound VFS session — already bound above when filesystem is on.
    if _flags.get("filesystem"):
        try:
            from app.config import settings
            from app.harness.tool_offload import wrap_tools_with_offload
            tools[:] = wrap_tools_with_offload(
                tools, execution_id, settings.tool_result_offload_chars,
            )
        except Exception as _oe:  # noqa: BLE001
            logger_instance.warning("ReactStrategy: tool offload skipped (%s)", _oe)

    return tools
