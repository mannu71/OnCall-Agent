"""MCP Configuration repository for data access."""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from app.repositories.base import BaseRepository
from app.config import settings

logger = logging.getLogger(__name__)


class MCPConfigRepository(BaseRepository[Dict[str, Any]]):
    """Repository for MCP server configuration data access."""
    
    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize MCP config repository.
        
        Args:
            storage_path: Path to config directory
        """
        # Use data/config directory for MCP servers config
        path = Path(storage_path) if storage_path else Path("data/config")
        super().__init__(path)
        
        self._config_file = self.storage_path / "mcp-servers.json"
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(minutes=5)  # 5 minute TTL
    
    def _is_cache_valid(self) -> bool:
        """Check if cached config is still valid.
        
        Returns:
            True if cache is valid, False otherwise
        """
        if self._cache is None:
            return False
        
        if self._cache_timestamp is None:
            return False
        
        return datetime.now(timezone.utc) - self._cache_timestamp < self._cache_ttl
    
    def _update_cache(self, config: Dict[str, Any]) -> None:
        """Update cache with new config.
        
        Args:
            config: Configuration data
        """
        self._cache = config
        self._cache_timestamp = datetime.now(timezone.utc)
    
    def _invalidate_cache(self) -> None:
        """Invalidate the config cache."""
        self._cache = None
        self._cache_timestamp = None
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file.
        
        Returns:
            Configuration dictionary
        """
        try:
            if self._config_file.exists():
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        return self._get_default_config()
                    return json.loads(content)
        except Exception as e:
            logger.error(f"Error loading MCP config: {e}")
        
        return self._get_default_config()
    
    def _save_config(self, config: Dict[str, Any]) -> bool:
        """Save configuration to file.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            logger.info(f"MCP config saved to: {self._config_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving MCP config: {e}")
            return False
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default configuration structure.
        
        Returns:
            Default configuration dictionary
        """
        return {
            "servers": {}
        }
    
    async def get_by_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get MCP server by name (ID).
        
        Args:
            item_id: Server name
            
        Returns:
            Server config if found, None otherwise
        """
        config = await self.get_full_config()
        return config.get("servers", {}).get(item_id)
    
    async def get_full_config(self) -> Dict[str, Any]:
        """Get full MCP configuration.
        
        Returns:
            Full configuration dictionary
        """
        if self._is_cache_valid():
            return self._cache or self._get_default_config()
        
        config = self._load_config()
        self._update_cache(config)
        return config
    
    async def list_all(self) -> List[Dict[str, Any]]:
        """List all MCP servers.
        
        Returns:
            List of server configurations with names
        """
        config = await self.get_full_config()
        servers = config.get("servers", {})
        return [
            {"name": name, **server_config}
            for name, server_config in servers.items()
        ]
    
    async def get_servers(self) -> Dict[str, Any]:
        """Get all MCP servers as a dictionary.
        
        Returns:
            Dictionary of server name -> config
        """
        config = await self.get_full_config()
        return config.get("servers", {})
    
    async def save(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Save or update an MCP server.
        
        Args:
            item: Server configuration with 'name' field
            
        Returns:
            Saved server configuration
        """
        server_name = item.get("name")
        if not server_name:
            raise ValueError("Server name is required")
        
        config = await self.get_full_config()
        
        # Extract server config without the name field
        server_config = {k: v for k, v in item.items() if k != "name"}
        
        config["servers"][server_name] = server_config
        self._save_config(config)
        self._invalidate_cache()
        
        return item
    
    async def save_full_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Save full MCP configuration.
        
        Args:
            config: Full configuration dictionary
            
        Returns:
            Saved configuration
        """
        self._save_config(config)
        self._invalidate_cache()
        return config
    
    async def delete(self, item_id: str) -> bool:
        """Delete an MCP server by name.
        
        Args:
            item_id: Server name
            
        Returns:
            True if deleted, False if not found
        """
        config = await self.get_full_config()
        
        if item_id not in config.get("servers", {}):
            return False
        
        del config["servers"][item_id]
        self._save_config(config)
        self._invalidate_cache()
        return True
    
    async def exists(self, item_id: str) -> bool:
        """Check if an MCP server exists.
        
        Args:
            item_id: Server name
            
        Returns:
            True if exists, False otherwise
        """
        config = await self.get_full_config()
        return item_id in config.get("servers", {})
