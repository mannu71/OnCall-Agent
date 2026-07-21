"""LLM configuration repository."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import LLMConfigModel
from app.services.credential_transformer import CredentialTransformer

logger = logging.getLogger(__name__)


class LLMConfigRepository(BaseAsyncRepository):
    """Repository for LLM configuration data access."""

    def __init__(self):
        """Initialize LLM config repository."""
        logger.info("LLMConfigRepository initialized")

    async def list_all(self) -> Dict[str, Dict[str, Any]]:
        """List all LLM configurations keyed by name."""
        configs = await self._all(select(LLMConfigModel))
        return {config.name: self._llm_config_to_dict(config) for config in configs}

    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get LLM configuration by name, or None."""
        config = await self._one(select(LLMConfigModel).where(LLMConfigModel.name == name))
        return self._llm_config_to_dict(config) if config else None

    async def create(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new LLM configuration and return it."""
        async def _work(session):
            config = LLMConfigModel(
                name=config_data["name"],
                provider=config_data["provider"],
                model=config_data.get("model", ""),
                endpoint=config_data.get("endpoint"),
                base_url=config_data.get("baseUrl") or config_data.get("base_url"),
                temperature=config_data.get("temperature", 0.7),
                max_tokens=config_data.get("maxTokens") or config_data.get("max_tokens", 4096),
                region=config_data.get("region", "us-east-1"),
                icon=config_data.get("icon"),
                description=config_data.get("description"),
                aws_profile=config_data.get("aws_profile"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(config)
            await session.flush()
            await session.refresh(config)
            return self._llm_config_to_dict(config)
        return await self._run(_work)

    async def update(self, name: str, config_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing LLM configuration; return it, or None."""
        async def _work(session):
            config = (await session.execute(
                select(LLMConfigModel).where(LLMConfigModel.name == name)
            )).scalar_one_or_none()
            if not config:
                return None

            field_map = {
                "provider": "provider",
                "model": "model",
                "endpoint": "endpoint",
                "baseUrl": "base_url",
                "base_url": "base_url",
                "temperature": "temperature",
                "maxTokens": "max_tokens",
                "max_tokens": "max_tokens",
                "region": "region",
                "icon": "icon",
                "description": "description",
                "aws_profile": "aws_profile",
            }
            for json_key, col_name in field_map.items():
                if json_key in config_data:
                    setattr(config, col_name, config_data[json_key])
            if "name" in config_data and config_data["name"] != name:
                config.name = config_data["name"]
            config.updated_at = datetime.now(timezone.utc)

            await session.flush()
            await session.refresh(config)
            return self._llm_config_to_dict(config)
        return await self._run(_work)
    
    async def upsert(self, name: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update LLM configuration by name.
        
        Args:
            name: Configuration name
            config_data: Fields to set
            
        Returns:
            Upserted config dict
        """
        existing = await self.get_by_name(name)
        if existing:
            return await self.update(name, config_data)
        return await self.create({**config_data, "name": name})
    
    async def delete(self, name: str) -> bool:
        """Delete an LLM configuration.
        
        Args:
            name: Configuration name
            
        Returns:
            True if deleted, False if not found
        """
        async def _work(session):
            result = await session.execute(
                delete(LLMConfigModel).where(LLMConfigModel.name == name).returning(LLMConfigModel.id)
            )
            return result.scalar_one_or_none() is not None
        return await self._run(_work)

    async def exists(self, name: str) -> bool:
        """Return True if an LLM configuration named *name* exists."""
        row = await self._one(select(LLMConfigModel.id).where(LLMConfigModel.name == name))
        return row is not None

    async def find_existing_names(self, names: List[str]) -> set[str]:
        """Return the subset of *names* that already exist in the DB."""
        if not names:
            return set()
        return set(await self._all(
            select(LLMConfigModel.name).where(LLMConfigModel.name.in_(names))
        ))

    async def mark_discovered_existing(
        self, discovered: List[Dict[str, Any]], *, key: str = "name"
    ) -> List[Dict[str, Any]]:
        """Set ``already_exists`` on discovery rows with one batched query."""
        names = [d[key] for d in discovered if d.get(key)]
        existing = await self.find_existing_names(names)
        for item in discovered:
            item["already_exists"] = item.get(key) in existing
        return discovered
    
    def _llm_config_to_dict(self, config: LLMConfigModel) -> Dict[str, Any]:
        """Convert LLM config model to dictionary."""
        return {
            "provider": config.provider,
            "model": config.model,
            "endpoint": config.endpoint,
            "base_url": config.base_url,
            "baseUrl": config.base_url,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "maxTokens": config.max_tokens,
            "region": config.region,
            "icon": config.icon,
            "description": config.description,
            "aws_profile": config.aws_profile,
        }