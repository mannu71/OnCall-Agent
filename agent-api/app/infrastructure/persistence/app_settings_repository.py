"""Application settings repository (generic key/value store)."""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy import select

from app.infrastructure.persistence.base import BaseAsyncRepository
from app.models.db_models import AppSettingModel

logger = logging.getLogger(__name__)


class AppSettingsRepository(BaseAsyncRepository):
    """Repository for runtime-editable scalar app settings.

    Backed by the ``app_settings`` table (see migration 011). Values are stored
    as text; callers coerce as needed.
    """

    def __init__(self):
        logger.info("AppSettingsRepository initialized")

    async def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return the value for *key*, or *default* if unset."""
        row = await self._one(select(AppSettingModel).where(AppSettingModel.key == key))
        return row.value if row is not None else default

    async def set(self, key: str, value: str) -> None:
        """Insert or update *key* with *value* (upsert)."""
        async def _work(session):
            row = (await session.execute(
                select(AppSettingModel).where(AppSettingModel.key == key)
            )).scalar_one_or_none()
            if row is None:
                session.add(AppSettingModel(
                    key=key, value=value, updated_at=datetime.now(timezone.utc),
                ))
            else:
                row.value = value
                row.updated_at = datetime.now(timezone.utc)
        await self._run(_work)

    async def all(self) -> Dict[str, str]:
        """Return all settings as a dict."""
        rows = await self._all(select(AppSettingModel))
        return {row.key: row.value for row in rows}
