"""Main FastAPI application."""
import logging as _bootstrap_logging
from contextlib import asynccontextmanager

from app.config import settings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Scoped SSL trust override for AWS calls only.
#
# Historically this module mutated ``ssl._create_default_https_context`` to
# bypass verification process-wide — that leaked the override to httpx
# (Anthropic, Azure DevOps), urllib, and any other outbound stdlib HTTPS,
# which is a serious security regression. We now apply the override only to
# boto3's default session via ``AWS_CA_BUNDLE``, leaving all other HTTPS
# traffic untouched.
#
# Preferred remediation: set ``AWS_CA_BUNDLE`` to a PEM bundle containing
# your corporate / self-signed CA and leave verification enabled.
if not settings.aws_ssl_verify:
    _bootstrap_logging.getLogger(__name__).warning(
        "AWS_SSL_VERIFY=false set — disabling TLS verification for boto3 only. "
        "Prefer AWS_CA_BUNDLE pointing at your CA bundle in production."
    )
    try:
        # Scoped monkey-patch: inject verify=False default into boto3 client
        # and resource factories so AWS calls skip cert validation while
        # httpx (Anthropic), urllib (stdlib), and other HTTPS stacks remain
        # untouched.
        import boto3

        def _wrap(factory):
            def _patched(*args, **kwargs):
                kwargs.setdefault("verify", False)
                return factory(*args, **kwargs)
            return _patched

        boto3.client = _wrap(boto3.client)  # type: ignore[assignment]
        boto3.resource = _wrap(boto3.resource)  # type: ignore[assignment]

        # Also patch boto3.Session.client — transports that go through
        # boto3.Session().client(...) (e.g. BedrockTransport) would otherwise
        # bypass the verify=False override above.
        _orig_session_client = boto3.Session.client
        def _patched_session_client(self, *args, **kwargs):  # type: ignore[misc]
            kwargs.setdefault("verify", False)
            return _orig_session_client(self, *args, **kwargs)
        boto3.Session.client = _patched_session_client  # type: ignore[assignment]

    except Exception:  # pragma: no cover - boto3 missing in tooling envs
        pass
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

from app.config import settings
from app.api.v1.api import api_router
from app.api.middleware import register_exception_handlers
from app.core.scheduler import workflow_scheduler
from app.core.heartbeat import heartbeat_monitor
from app.core.database import init_db
from app.core.logging import setup_logging, get_logger
from app.core.telemetry import setup_telemetry

# Setup logging first
setup_logging()
logger = get_logger(__name__)

# Initialise OTel tracing (no-op when opentelemetry-sdk is not installed)
setup_telemetry()


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

    # Start proactive alarm monitoring (non-blocking background task).
    # Heartbeat polls CloudWatch for new ALARM-state alerts and auto-triggers
    # investigation workflows.  Degrades gracefully when AWS is unavailable.
    heartbeat_monitor.start()

    yield

    # Shutdown
    logger.info("Shutting down Agent API...")
    workflow_scheduler.stop()
    await heartbeat_monitor.stop()


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
