"""MCP Client Manager for connecting to and executing tools on MCP servers."""
import asyncio
import logging
import os
import os.path
import glob
from typing import Dict, List, Any, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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
    
    async def connect_server(self, server_id: str, config: Dict[str, Any]) -> bool:
        """Connect to an MCP server.
        
        Args:
            server_id: Unique identifier for this server
            config: Server configuration with 'command', 'args', 'env'
        
        Returns:
            True if connection successful
        """
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
            
            stdio_context = stdio_client(server_params)
            read, write = await stdio_context.__aenter__()
            logger.info("Stdio streams created for %s", server_id)
            
            session = ClientSession(read, write)
            await session.__aenter__()
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
                'stdio_context': stdio_context
            }
            self.tools[server_id] = tool_names
            
            logger.info("Connected to %s with %d tools: %s", server_id, len(tool_names), tool_names)
            return True
                
        except Exception as e:
            logger.error("Failed to connect to MCP server %s: %s", server_id, e, exc_info=True)
            logger.error("Config was - command: %s, args: %s", config.get('command'), config.get('args'))
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
        
        try:
            logger.info("Executing tool '%s' on server '%s'", tool_name, server_id)
            logger.debug("Arguments: %s", arguments)
            
            session = self.connections[server_id]['session']
            
            result = await session.call_tool(tool_name, arguments)
            
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
    
    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        # Iterate over items without modifying dict during iteration
        for server_id, conn in tuple(self.connections.items()):
            try:
                # Close session
                session = conn.get('session')
                if session:
                    await session.__aexit__(None, None, None)
                
                # Close stdio context
                stdio_context = conn.get('stdio_context')
                if stdio_context:
                    await stdio_context.__aexit__(None, None, None)
                
                logger.info("Disconnected from MCP server: %s", server_id)
            except Exception as e:
                logger.error("Error disconnecting from %s: %s", server_id, e)
        
        # Clear all connections after disconnecting
        self.connections.clear()
        self.tools.clear()
    
    async def disconnect_server(self, server_id: str):
        """Disconnect from a specific MCP server."""
        if server_id in self.connections:
            try:
                conn = self.connections[server_id]
                
                # Close session
                session = conn.get('session')
                if session:
                    await session.__aexit__(None, None, None)
                
                # Close stdio context
                stdio_context = conn.get('stdio_context')
                if stdio_context:
                    await stdio_context.__aexit__(None, None, None)
                
                logger.info("Disconnected from MCP server: %s", server_id)
            except Exception as e:
                logger.error("Error disconnecting from %s: %s", server_id, e)
            finally:
                del self.connections[server_id]
                if server_id in self.tools:
                    del self.tools[server_id]
    
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
