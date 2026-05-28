"""Model key repository for API credential storage."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete

from app.core.database import AsyncSessionLocal
from app.models.db_models import ModelKeyModel
from app.services.credential_transformer import CredentialTransformer

logger = logging.getLogger(__name__)


# Canonical provider name -> all known aliases stored in ``model_keys.provider``.
# The discover-models endpoint and the Settings UI use the canonical form on
# the left; legacy rows / CLI scripts use the form on the right. Lookup tries
# every variant so neither side has to migrate.
_PROVIDER_ALIASES: Dict[str, tuple] = {
    "aws bedrock":  ("AWS Bedrock", "bedrock", "Bedrock", "aws-bedrock", "aws_bedrock"),
    "openai":       ("OpenAI", "openai", "open_ai", "open-ai"),
    "anthropic":    ("Anthropic", "anthropic"),
    "azure openai": ("Azure OpenAI", "azure_openai", "azure-openai", "azure"),
    "google":       ("Google", "google", "google-genai", "google_genai", "gemini"),
    "groq":         ("Groq", "groq"),
    "ollama":       ("Ollama", "ollama"),
}


def _provider_aliases(provider: str) -> List[str]:
    """Return every spelling that should match *provider* in the DB."""
    if not provider:
        return [provider]
    key = provider.strip().lower()
    aliases = _PROVIDER_ALIASES.get(key)
    if aliases is None:
        # Unknown provider — try the input as-is plus a lowercase copy.
        return list({provider, provider.lower()})
    # Include the input value too in case the caller passes a novel variant.
    return list({provider, *aliases})


class ModelKeyRepository:
    """Repository for model key (API credential) data access."""
    
    def __init__(self):
        """Initialize model key repository."""
        logger.info("ModelKeyRepository initialized")
    
    async def list_all(self, include_secrets: bool = False) -> List[Dict[str, Any]]:
        """List all model keys.
        
        Args:
            include_secrets: If True, include actual secret values
            
        Returns:
            List of model key data
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ModelKeyModel))
            keys = result.scalars().all()
            return [self._model_key_to_dict(k, include_secrets) for k in keys]
    
    async def get_by_provider(self, provider: str, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
        """Get model key by provider.
        
        Args:
            provider: Provider identifier
            include_secrets: If True, include actual secret values
            
        Returns:
            Model key dict or None
        """
        # Accept both the canonical UI form ("AWS Bedrock", "Azure OpenAI")
        # and shorter aliases ("bedrock", "azure", "openai") so the LLM-config
        # endpoint and the model-keys table can be edited independently
        # without breaking lookup.
        aliases = _provider_aliases(provider)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelKeyModel).where(ModelKeyModel.provider.in_(aliases))
            )
            k = result.scalars().first()
            return self._model_key_to_dict(k, include_secrets) if k else None

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new model key.
        
        Args:
            data: Model key data with potentially aws_* prefixed field names
            
        Returns:
            Created model key data
        """
        normalized_data = CredentialTransformer.normalize_input(data)
        
        async with AsyncSessionLocal() as session:
            key = ModelKeyModel(
                provider=normalized_data["provider"],
                api_key=normalized_data.get("api_key"),
                secret_key=normalized_data.get("secret_key"),
                endpoint=normalized_data.get("endpoint"),
                region=normalized_data.get("region"),
                access_key_id=normalized_data.get("access_key_id"),
                secret_access_key=normalized_data.get("secret_access_key"),
                session_token=normalized_data.get("session_token"),
                description=normalized_data.get("description"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(key)
            await session.commit()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)
    
    async def update(self, provider: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing model key.
        
        Args:
            provider: Provider identifier
            data: Updated model key data
            
        Returns:
            Updated model key data or None if not found
        """
        normalized_data = CredentialTransformer.normalize_input(data)
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelKeyModel).where(ModelKeyModel.provider == provider)
            )
            key = result.scalar_one_or_none()
            if not key:
                return None
            
            for field_name in ["api_key", "secret_key", "endpoint", "region",
                               "access_key_id", "secret_access_key", "session_token", "description"]:
                if field_name in normalized_data:
                    setattr(key, field_name, normalized_data[field_name])
            
            key.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)
    
    async def upsert(self, provider: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or update a model key.
        
        Args:
            provider: Provider identifier
            data: Model key data
            
        Returns:
            Upserted model key data
        """
        existing = await self.get_by_provider(provider)
        if existing:
            return await self.update(provider, data)
        return await self.create({**data, "provider": provider})
    
    async def delete(self, provider: str) -> bool:
        """Delete a model key.
        
        Args:
            provider: Provider identifier
            
        Returns:
            True if deleted, False if not found
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(ModelKeyModel).where(ModelKeyModel.provider == provider).returning(ModelKeyModel.id)
            )
            deleted = result.scalar_one_or_none()
            await session.commit()
            return deleted is not None
    
    async def exists(self, provider: str) -> bool:
        """Check if a model key exists.
        
        Args:
            provider: Provider identifier
            
        Returns:
            True if exists
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ModelKeyModel.id).where(ModelKeyModel.provider == provider)
            )
            return result.scalar_one_or_none() is not None
    
    def _model_key_to_dict(
        self,
        key: ModelKeyModel,
        include_secrets: bool = False,
        schema: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Convert model key to dictionary.
        
        Args:
            key: ModelKeyModel instance
            include_secrets: If True, include actual secret values
            schema: Optional schema to include
            
        Returns:
            Dictionary representation
        """
        d = {
            "provider": key.provider,
            "has_api_key": bool(key.api_key),
            "has_secret_key": bool(key.secret_key),
            "has_access_credentials": bool(key.access_key_id and key.secret_access_key),
            "endpoint": key.endpoint,
            "region": key.region,
            "description": key.description,
        }
        
        configured_fields = []
        if key.api_key:
            configured_fields.append("api_key")
        if key.secret_key:
            configured_fields.append("secret_key")
        if key.access_key_id:
            configured_fields.append("access_key_id")
        if key.secret_access_key:
            configured_fields.append("secret_access_key")
        if key.session_token:
            configured_fields.append("session_token")
        if key.endpoint:
            configured_fields.append("endpoint")
        if key.region:
            configured_fields.append("region")
        if key.description:
            configured_fields.append("description")
        
        d["configured_fields"] = configured_fields
        
        if include_secrets:
            d["api_key"] = key.api_key
            d["secret_key"] = key.secret_key
            d["access_key_id"] = key.access_key_id
            d["secret_access_key"] = key.secret_access_key
            d["session_token"] = key.session_token
        
        return d