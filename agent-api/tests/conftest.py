"""Pytest configuration and fixtures for agent-api tests."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from app.config import settings
from app.core.database import Base


# Create a test engine with NullPool to avoid connection reuse issues
@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create test database engine and initialize tables."""
    engine = create_async_engine(
        settings.async_database_url,
        poolclass=NullPool,  # No connection pooling for tests
        echo=False
    )
    
    # Initialize database tables
    async with engine.begin() as conn:
        # Import all models to register them with Base
        from app.models.db_models import (
            WorkflowModel, ExecutionModel,
            LLMConfigModel, MCPServerModel, LogPatternModel,
            KnownIssueModel, BaselineMetricModel, AnalysisHistoryModel, AlertModel,
            ModelKeyModel,
            TrajectoryModel, ContextReferenceModel, ContextLengthCacheModel
        )
        
        # Create vector extension if needed
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass  # Extension might already exist or not be needed
        
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup: Drop all tables after all tests
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session(test_engine):
    """Create test database session with transaction rollback.
    
    This fixture creates a fresh session for each test that will be rolled back
    after the test completes, ensuring test isolation.
    """
    # Create a session factory
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    async with async_session() as session:
        yield session
        # Rollback any uncommitted changes
        await session.rollback()
