"""Skills API — manage DB-backed executable skills + view filesystem skills.

DB skills (``SkillService``) are executable (via the ``execute_skill`` tool) and
are normally auto-distilled from runs; these endpoints let an operator view, add,
and delete them. Filesystem markdown skills (``SkillManager``) are read-only
guidance shipped on disk and are returned for visibility only.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.skills import skill_service, get_default_skill_manager

router = APIRouter(prefix="/skills", tags=["skills"])
logger = logging.getLogger(__name__)


class SkillBody(BaseModel):
    name: str = Field(..., description="Skill name (slugified server-side)")
    title: str = Field(default="")
    description: str = Field(default="")
    trigger_patterns: List[str] = Field(default_factory=list)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    workflow_name: Optional[str] = Field(default=None)


class FsSkillBody(BaseModel):
    name: str = Field(..., description="Skill name (slugified into the directory name)")
    content: str = Field(..., description="Full SKILL.md content, including YAML frontmatter")


class FsSkillUpdateBody(BaseModel):
    content: str = Field(..., description="Full SKILL.md content, including YAML frontmatter")


@router.get("", response_model=Dict[str, Any])
async def list_skills() -> Dict[str, Any]:
    """List DB skills (all statuses) and read-only filesystem skills."""
    db_skills = await skill_service.list_skills(status="all", limit=500)
    try:
        fs_skills = get_default_skill_manager().list_skills()
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills: filesystem list failed (%s)", exc)
        fs_skills = []
    return {"success": True, "db": db_skills, "filesystem": fs_skills}


@router.post("", response_model=Dict[str, Any])
async def create_skill(body: SkillBody) -> Dict[str, Any]:
    """Create (or overwrite) an operator-authored DB skill."""
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    skill = await skill_service.create_manual(
        name=body.name,
        title=body.title,
        description=body.description,
        trigger_patterns=body.trigger_patterns,
        steps=body.steps,
        workflow_name=body.workflow_name,
    )
    return {"success": True, "skill": skill}


@router.delete("", response_model=Dict[str, Any])
async def delete_all_skills(
    confirm: bool = Query(default=False, description="Must be true to delete all skills"),
) -> Dict[str, Any]:
    """Delete ALL DB skills (irreversible). Requires ?confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pass ?confirm=true to delete all skills",
        )
    removed = await skill_service.delete_all()
    return {"success": True, "deleted": removed}


@router.get("/{name}", response_model=Dict[str, Any])
async def get_skill(name: str) -> Dict[str, Any]:
    """Return the full detail for a single skill by name."""
    skill = await skill_service.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"skill '{name}' not found")
    return {"success": True, "skill": skill}


@router.put("/{name}", response_model=Dict[str, Any])
async def update_skill(name: str, body: SkillBody) -> Dict[str, Any]:
    """Update (upsert) an existing skill by name."""
    existing = await skill_service.get_skill(name)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"skill '{name}' not found")
    triggers = body.trigger_patterns or []
    skill = await skill_service.create_manual(
        name=name,
        title=body.title,
        description=body.description,
        trigger_patterns=triggers,
        steps=body.steps,
        workflow_name=body.workflow_name,
    )
    return {"success": True, "skill": skill}


@router.get("/fs/{name}", response_model=Dict[str, Any])
async def get_fs_skill(name: str) -> Dict[str, Any]:
    """Return the raw SKILL.md source for a single filesystem skill."""
    manager = get_default_skill_manager()
    source = manager.get_skill_source(name)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"filesystem skill '{name}' not found")
    return {"success": True, "skill": source}


@router.post("/fs", response_model=Dict[str, Any])
async def create_fs_skill(body: FsSkillBody) -> Dict[str, Any]:
    """Create a new markdown SKILL.md file under the user skills directory."""
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    manager = get_default_skill_manager()
    try:
        skill = manager.write_skill(body.name, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"success": True, "skill": {"name": skill.name, "description": skill.description}}


@router.put("/fs/{name}", response_model=Dict[str, Any])
async def update_fs_skill(name: str, body: FsSkillUpdateBody) -> Dict[str, Any]:
    """Update (overwrite) an existing markdown SKILL.md file's content."""
    manager = get_default_skill_manager()
    if manager.get_skill_source(name) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"filesystem skill '{name}' not found")
    try:
        skill = manager.write_skill(name, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"success": True, "skill": {"name": skill.name, "description": skill.description}}


@router.delete("/fs/{name}", response_model=Dict[str, Any])
async def delete_fs_skill(name: str) -> Dict[str, Any]:
    """Delete a filesystem (markdown) skill by removing its directory."""
    manager = get_default_skill_manager()
    deleted = manager.delete_skill(name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"filesystem skill '{name}' not found")
    return {"success": True, "deleted": name}


@router.delete("/{name}", response_model=Dict[str, Any])
async def delete_skill(name: str) -> Dict[str, Any]:
    deleted = await skill_service.delete(name)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"skill '{name}' not found")
    return {"success": True, "deleted": name}
