"""Database bootstrap — re-exports ``app.core.database``.

The canonical database module is ``app.core.database``.  DDL is applied via
numbered SQL files in ``migrations/``; ``init_db()`` only verifies connectivity
and must **not** call ``Base.metadata.create_all``.
"""
from app.core.database import (
    AsyncSessionLocal,
    Base,
    async_engine,
    get_db_session,
    init_db,
)

__all__ = [
    "AsyncSessionLocal",
    "Base",
    "async_engine",
    "get_db_session",
    "init_db",
]
