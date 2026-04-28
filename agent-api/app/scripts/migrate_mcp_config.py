"""
Migration script to transfer MCP server configurations from file to database.

This script reads the mcp-servers.json file and imports all servers into the database.
It can be run manually or as part of the deployment process.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any

from app.repositories.db_repository import db_repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def migrate_mcp_servers_to_db(config_file: str = "data/config/mcp-servers.json") -> Dict[str, Any]:
    """
    Migrate MCP servers from JSON file to database.
    
    Args:
        config_file: Path to the mcp-servers.json file
        
    Returns:
        Migration results with stats
    """
    config_path = Path(config_file)
    
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_file}")
        return {
            "success": False,
            "error": f"Config file not found: {config_file}",
            "migrated": 0,
            "skipped": 0,
            "failed": 0
        }
    
    # Read config file
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Error reading config file: {e}")
        return {
            "success": False,
            "error": f"Error reading config file: {str(e)}",
            "migrated": 0,
            "skipped": 0,
            "failed": 0
        }
    
    servers = config.get("servers", {})
    
    if not servers:
        logger.info("No servers found in config file")
        return {
            "success": True,
            "message": "No servers to migrate",
            "migrated": 0,
            "skipped": 0,
            "failed": 0
        }
    
    migrated = 0
    skipped = 0
    failed = 0
    errors = []
    
    # Migrate each server
    for server_name, server_config in servers.items():
        try:
            # Check if server already exists
            existing = await db_repository.get_mcp_server_by_name(server_name)
            
            if existing:
                logger.info(f"Server '{server_name}' already exists in database, skipping")
                skipped += 1
                continue
            
            # Convert disabled to enabled
            server_data = {
                "name": server_name,
                "command": server_config.get("command", ""),
                "args": server_config.get("args", []),
                "env": server_config.get("env", {}),
                "description": server_config.get("description"),
                "enabled": not server_config.get("disabled", False)
            }
            
            # Create in database
            await db_repository.create_mcp_server(server_data)
            logger.info(f"Migrated server: {server_name}")
            migrated += 1
            
        except Exception as e:
            logger.error(f"Error migrating server '{server_name}': {e}")
            errors.append({"server": server_name, "error": str(e)})
            failed += 1
    
    result = {
        "success": failed == 0,
        "total": len(servers),
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed
    }
    
    if errors:
        result["errors"] = errors
    
    logger.info(f"Migration complete: {migrated} migrated, {skipped} skipped, {failed} failed")
    
    return result


async def export_db_to_file(output_file: str = "data/config/mcp-servers-export.json") -> Dict[str, Any]:
    """
    Export MCP servers from database to JSON file.
    
    Args:
        output_file: Path to save the exported config
        
    Returns:
        Export results
    """
    try:
        # Get all servers from database
        servers = await db_repository.list_mcp_servers(include_disabled=True)
        
        # Convert to file format
        servers_dict = {}
        for server in servers:
            server_name = server["name"]
            servers_dict[server_name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": server.get("env", {}),
                "description": server.get("description"),
                "disabled": not server.get("enabled", True)
            }
        
        config = {"servers": servers_dict}
        
        # Write to file
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        
        logger.info(f"Exported {len(servers)} servers to {output_file}")
        
        return {
            "success": True,
            "file": output_file,
            "server_count": len(servers)
        }
        
    except Exception as e:
        logger.error(f"Error exporting to file: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def main():
    """Main entry point for migration script."""
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "import":
            # Import from file to database
            config_file = sys.argv[2] if len(sys.argv) > 2 else "data/config/mcp-servers.json"
            result = await migrate_mcp_servers_to_db(config_file)
            print(json.dumps(result, indent=2))
            
        elif command == "export":
            # Export from database to file
            output_file = sys.argv[2] if len(sys.argv) > 2 else "data/config/mcp-servers-export.json"
            result = await export_db_to_file(output_file)
            print(json.dumps(result, indent=2))
            
        else:
            print(f"Unknown command: {command}")
            print("Usage:")
            print("  python -m app.scripts.migrate_mcp_config import [config_file]")
            print("  python -m app.scripts.migrate_mcp_config export [output_file]")
            sys.exit(1)
    else:
        print("MCP Server Configuration Migration Tool")
        print("")
        print("Usage:")
        print("  python -m app.scripts.migrate_mcp_config import [config_file]")
        print("    Import servers from JSON file to database")
        print("")
        print("  python -m app.scripts.migrate_mcp_config export [output_file]")
        print("    Export servers from database to JSON file")


if __name__ == "__main__":
    asyncio.run(main())
