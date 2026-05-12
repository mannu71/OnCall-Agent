"""MCP Client Manager for connecting to and executing tools on MCP servers."""
import asyncio
import logging
import os
import os.path
import glob
from contextlib import AsyncExitStack
from typing import Dict, List, Any, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.core.retry import with_retry
from app.core.security import check_ssrf, SSRFError

logger = logging.getLogger(__name__)

# Default certificate directory in container
CERTS_DIR = '/app/data/certs'


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
            logger.error(
                "MCP server %s connection timed out after %ss",
                server_id, self.CONNECT_TIMEOUT,
            )
            if server_id in self._exit_stacks:
                try:
                    await self._exit_stacks[server_id].aclose()
                except Exception:
                    pass
                finally:
                    self._exit_stacks.pop(server_id, None)
            return False

    async def _connect_server_inner(self, server_id: str, config: Dict[str, Any]) -> bool:
        """Internal connection logic wrapped by connect_server timeout."""
        try:
            logger.info("Connecting to MCP server: %s", server_id)
            
            command = config.get('command')
            args = config.get('args', [])
            custom_env = config.get('env', {}).copy()
            
            _transform_cert_path(custom_env)
            _auto_detect_certificate(custom_env, args)
            
            env = {**os.environ, **custom_env} if custom_env else None
            
            if not command:
                raise ValueError(f"Server {server_id} missing 'command' in config")
            
            if command == 'npx':
                command = '/usr/bin/npx'
            
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
            
            self.connections[server_id] = {
                'config': config,
                'session': session,
                'read': read,
                'write': write,
            }
            self.tools[server_id] = tool_names
            # Store full tool objects for schema/description access by LangChain adapter
            self.tool_objects[server_id] = {tool.name: tool for tool in tools_result.tools}
            
            logger.info("Connected to %s with %d tools: %s", server_id, len(tool_names), tool_names)
            return True
                
        except Exception as e:
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
    
    async def execute_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a tool on an MCP server.
        
        Args:
            server_id: Server identifier
            tool_name: Name of the tool to execute
            arguments: Tool arguments
        
        Returns:
            Tool execution result
        """
        if server_id not in self.connections:
            raise ValueError(f"Not connected to server: {server_id}")

        # SSRF guard: reject any URL-shaped argument that targets a private range.
        # This is a cooperative defence layer — the real boundary is OS isolation.
        self._check_tool_arguments_ssrf(tool_name, arguments)

        try:
            logger.info("Executing tool '%s' on server '%s'", tool_name, server_id)
            logger.debug("Arguments: %s", arguments)
            
            session = self.connections[server_id]['session']
            
            async def _call_tool():
                return await session.call_tool(tool_name, arguments)
            
            result = await with_retry(_call_tool, max_retries=2)
            
            logger.info("Tool '%s' executed successfully", tool_name)
            
            return {
                'success': True,
                'content': result.content if hasattr(result, 'content') else result,
                'isError': result.isError if hasattr(result, 'isError') else False
            }
            
        except asyncio.TimeoutError:
            logger.error("Tool '%s' timed out", tool_name)
            return {
                'success': False,
                'error': f"Tool '{tool_name}' timed out",
                'isError': True
            }
        except Exception as e:
            logger.error("Tool '%s' execution failed: %s", tool_name, e)
            return {
                'success': False,
                'error': str(e),
                'isError': True
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
