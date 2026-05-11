"""LLM configuration repository."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete, update

from app.core.database import AsyncSessionLocal
from app.models.db_models import LLMConfigModel
from app.services.credential_transformer import CredentialTransformer
from app.services.provider_schema_registry import ProviderSchema

logger = logging.getLogger(__name__)


class LLMConfigRepository:
    """Repository for LLM configuration data access."""
    
    def __init__(self):
        """Initialize LLM config repository."""
        logger.info("LLMConfigRepository initialized")
    
    async def list_all(self) -> Dict[str, Dict[str, Any]]:
        """List all LLM configurations.
        
        Returns:
            Dictionary of LLM configurations keyed by name
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(LLMConfigModel))
            configs = result.scalars().all()
            return {
                config.name: self._llm_config_to_dict(config)
                for config in configs
            }
    
    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get LLM configuration by name.
        
        Args:
            name: Configuration name
            
        Returns:
            LLM config dict or None
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel).where(LLMConfigModel.name == name)
            )
            config = result.scalar_one_or_none()
            return self._llm_config_to_dict(config) if config else None
    
    async def create(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new LLM configuration.
        
        Args:
            config_data: Configuration data
            
        Returns:
            Created configuration dict
        """
        async with AsyncSessionLocal() as session:
            use_for_embeddings = config_data.get("use_for_embeddings") or config_data.get("useForEmbeddings", False)
            
            if use_for_embeddings:
                await session.execute(
                    update(LLMConfigModel).values(use_for_embeddings=False)
                )
            
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
                use_for_embeddings=use_for_embeddings,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(config)
            await session.commit()
            await session.refresh(config)
            return self._llm_config_to_dict(config)
    
    async def update(self, name: str, config_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing LLM configuration.
        
        Args:
            name: Configuration name
            config_data: Fields to update
            
        Returns:
            Updated configuration dict or None
        """
        async with AsyncSessionLocal() as session:
            use_for_embeddings = config_data.get("use_for_embeddings") or config_data.get("useForEmbeddings")
            
            if use_for_embeddings:
                await session.execute(
                    update(LLMConfigModel).values(use_for_embeddings=False)
                )
            
            result = await session.execute(
                select(LLMConfigModel).where(LLMConfigModel.name == name)
            )
            config = result.scalar_one_or_none()
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
                "use_for_embeddings": "use_for_embeddings",
                "useForEmbeddings": "use_for_embeddings",
            }
            for json_key, col_name in field_map.items():
                if json_key in config_data:
                    setattr(config, col_name, config_data[json_key])
            
            if "name" in config_data and config_data["name"] != name:
                config.name = config_data["name"]
            
            config.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(config)
            return self._llm_config_to_dict(config)
    
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
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(LLMConfigModel).where(LLMConfigModel.name == name).returning(LLMConfigModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None
    
    async def exists(self, name: str) -> bool:
        """Check if LLM configuration exists.
        
        Args:
            name: Configuration name
            
        Returns:
            True if exists
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(LLMConfigModel.id).where(LLMConfigModel.name == name)
            )
            return result.scalar_one_or_none() is not None
    
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
            "use_for_embeddings": config.use_for_embeddings or False,
            "useForEmbeddings": config.use_for_embeddings or False,
        }