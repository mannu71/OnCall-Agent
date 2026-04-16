"""API v1 router aggregator"""

from fastapi import APIRouter

from app.api.v1.endpoints import health, workflows, executions, mcp_config, certificates, llm_config, log_watch, model_keys, insights

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(workflows.router)
api_router.include_router(executions.router)
api_router.include_router(mcp_config.router)
api_router.include_router(certificates.router)
api_router.include_router(llm_config.router)
api_router.include_router(log_watch.router)
api_router.include_router(model_keys.router)
api_router.include_router(insights.router)
