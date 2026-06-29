"""API v1 router aggregator"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent_profiles,
    certificates,
    code_analyzer,
    codegraph,
    crawler,
    curator,
    executions,
    gateway,
    health,
    improvement,
    insights,
    jobs,
    llm_config,
    log_watch,
    mcp_config,
    model_keys,
    sessions,
    settings,
    skills,
    tools,
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
api_router.include_router(codegraph.router)
api_router.include_router(trajectories.router)
api_router.include_router(settings.router)
api_router.include_router(tools.router)
api_router.include_router(tools.node_schemas_router)
api_router.include_router(jobs.router)
api_router.include_router(gateway.router)
api_router.include_router(sessions.router)
api_router.include_router(agent_profiles.router)
api_router.include_router(improvement.router)
api_router.include_router(skills.router)
