"""Skills API — manage file-based markdown (``SKILL.md``) skills.

Skills are markdown files with YAML frontmatter (``SkillManager``). They are
reusable guidance the agent auto-selects per query (RAG) or is invoked by
slash-command. There is no database: these endpoints view, create, edit, and
delete the ``SKILL.md`` files on disk.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.skills import get_default_skill_manager

router = APIRouter(prefix="/skills", tags=["skills"])
logger = logging.getLogger(__name__)


class FsSkillBody(BaseModel):
    name: str = Field(..., description="Skill name (slugified into the directory name)")
    content: str = Field(..., description="Full SKILL.md content, including YAML frontmatter")


class FsSkillUpdateBody(BaseModel):
    content: str = Field(..., description="Full SKILL.md content, including YAML frontmatter")


@router.get("", response_model=Dict[str, Any])
async def list_skills() -> Dict[str, Any]:
    """List the file-based markdown (SKILL.md) skills."""
    try:
        fs_skills = get_default_skill_manager().list_skills()
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills: filesystem list failed (%s)", exc)
        fs_skills = []
    return {"success": True, "filesystem": fs_skills}


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
