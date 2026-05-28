"""API v1 router aggregator"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    certificates,
    code_analyzer,
    crawler,
    curator,
    executions,
    health,
    insights,
    llm_config,
    log_watch,
    mcp_config,
    model_keys,
    trajectories,
    workflows,
)

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
api_router.include_router(curator.router)
api_router.include_router(code_analyzer.router)
api_router.include_router(crawler.router)
api_router.include_router(trajectories.router)
