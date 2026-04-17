"""Main FastAPI application."""
import os
import ssl
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Disable SSL certificate verification globally when AWS_SSL_VERIFY=false.
# Required in environments where a self-signed CA is in the certificate chain.
if os.environ.get("AWS_SSL_VERIFY", "true").lower() in ("false", "0", "no"):
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

from app.config import settings
from app.api.v1.api import api_router
from app.api.middleware import register_exception_handlers
from app.core.scheduler import workflow_scheduler
from app.core.database import init_db
from app.core.logging import setup_logging, get_logger

# Setup logging first
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Agent API...")
    
    # Initialize database tables
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.warning(f"Database initialization skipped (may already exist): {e}")
    
    workflow_scheduler.start()
    yield
    # Shutdown
    logger.info("Shutting down Agent API...")
    workflow_scheduler.stop()


# Create FastAPI application
app = FastAPI(
    title="Agent API",
    description="Lightweight workflow automation engine with real-time streaming",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware - configurable via settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register error handlers
register_exception_handlers(app)

# Register API v1 routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Agent API",
        "version": "1.0.0",
        "description": "Lightweight workflow automation engine",
        "docs_url": "/docs",
        "health_url": "/api/v1/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload
    )
