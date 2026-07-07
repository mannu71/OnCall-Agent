"""Skill system — file-based markdown commands (SkillManager).

Skills are ``SKILL.md`` markdown files (YAML frontmatter + body) discovered on
disk. There is NO database and no separate "executable" skill store — a skill is
reusable guidance the agent follows, auto-selected per query (RAG) or invoked by
slash-command. See :mod:`app.core.skills.manager`.
"""

from pathlib import Path
from typing import Optional

from .manager import Skill, SkillManager

__all__ = [
    "Skill",
    "SkillManager",
    "get_default_skill_manager",
]

# Process-wide markdown-skill manager, scanned once. Shared by the skills API
# and the agent recall path (RAG auto-selection) so skills load a single time.
_default_manager: Optional[SkillManager] = None


def get_default_skill_manager() -> SkillManager:
    """Return the shared SkillManager, scanning the skills dir on first use."""
    global _default_manager
    if _default_manager is None:
        from app.config import settings
        mgr = SkillManager(skills_dir=Path(settings.skills_dir))
        mgr.scan_skills()
        _default_manager = mgr
    return _default_manager
