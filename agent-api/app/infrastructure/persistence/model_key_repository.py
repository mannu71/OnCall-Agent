"""Model key repository for API credential storage."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from sqlalchemy import select, delete

from app.infrastructure.persistence.base import BaseAsyncRepository
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


class ModelKeyRepository(BaseAsyncRepository):
    """Repository for model key (API credential) data access."""

    def __init__(self):
        """Initialize model key repository."""
        logger.info("ModelKeyRepository initialized")

    async def list_all(self, include_secrets: bool = False) -> List[Dict[str, Any]]:
        """List all model keys."""
        keys = await self._all(select(ModelKeyModel))
        return [self._model_key_to_dict(k, include_secrets) for k in keys]
    
    async def get_by_provider(self, provider: str, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
        """Get the highest-priority enabled model key for *provider*.

        With multi-credential support (migration 017) a provider may have several
        rows; this returns the preferred one (enabled, lowest ``priority``) so all
        existing single-credential callers keep working unchanged.

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
        k = await self._first(
            select(ModelKeyModel)
            .where(ModelKeyModel.provider.in_(aliases))
            .where(ModelKeyModel.enabled.is_(True))
            .order_by(ModelKeyModel.priority.asc(), ModelKeyModel.id.asc())
        )
        return self._model_key_to_dict(k, include_secrets) if k else None

    async def list_by_provider(self, provider: str, include_secrets: bool = False,
                               enabled_only: bool = True) -> List[Dict[str, Any]]:
        """List all credentials for *provider*, ordered by selection priority.

        Used by the fallback-chain router to gather alternate credentials to
        rotate across when the preferred one is throttled.
        """
        aliases = _provider_aliases(provider)
        stmt = select(ModelKeyModel).where(ModelKeyModel.provider.in_(aliases))
        if enabled_only:
            stmt = stmt.where(ModelKeyModel.enabled.is_(True))
        stmt = stmt.order_by(ModelKeyModel.priority.asc(), ModelKeyModel.id.asc())
        keys = await self._all(stmt)
        return [self._model_key_to_dict(k, include_secrets) for k in keys]

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new model key.
        
        Args:
            data: Model key data with potentially aws_* prefixed field names
            
        Returns:
            Created model key data
        """
        normalized_data = CredentialTransformer.normalize_input(data)

        async def _work(session):
            key = ModelKeyModel(
                provider=normalized_data["provider"],
                key_label=normalized_data.get("key_label") or "default",
                priority=int(normalized_data.get("priority") or 100),
                enabled=bool(normalized_data.get("enabled", True)),
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
            await session.flush()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)
        return await self._run(_work)
    
    async def update(self, provider: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update an existing model key.
        
        Args:
            provider: Provider identifier
            data: Updated model key data
            
        Returns:
            Updated model key data or None if not found
        """
        normalized_data = CredentialTransformer.normalize_input(data)

        async def _work(session):
            # A provider may now have several rows; update the preferred one
            # (lowest priority). Multi-key management of specific labels is a
            # separate concern from this back-compat single-provider update.
            key = (await session.execute(
                select(ModelKeyModel)
                .where(ModelKeyModel.provider == provider)
                .order_by(ModelKeyModel.priority.asc(), ModelKeyModel.id.asc())
            )).scalars().first()
            if not key:
                return None

            for field_name in ["key_label", "priority", "enabled",
                               "api_key", "secret_key", "endpoint", "region",
                               "access_key_id", "secret_access_key", "session_token", "description"]:
                if field_name in normalized_data:
                    setattr(key, field_name, normalized_data[field_name])

            key.updated_at = datetime.now(timezone.utc)
            await session.flush()
            await session.refresh(key)
            return self._model_key_to_dict(key, include_secrets=True)
        return await self._run(_work)
    
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
        async def _work(session):
            result = await session.execute(
                delete(ModelKeyModel).where(ModelKeyModel.provider == provider).returning(ModelKeyModel.id)
            )
            return result.scalar_one_or_none() is not None
        return await self._run(_work)

    async def exists(self, provider: str) -> bool:
        """Return True if a model key exists for *provider* (first match)."""
        row = await self._first(
            select(ModelKeyModel.id).where(ModelKeyModel.provider == provider)
        )
        return row is not None
    
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
            "key_label": getattr(key, "key_label", "default"),
            "priority": getattr(key, "priority", 100),
            "enabled": getattr(key, "enabled", True),
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