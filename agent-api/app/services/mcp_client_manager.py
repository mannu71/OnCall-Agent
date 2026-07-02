"""MCP Client Manager for connecting to and executing tools on MCP servers.

Security hardening:
- **Credential sanitization** — strips API keys / bearer tokens from tool
  responses before they are returned to the LLM (prevents key exfiltration
  via trajectory logging).
- **Injection detection in descriptions** — scans tool description text for
  prompt-injection patterns at connect time so poisoned MCP servers cannot
  hijack the agent's reasoning.
- **SSRF guard** — already present on tool *arguments*, unchanged.
"""
import asyncio
import logging
import os
import os.path
import glob
from contextlib import AsyncExitStack
from typing import Dict, List, Any, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.core.redact import redact as _redact_credentials
from app.core.retry import with_retry
from app.core.security import check_ssrf, SSRFError, scan_injection, InjectionError

logger = logging.getLogger(__name__)

# Default certificate directory in container
CERTS_DIR = '/app/data/certs'

# ─────────────────────────────────────────────────────────────────────────────
# Credential sanitization
# Strips common credential patterns from tool response text before the LLM
# sees the output, preventing key material leaking into trajectory logs or
# the model's context window.
#
# Credential patterns are consolidated in app.core.redact (imported above as
# _redact_credentials) so they are maintained in one place. Pseudonymization
# of PII is applied as a second pass below via the active session vault.
# ─────────────────────────────────────────────────────────────────────────────


def _sanitize_credential(text: str) -> str:
    """Scrub credentials, then pseudonymize PII, before MCP output reaches the LLM.

    Credentials are redacted one-way via core.redact; PII (email/phone/SSN/
    card/IP/account-id) is swapped for reversible placeholders via the active
    session vault so the final answer can be re-hydrated. Pseudonymization is a
    no-op when disabled or when no session is bound.
    """
    text = _redact_credentials(text)
    try:
        from app.core.privacy import pseudonymize_active
        text = pseudonymize_active(text)
    except Exception:  # noqa: BLE001 — privacy must never break tool output
        pass
    return text


def _sanitize_tool_content(content: Any) -> Any:
    """Recursively sanitize credential-shaped strings inside *content*.

    Handles the MCP result shapes: plain str, list-of-content-blocks (dicts),
    and nested lists.
    """
    if isinstance(content, str):
        return _sanitize_credential(content)
    if isinstance(content, list):
        sanitized: list[Any] = []
        for item in content:
            if isinstance(item, dict):
                item = dict(item)  # shallow copy
                if "text" in item and isinstance(item["text"], str):
                    item["text"] = _sanitize_credential(item["text"])
            elif isinstance(item, str):
                item = _sanitize_credential(item)
            sanitized.append(item)
        return sanitized
    return content


def find_certificate_file() -> Optional[str]:
    """Find a certificate file in the certs directory.
    
    Returns the path to the first .pem or .crt file found.
    """
    if not os.path.exists(CERTS_DIR):
        logger.debug("Certificate directory %s does not exist", CERTS_DIR)
        return None
    
    for ext in ['*.pem', '*.crt', '*.cer']:
        files = glob.glob(os.path.join(CERTS_DIR, ext))
        if files:
            logger.info("Found certificate file: %s", files[0])
            return files[0]
    
    logger.debug("No certificate files found in %s", CERTS_DIR)
    return None


def _transform_cert_path(custom_env: Dict[str, str]) -> None:
    """Transform Windows certificate paths to container paths in-place."""
    if 'NODE_EXTRA_CA_CERTS' not in custom_env:
        return
    cert_path = custom_env['NODE_EXTRA_CA_CERTS']
    if not cert_path or (':' not in cert_path and not cert_path.startswith('\\')):
        return
    filename = os.path.basename(cert_path.replace('\\', '/'))
    container_path = f'/app/data/certs/{filename}'
    logger.info("Transforming cert path: %s -> %s", cert_path, container_path)
    custom_env['NODE_EXTRA_CA_CERTS'] = container_path


def _auto_detect_certificate(custom_env: Dict[str, str], args: List[Any]) -> None:
    """Auto-detect and configure certificate for database connections in-place."""
    if 'NODE_EXTRA_CA_CERTS' in custom_env:
        return
    args_str = ' '.join(str(arg) for arg in args) if args else ''
    args_lower = args_str.lower()
    if 'postgres' not in args_lower and 'mysql' not in args_lower and 'sslmode' not in args_lower:
        return
    cert_file = find_certificate_file()
    if cert_file:
        logger.info("Auto-configuring certificate for database connection: %s", cert_file)
        custom_env['NODE_EXTRA_CA_CERTS'] = cert_file


class MCPClientManager:
    """Manages connections to multiple MCP servers."""
    
    def __init__(self):
        self.connections: Dict[str, Dict[str, Any]] = {}
        self.tools: Dict[str, List[str]] = {}  # server_id -> list of tool names
        self.tool_objects: Dict[str, Dict[str, Any]] = {}  # server_id -> {tool_name -> MCP Tool obj}
        self._exit_stacks: Dict[str, AsyncExitStack] = {}  # server_id -> exit stack
        self.last_errors: Dict[str, str] = {}  # server_id -> last connection failure reason
        # server_id -> last config used to connect. Survives a _forget_connection()
        # (unlike self.connections, which is cleared) so a later call can lazily
        # reconnect with the same command/args/env — mirrors the reference client's
        # memoize-and-invalidate model (connectToServer cache + ensureConnectedClient).
        self._server_configs: Dict[str, Dict[str, Any]] = {}
        # server_id -> list of fnmatch patterns restricting which discovered tools
        # are exposed to the agent. Empty/missing = expose all. Lets a node pick a
        # handful of tools (e.g. wit_*) from a server that advertises dozens, so
        # the agent's tool list — and every request's token count — stays small.
        self.tool_filters: Dict[str, List[str]] = {}
        # Per-server call lock. MCP stdio is a single pipe — concurrent call_tool
        # requests interleave their JSON-RPC frames and responses get mismatched,
        # so every call to a given server must be serialized. The model can emit
        # parallel tool calls in one turn; without this they corrupt the session
        # and all hang to timeout.
        self._call_locks: Dict[str, asyncio.Lock] = {}

    def _get_call_lock(self, server_id: str) -> "asyncio.Lock":
        lock = self._call_locks.get(server_id)
        if lock is None:
            lock = asyncio.Lock()
            self._call_locks[server_id] = lock
        return lock

    def set_tool_filter(self, server_id: str, patterns: List[str]) -> None:
        """Restrict which tools from *server_id* are exposed to the agent.

        ``patterns`` are fnmatch-style globs (e.g. ``wit_*``, ``search_code``).
        An empty list clears the filter (all tools exposed).
        """
        clean = [p.strip() for p in patterns if p and p.strip()]
        if clean:
            self.tool_filters[server_id] = clean
        else:
            self.tool_filters.pop(server_id, None)

    def filtered_tool_names(self, server_id: str) -> List[str]:
        """Return the tool names for *server_id* after applying its tool filter.

        With no filter set, returns every discovered tool name unchanged.
        """
        import fnmatch

        all_names = self.tools.get(server_id, [])
        patterns = self.tool_filters.get(server_id)
        if not patterns:
            return all_names
        kept = [n for n in all_names if any(fnmatch.fnmatch(n, p) for p in patterns)]
        if not kept:
            logger.warning(
                "MCP server %s: tool filter %s matched no tools (of %d) — exposing "
                "all to avoid leaving the agent with none",
                server_id, patterns, len(all_names),
            )
            return all_names
        return kept
    
    # Timeout (seconds) for the entire connect + initialize + list_tools sequence.
    # npx must download the package on first run, so allow generous time.
    CONNECT_TIMEOUT = 90

    async def connect_server(self, server_id: str, config: Dict[str, Any]) -> bool:
        """Connect to an MCP server.
        
        Args:
            server_id: Unique identifier for this server
            config: Server configuration with 'command', 'args', 'env'
        
        Returns:
            True if connection successful
        """
        try:
            return await asyncio.wait_for(
                self._connect_server_inner(server_id, config),
                timeout=self.CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self.last_errors[server_id] = (
                f"connection timed out after {self.CONNECT_TIMEOUT}s"
            )
            logger.error(
                "MCP server %s connection timed out after %ss",
                server_id, self.CONNECT_TIMEOUT,
            )
            if server_id in self._exit_stacks:
                try:
                    await self._exit_stacks[server_id].aclose()
                except Exception as cleanup_exc:
                    logger.warning(
                        "MCP server %s cleanup after connect-timeout failed: %s",
                        server_id, cleanup_exc, exc_info=True,
                    )
                finally:
                    self._exit_stacks.pop(server_id, None)
            return False

    async def _connect_server_inner(self, server_id: str, config: Dict[str, Any]) -> bool:
        """Internal connection logic wrapped by connect_server timeout."""
        try:
            logger.info("Connecting to MCP server: %s", server_id)
            
            command = config.get('command')
            args = config.get('args', [])
            raw_env = config.get('env', {}) or {}
            custom_env = {k: v if isinstance(v, str) else str(v) for k, v in raw_env.items()}

            _transform_cert_path(custom_env)
            _auto_detect_certificate(custom_env, args)

            env = {**os.environ, **custom_env} if custom_env else None
            
            if not command:
                raise ValueError(f"Server {server_id} missing 'command' in config")

            # Resolve interpreter commands to an absolute path via PATH rather than
            # hardcoding a location (apt installs npx at /usr/bin, nvm elsewhere).
            # Fail fast with a clear message if the runtime is missing entirely.
            if command in ('npx', 'node', 'npm'):
                import shutil
                resolved = shutil.which(command)
                if not resolved:
                    raise FileNotFoundError(
                        f"'{command}' is not installed in this container — the MCP "
                        f"server '{server_id}' cannot start. Install Node.js in the "
                        f"agent-api image or reconfigure the server to use an "
                        f"available command."
                    )
                command = resolved
            
            logger.info("MCP Config - Command: %s", command)
            logger.info("MCP Config - Args: %s", args)
            logger.info("MCP Config - Env keys: %s", list(env.keys()) if env else 'default')
            
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env
            )
            
            # Use AsyncExitStack for proper async context management
            # This ensures cleanup happens correctly even across task boundaries
            exit_stack = AsyncExitStack()
            self._exit_stacks[server_id] = exit_stack
            
            # Enter the stdio context using the exit stack
            read, write = await exit_stack.enter_async_context(stdio_client(server_params))
            logger.info("Stdio streams created for %s", server_id)
            
            # Enter the session context using the exit stack
            session = await exit_stack.enter_async_context(ClientSession(read, write))
            logger.info("Session created for %s, initializing...", server_id)
            
            await session.initialize()
            logger.info("Session initialized for %s, listing tools...", server_id)
            
            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]

            # ── Injection detection in tool descriptions ─────────────────────
            # Scan description text for prompt-injection patterns at connect time.
            # A poisoned MCP server that embeds "ignore previous instructions" in
            # a tool description would otherwise silently hijack the agent.
            for tool in tools_result.tools:
                description = getattr(tool, "description", None) or ""
                if description:
                    try:
                        scan_injection(description)
                    except InjectionError as inj_err:
                        logger.warning(
                            "MCP server '%s': tool '%s' description failed injection "
                            "scan — tool will be registered but description is suspicious. "
                            "Error: %s",
                            server_id, tool.name, inj_err,
                        )
                        # Continue registration with a sanitized placeholder description
                        # so the agent can still use the tool but won't be misled.
                        try:
                            tool.description = (
                                f"[Description suppressed — injection pattern detected] "
                                f"Original tool: {tool.name}"
                            )
                        except AttributeError:
                            pass  # Frozen dataclass — leave as-is

            self.connections[server_id] = {
                'config': config,
                'session': session,
                'read': read,
                'write': write,
            }
            self._server_configs[server_id] = config
            self.tools[server_id] = tool_names
            # Store full tool objects for schema/description access by LangChain adapter
            self.tool_objects[server_id] = {tool.name: tool for tool in tools_result.tools}

            logger.info("Connected to %s with %d tools: %s", server_id, len(tool_names), tool_names)
            return True
                
        except Exception as e:
            self.last_errors[server_id] = f"{type(e).__name__}: {e}"
            logger.error("Failed to connect to MCP server %s: %s", server_id, e, exc_info=True)
            logger.error("Config was - command: %s, args: %s", config.get('command'), config.get('args'))
            # Clean up the exit stack if connection failed
            if server_id in self._exit_stacks:
                try:
                    await self._exit_stacks[server_id].aclose()
                except Exception as cleanup_error:
                    logger.debug("Error cleaning up failed connection: %s", cleanup_error)
                finally:
                    del self._exit_stacks[server_id]
            return False
    
    # Default per-tool call timeout in seconds.
    # Callers can override via the ``tool_timeout`` argument to ``execute_tool()``.
    TOOL_TIMEOUT: float = 60.0

    async def execute_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        tool_timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute a tool on an MCP server.

        Args:
            server_id:    Server identifier.
            tool_name:    Name of the tool to execute.
            arguments:    Tool arguments.
            tool_timeout: Per-call timeout in seconds.  When ``None`` the class-
                          level ``TOOL_TIMEOUT`` default applies.  Pass ``0`` to
                          disable the timeout entirely.

        Returns:
            Tool execution result dict with ``success``, ``content``,
            ``isError`` keys.
        """
        # Lazy reconnect (mirrors the reference client's ensureConnectedClient):
        # a prior call may have _forget_connection()'d a dead session (see the
        # BrokenPipe branch below). Reconnecting HERE — in the task that is
        # about to make the call — keeps the exit stack's anyio cancel scope
        # owned by a task that isn't a throwaway subtask. Raises only if we
        # have never connected to this server at all (no remembered config).
        await self._ensure_connected(server_id)

        # SSRF guard: reject any URL-shaped argument that targets a private range.
        # This is a cooperative defence layer — the real boundary is OS isolation.
        self._check_tool_arguments_ssrf(tool_name, arguments)

        timeout_secs = tool_timeout if tool_timeout is not None else self.TOOL_TIMEOUT

        try:
            logger.info("Executing tool '%s' on server '%s' (timeout=%.1fs)",
                        tool_name, server_id, timeout_secs or 0)
            logger.debug("Arguments: %s", arguments)

            from app.core.telemetry import tool_span

            async def _call_tool():
                # Always read session fresh — if reconnect replaced it we use the new one.
                conn = self.connections.get(server_id)
                if not conn:
                    raise RuntimeError(f"MCP server '{server_id}' is not connected")
                session = conn['session']
                async with tool_span(tool_name):
                    if timeout_secs:
                        return await asyncio.wait_for(
                            session.call_tool(tool_name, arguments),
                            timeout=timeout_secs,
                        )
                    return await session.call_tool(tool_name, arguments)

            async def _call_with_reconnect():
                try:
                    return await _call_tool()
                except asyncio.TimeoutError:
                    # asyncio.wait_for cancels the in-flight session.call_tool()
                    # coroutine, but this does NOT corrupt the session: the mcp
                    # SDK's ClientSession runs its _receive_loop as an INDEPENDENT
                    # background task (owned by the session's own task group, not
                    # by whichever task called us) and dispatches every response
                    # strictly by request_id via self._response_streams. Cancelling
                    # our local await on that one response stream just abandons it
                    # — the receive loop, and every other in-flight/future request
                    # on this session, is unaffected. So on a plain timeout we do
                    # NOT reconnect: the session stays valid, and reconnecting would
                    # only add a slow, unnecessary connect round-trip (previously
                    # this branch forced a reconnect, and reconnecting via
                    # disconnect_server's cross-task aclose() is exactly what broke
                    # runs — see the BrokenPipe branch below for why THAT case still
                    # must forget-not-close).
                    logger.warning(
                        "MCP server '%s' tool '%s' timed out after %.1fs — "
                        "session remains valid (independent receive loop, "
                        "ID-demuxed responses); not reconnecting",
                        server_id, tool_name, timeout_secs or 0,
                    )
                    raise
                except (BrokenPipeError, ConnectionResetError, EOFError, OSError) as exc:
                    # The MCP server process died (common for npx servers after a long
                    # idle or mid-run OOM). Try to reconnect once before giving up.
                    logger.warning(
                        "MCP server '%s' pipe broken (%s) — attempting reconnect",
                        server_id, exc,
                    )
                    config = self._server_configs.get(server_id)
                    # Forget WITHOUT aclose, then reconnect fresh in this task. Using
                    # disconnect_server here would exit the dead connection's anyio
                    # cancel scope from a foreign task and cancel the owning agent-node
                    # task — the same cross-task hazard as the timeout path above.
                    self._forget_connection(server_id)
                    if config:
                        reconnected = await self.connect_server(server_id, config)
                        if reconnected:
                            logger.info("MCP server '%s' reconnected — retrying tool call", server_id)
                            return await _call_tool()
                    raise  # re-raise if we couldn't reconnect

            # Serialize calls to this server — MCP stdio cannot multiplex
            # concurrent requests on one session (parallel tool calls from the
            # model would otherwise interleave frames and hang to timeout).
            async with self._get_call_lock(server_id):
                result = await with_retry(_call_with_reconnect, max_retries=2)

            logger.info("Tool '%s' executed successfully", tool_name)

            raw_content = result.content if hasattr(result, 'content') else result
            # ── Credential sanitization ───────────────────────────────────────
            # Strip API keys / bearer tokens from tool output before returning
            # to the LLM.
            safe_content = _sanitize_tool_content(raw_content)

            return {
                'success': True,
                'content': safe_content,
                'isError': result.isError if hasattr(result, 'isError') else False
            }
            
        except asyncio.TimeoutError:
            logger.error("Tool '%s' timed out after retries", tool_name)
            return {
                'success': False,
                'error': f"Tool '{tool_name}' timed out after retries",
                'isError': True,
                'terminal': True,
            }
        except Exception as e:
            logger.error("Tool '%s' execution failed: %s", tool_name, e)
            return {
                'success': False,
                'error': str(e),
                'isError': True,
                'terminal': False,  # application error — return to agent for self-correction
            }
    
    @staticmethod
    def _check_tool_arguments_ssrf(tool_name: str, arguments: Dict[str, Any]) -> None:
        """Scan tool arguments for URL values and apply SSRF guard to each.

        Iterates over all string argument values.  Any value that looks like
        an HTTP/HTTPS URL is validated via check_ssrf.  Non-URL strings and
        non-string values are skipped.

        Raises:
            SSRFError: When a URL argument targets a blocked network range.
        """
        if not arguments:
            return

        for key, value in arguments.items():
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped.startswith(("http://", "https://")):
                continue
            try:
                check_ssrf(stripped)
            except SSRFError as exc:
                logger.warning(
                    "SSRF guard blocked tool '%s' argument '%s': %s",
                    tool_name, key, exc,
                )
                raise

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        # Disconnect each server using individual disconnect to handle errors gracefully
        for server_id in tuple(self._exit_stacks.keys()):
            await self.disconnect_server(server_id)
    
    def _forget_connection(self, server_id: str) -> None:
        """Drop all references to a connection WITHOUT closing its exit stack.

        The stdio_client/ClientSession anyio cancel scope is bound to the task
        that opened it (in ``connect_server``). Calling ``exit_stack.aclose()``
        from any OTHER task makes anyio fire a ``CancelledError`` into the owning
        task — and on the tool-timeout path that owner is the agent node running
        the tool call, so the cross-task close silently kills the whole run.

        This helper is the task-safe alternative: it only drops the manager's
        references. The orphaned stdio subprocess is reaped by GC — the same
        trade-off ``disconnect_server`` already accepts ("resources will be
        garbage collected anyway"). The next ``execute_tool`` for this server
        reconnects fresh. Use this (never ``disconnect_server``) from a task that
        did not open the connection.
        """
        self._exit_stacks.pop(server_id, None)
        self.connections.pop(server_id, None)
        self.tools.pop(server_id, None)
        self.tool_objects.pop(server_id, None)

    async def _ensure_connected(self, server_id: str) -> None:
        """Lazily (re)connect to *server_id* if it isn't currently connected.

        Mirrors the reference MCP client's ``ensureConnectedClient``: called at
        the top of every ``execute_tool``, not just once at setup. If a prior
        call ``_forget_connection()``'d a dead session (see the BrokenPipe
        branch in ``execute_tool``), this transparently reconnects using the
        remembered config — in the CALLING task, so the new exit stack's anyio
        cancel scope is owned by whichever task is actually about to use it,
        never left dangling in a subtask that already returned.

        Raises ``ValueError`` if this server has never been connected (no
        remembered config — same contract as the old hard guard in
        ``execute_tool``). Raises ``RuntimeError`` if a reconnect attempt fails.
        """
        if server_id in self.connections:
            return
        config = self._server_configs.get(server_id)
        if not config:
            raise ValueError(f"Not connected to server: {server_id}")
        logger.info("MCP server '%s' not connected — lazily reconnecting", server_id)
        connected = await self.connect_server(server_id, config)
        if not connected:
            raise RuntimeError(
                f"MCP server '{server_id}' reconnect failed: "
                f"{self.last_errors.get(server_id, 'unknown error')}"
            )

    async def disconnect_server(self, server_id: str):
        """Disconnect from a specific MCP server."""
        if server_id not in self._exit_stacks:
            return
            
        exit_stack = self._exit_stacks.pop(server_id)
        self.connections.pop(server_id, None)
        self.tools.pop(server_id, None)
        self.tool_objects.pop(server_id, None)
        
        # Try to close the exit stack, but suppress all errors since
        # cross-task cleanup with anyio cancel scopes is fundamentally problematic
        try:
            await exit_stack.aclose()
            logger.info("Disconnected from MCP server: %s", server_id)
        except Exception:
            # Silently suppress all cleanup errors
            # The resources will be garbage collected anyway
            logger.debug("MCP cleanup completed for %s (errors suppressed)", server_id)
            pass
    
    def get_available_tools(self, server_id: Optional[str] = None) -> Dict[str, List[str]]:
        """
        Get available tools across all servers or a specific server.
        
        Args:
            server_id: Optional server ID to filter by
        
        Returns:
            Dictionary mapping server IDs to tool lists
        """
        if server_id:
            return {server_id: self.tools.get(server_id, [])}
        return self.tools.copy()
    
    def is_connected(self, server_id: str) -> bool:
        """Check if connected to a server."""
        return server_id in self.connections
