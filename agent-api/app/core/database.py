"""Database configuration and connection management."""
import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)

# Create async engine using settings.
# Pool sized for parallel DAG node execution (see plan §3.2).
# Override via settings.db_pool_size / settings.db_max_overflow if defined.
async_engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    future=True,
    pool_size=getattr(settings, "db_pool_size", 20),
    max_overflow=getattr(settings, "db_max_overflow", 40),
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=30,
)

# Create async session factory
AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base class for models
Base = declarative_base()


async def get_db_session() -> AsyncSession:
    """Get database session.
    
    Yields:
        AsyncSession: Database session
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Verify database connectivity.

    Tables are NOT created here.  All DDL lives in the numbered migration
    scripts under ``migrations/`` and must be applied before starting the
    application::

        psql -f migrations/001_initial_schema.sql
        psql -f migrations/002_add_token_columns.sql
        psql -f migrations/002_vector_indexes.sql
        psql -f migrations/003_code_intelligence.sql
        psql -f migrations/004_atropos.sql
        psql -f migrations/005_code_analyzer_v1.sql

    This function only checks that the database is reachable.  A failed
    connectivity check logs a warning and lets the application continue so
    that transient Postgres start-up delays do not permanently crash the
    container.
    """
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connectivity verified")
    except Exception as exc:
        logger.warning(
            "Database connectivity check failed: %s — "
            "the application will start but DB-backed features may not work. "
            "Ensure migrations/001–005 have been applied before first use.",
            exc,
        )
