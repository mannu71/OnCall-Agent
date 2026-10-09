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
from app.api.middleware import register_exception_handlers, APIKeyAuthMiddleware
from app.core.runtime.scheduler import workflow_scheduler
from app.core.runtime.heartbeat import heartbeat_monitor
from app.core.database import init_db
from app.core.logging import setup_logging, get_logger
from app.core.observability.telemetry import setup_telemetry

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

    # Load the operator-configured global timezone into the in-process cache so
    # scheduling reflects it without waiting for the first settings write.
    try:
        from app.core.runtime.app_timezone import refresh_global_timezone

        tz_name = await refresh_global_timezone()
        logger.info("Global timezone loaded: %s", tz_name)
    except Exception as e:
        logger.warning(f"Global timezone load skipped: {e}")

    workflow_scheduler.start()

    # Start proactive alarm monitoring (non-blocking background task).
    # Heartbeat polls CloudWatch for new ALARM-state alerts and auto-triggers
    # investigation workflows.  Degrades gracefully when AWS is unavailable.
    heartbeat_monitor.start()

    # Re-fire any repo indexing that was interrupted by a previous restart so
    # workflows don't stay stuck on indexing_status='indexing' forever. The
    # in-process indexer task dies with the process; this heals on next boot.
    # Skipped under APP_PROFILE=lite (crawler-light deployments).
    if settings.startup_indexing_recovery_enabled:
        try:
            # Reap background jobs left 'running' by a previous process, then re-fire
            # any workflows still stuck mid-index so they self-heal.
            from app.services.background_jobs import background_job_store
            from app.services.codegraph_indexer import recover_interrupted_indexing

            await background_job_store.reap_orphans()
            await recover_interrupted_indexing()
        except Exception as e:
            logger.warning(f"Indexing recovery skipped: {e}")
    else:
        logger.info("Startup indexing recovery disabled (APP_PROFILE=lite or flag off)")

    # Populate the tool registry (built-in + MCP-discovered) so the catalog is
    # available via GET /api/v1/tools. Discovery-only in Phase 1; never fatal.
    try:
        from app.harness.registry_loader import load_registry

        await load_registry()
    except Exception as e:
        logger.warning(f"Tool registry load skipped: {e}")

    # Shared LangGraph persistence (durable checkpointer) for HITL resume.
    # Best-effort; falls back to in-memory if unavailable.
    try:
        from app.harness.runtime import init_persistence

        await init_persistence()
    except Exception as e:
        logger.warning(f"LangGraph persistence init skipped: {e}")

    # Apply any runtime feature-flag overrides saved from the Settings UI on top
    # of the environment defaults (best-effort; never blocks startup).
    try:
        from app.core import feature_flags

        await feature_flags.load_overlay()
    except Exception as e:  # noqa: BLE001 — never fail startup on the overlay
        logger.warning(f"Feature-flag overlay load skipped: {e}")

    # One-time: relocate a legacy ``data/skills`` tree under the OKF knowledge
    # bundle (``data/knowledge/skills``) so skills + KB share one portable bundle.
    # Idempotent + defensive; must run BEFORE the skill manager scans.
    try:
        import asyncio as _asyncio
        from app.core.knowledge import migrate_legacy_skills

        if await _asyncio.to_thread(migrate_legacy_skills):
            logger.info("Skills relocated under the knowledge bundle")
    except Exception as e:  # noqa: BLE001 — never fail startup on migration
        logger.warning(f"Skills relocation skipped: {e}")

    # Pre-scan the file-based skills once at startup, OFF the event loop, so the
    # first request coroutine never pays the blocking directory walk + YAML parse
    # (get_default_skill_manager scans lazily on first use otherwise). Best-effort:
    # a missing skills dir or bad file must never block startup.
    try:
        import asyncio as _asyncio
        from app.core.skills import get_default_skill_manager

        _mgr = await _asyncio.to_thread(get_default_skill_manager)
        logger.info("Skills pre-scanned: %d loaded", len(_mgr.list_skills()))
    except Exception as e:  # noqa: BLE001 — never fail startup on skill scan
        logger.warning(f"Skill pre-scan skipped: {e}")

    # Index the OKF knowledge bundle into the ``kb`` memory bank so human-authored
    # / git-shipped knowledge docs are recalled. Idempotent (sha-deduped) and
    # best-effort — needs the DB, so it runs after DB init and never blocks startup.
    if settings.knowledge_bundle_enabled:
        try:
            from app.core.knowledge import reconcile_kb_index

            _n = await reconcile_kb_index()
            if _n:
                logger.info("Knowledge bundle: indexed %d concept(s) into kb bank", _n)
        except Exception as e:  # noqa: BLE001 — never fail startup on reconcile
            logger.warning(f"Knowledge bundle reconcile skipped: {e}")

    # Surface the isolated-sandbox posture once at startup so a mis-set backend
    # (e.g. SANDBOX_BACKEND=container with no Docker) is visible before any run.
    try:
        from app.core import sandbox as _sandbox
        if _sandbox.is_enabled():
            logger.info(
                "Isolated sandbox ENABLED (backend=%s) — workflows with the "
                "sandbox toggle get an isolated run_command shell",
                settings.sandbox_backend,
            )
        else:
            logger.info("Isolated sandbox disabled (SANDBOX_BACKEND unset/disabled)")
    except Exception as e:  # noqa: BLE001 — never fail startup on a log
        logger.warning(f"Sandbox availability check skipped: {e}")

    yield

    # Shutdown
    logger.info("Shutting down Agent API...")
    workflow_scheduler.stop()
    await heartbeat_monitor.stop()
    try:
        from app.harness.runtime import close_persistence

        await close_persistence()
    except Exception:
        pass
    try:
        from app.core.context.tool_output import close_client as close_compressor_client

        await close_compressor_client()
    except Exception:
        pass
    try:
        from app.core.mini import shutdown_pool

        shutdown_pool()
    except Exception:
        pass


# Create FastAPI application
app = FastAPI(
    title="Agent API",
    description="Lightweight workflow automation engine with real-time streaming",
    version="1.0.0",
    lifespan=lifespan
)

# API-key auth - opt-in (see app.api.middleware.api_auth). Added before CORS
# so CORS ends up as the outermost middleware (Starlette: last-added wraps
# first) — that way a 401 rejection still carries CORS headers, so a
# browser-based client can read the response instead of hitting an opaque
# CORS network error.
app.add_middleware(APIKeyAuthMiddleware)

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
