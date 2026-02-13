"""MCP Client Manager for connecting to and executing tools on MCP servers."""
import asyncio
import logging
from typing import Dict, List, Any, Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manages connections to multiple MCP servers."""
    
    def __init__(self):
        self.connections: Dict[str, Dict[str, Any]] = {}
        self.tools: Dict[str, List[str]] = {}  # server_id -> list of tool names
    
    async def connect_server(self, server_id: str, config: Dict[str, Any]) -> bool:
        """
        Connect to an MCP server.
        
        Args:
            server_id: Unique identifier for this server
            config: Server configuration with 'command', 'args', 'env'
        
        Returns:
            True if connection successful
        """
        try:
            logger.info(f"Connecting to MCP server: {server_id}")
            
            command = config.get('command')
            args = config.get('args', [])
            env = config.get('env', {})
            
            if not command:
                raise ValueError(f"Server {server_id} missing 'command' in config")
            
            # Create server parameters
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env
            )
            
            # Connect to server with context manager
            stdio_context = stdio_client(server_params)
            read, write = await stdio_context.__aenter__()
            
            # Create session
            session = ClientSession(read, write)
            await session.__aenter__()
            
            # Initialize connection
            await session.initialize()
            
            # List available tools
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
            
            logger.info(f"Connected to {server_id} with {len(tool_names)} tools: {tool_names}")
            return True
                
        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_id}: {e}")
            return False
    
    async def execute_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: Optional[int] = 60
    ) -> Dict[str, Any]:
        """
        Execute a tool on an MCP server.
        
        Args:
            server_id: Server identifier
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            timeout: Optional timeout in seconds
        
        Returns:
            Tool execution result
        """
        if server_id not in self.connections:
            raise ValueError(f"Not connected to server: {server_id}")
        
        try:
            logger.info(f"Executing tool '{tool_name}' on server '{server_id}'")
            logger.debug(f"Arguments: {arguments}")
            
            session = self.connections[server_id]['session']
            
            # Execute tool with timeout
            if timeout:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=timeout
                )
            else:
                result = await session.call_tool(tool_name, arguments)
            
            logger.info(f"Tool '{tool_name}' executed successfully")
            
            # Parse result
            return {
                'success': True,
                'content': result.content if hasattr(result, 'content') else result,
                'isError': result.isError if hasattr(result, 'isError') else False
            }
            
        except asyncio.TimeoutError:
            logger.error(f"Tool '{tool_name}' timed out after {timeout}s")
            return {
                'success': False,
                'error': f"Timeout after {timeout}s",
                'isError': True
            }
        except Exception as e:
            logger.error(f"Tool '{tool_name}' execution failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'isError': True
            }
    
    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        for server_id in list(self.connections.keys()):
            await self.disconnect_server(server_id)
    
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
                
                logger.info(f"Disconnected from MCP server: {server_id}")
            except Exception as e:
                logger.error(f"Error disconnecting from {server_id}: {e}")
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
