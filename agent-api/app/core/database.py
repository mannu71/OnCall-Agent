"""Database configuration and connection management."""
import logging
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)

# Create async engine using settings
async_engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    future=True
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
    """Initialize database tables."""
    # Import models to register them with Base
    from app.models.db_models import (
        WorkflowModel, ExecutionModel,
        LLMConfigModel, MCPServerModel, LogPatternModel,
        KnowledgeEntryModel, SkillModel,
        BaselineMetricModel, AnalysisHistoryModel, AlertModel,
        ModelKeyModel
    )
    
    async with async_engine.begin() as conn:
        # First create the vector extension
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("pgvector extension created/verified")
        except Exception as e:
            logger.warning(f"Could not create vector extension: {e}")
        
        # Then create all tables
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")
