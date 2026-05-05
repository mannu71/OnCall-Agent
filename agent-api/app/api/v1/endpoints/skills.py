"""Skills API endpoints for managing and invoking skills."""

from typing import Dict, List, Optional
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field

from app.config import settings
from app.core.skills.manager import SkillManager


router = APIRouter(prefix="/skills", tags=["skills"])


# Response models
class SkillInfo(BaseModel):
    """Skill information response."""
    name: str = Field(..., description="Skill name")
    description: str = Field(..., description="Skill description")


class SkillInvocationRequest(BaseModel):
    """Request body for skill invocation."""
    instruction: str = Field(
        default="",
        description="Additional user instruction to append to skill content"
    )
    config: Optional[Dict[str, str]] = Field(
        default=None,
        description="Configuration overrides for skill variables"
    )


class SkillInvocationResponse(BaseModel):
    """Response from skill invocation."""
    skill_name: str = Field(..., description="Name of the invoked skill")
    message: str = Field(..., description="Formatted skill message content")
    config_resolved: Dict[str, str] = Field(
        default_factory=dict,
        description="Resolved configuration values"
    )


# Initialize skill manager (singleton pattern)
_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """Get or create the skill manager instance."""
    global _skill_manager
    if _skill_manager is None:
        skills_dir = Path(settings.skills_dir)
        _skill_manager = SkillManager(skills_dir=skills_dir)
        _skill_manager.scan_skills()
    return _skill_manager


@router.get("", response_model=List[SkillInfo])
async def list_skills() -> List[SkillInfo]:
    """List all available skills.
    
    Returns a list of skills with their names and descriptions.
    Skills are loaded from the configured skills directory.
    
    Returns:
        List of SkillInfo objects containing skill metadata
    """
    manager = get_skill_manager()
    skills_data = manager.list_skills()
    
    return [
        SkillInfo(name=skill["name"], description=skill["description"])
        for skill in skills_data
    ]


@router.post("/{skill_name}/invoke", response_model=SkillInvocationResponse)
async def invoke_skill(
    skill_name: str,
    request: SkillInvocationRequest = Body(...)
) -> SkillInvocationResponse:
    """Invoke a skill and return the formatted message content.
    
    This endpoint resolves the skill by name, applies configuration overrides,
    and returns the formatted skill content that can be used as a system message
    or injected into the conversation context.
    
    Args:
        skill_name: Name of the skill to invoke
        request: Invocation request with optional instruction and config overrides
    
    Returns:
        SkillInvocationResponse with formatted message content
    
    Raises:
        HTTPException: 404 if skill not found
    """
    manager = get_skill_manager()
    
    # Get the skill
    skill = manager.get_skill(skill_name)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill_name}' not found"
        )
    
    # Build the invocation message
    message = manager.build_invocation_message(
        skill=skill,
        user_instruction=request.instruction,
        config_overrides=request.config,
    )
    
    # Resolve config for response (merge defaults with overrides)
    config_resolved = {}
    if isinstance(skill.config_vars, dict):
        config_resolved.update(skill.config_vars)
    if request.config:
        config_resolved.update(request.config)
    
    return SkillInvocationResponse(
        skill_name=skill.name,
        message=message,
        config_resolved=config_resolved,
    )


@router.get("/{skill_name}", response_model=SkillInfo)
async def get_skill_info(skill_name: str) -> SkillInfo:
    """Get information about a specific skill.
    
    Args:
        skill_name: Name of the skill
    
    Returns:
        SkillInfo with skill metadata
    
    Raises:
        HTTPException: 404 if skill not found
    """
    manager = get_skill_manager()
    
    skill = manager.get_skill(skill_name)
    if not skill:
        raise HTTPException(
            status_code=404,
            detail=f"Skill '{skill_name}' not found"
        )
    
    return SkillInfo(
        name=skill.name,
        description=skill.description,
    )
