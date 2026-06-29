"""Skill Manager for loading and executing markdown-based skills."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import logging
import yaml
import re
import os

logger = logging.getLogger(__name__)

# Short, low-signal words ignored when scoring skill relevance.
_STOPWORDS = frozenset({
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
    "with", "how", "do", "i", "my", "this", "that", "it", "be", "can", "what",
})


def _tokenize(text: str):
    """Lowercase word tokens (≥3 chars, non-stopword) as a set, for overlap scoring."""
    if not text:
        return set()
    return {
        w for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) >= 3 and w not in _STOPWORDS
    }


@dataclass
class Skill:
    """Loaded skill definition."""
    name: str
    description: str
    content: str
    skill_dir: Path
    config_vars: Dict[str, Any]
    setup_note: Optional[str] = None


class SkillManager:
    """Manage skill loading and execution.
    
    Skills are markdown files with YAML frontmatter that define
    reusable prompt templates for common workflows.
    
    Supports:
    - Dynamic skill loading from SKILL.md files
    - Slash command resolution (/skill-name)
    - Config variable resolution from environment and settings
    - Preloaded skills for session-wide activation
    - Disabled skills filtering
    """
    
    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        disabled_skills: Optional[Set[str]] = None,
        preloaded_skills: Optional[List[str]] = None,
    ):
        """Initialize SkillManager.
        
        Args:
            skills_dir: Directory containing skill files (default: data/skills)
            disabled_skills: Set of skill names to skip loading
            preloaded_skills: List of skill names to preload for session
        """
        self.skills_dir = skills_dir or Path("data/skills")
        self._skills: Dict[str, Skill] = {}
        self._loaded = False
        self._disabled_skills = disabled_skills or set()
        self._preloaded_skills = preloaded_skills or []
    
    def scan_skills(self) -> Dict[str, Skill]:
        """Scan skills directory for SKILL.md files.
        
        Skips skills that are in the disabled_skills set.
        
        Returns:
            Dict mapping skill names to Skill objects
        """
        self._skills.clear()
        
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory does not exist: {self.skills_dir}")
            return self._skills
        
        # Find all SKILL.md files recursively
        skill_files = list(self.skills_dir.rglob("SKILL.md"))
        
        for skill_file in skill_files:
            try:
                skill = self._load_skill_file(skill_file)
                if skill:
                    # Skip disabled skills (Requirement 7.8)
                    if skill.name in self._disabled_skills:
                        logger.info(f"Skipping disabled skill: {skill.name}")
                        continue
                    
                    self._skills[skill.name] = skill
                    logger.info(f"Loaded skill: {skill.name}")
            except Exception as e:
                logger.error(f"Failed to load skill from {skill_file}: {e}")
        
        self._loaded = True
        logger.info(f"Loaded {len(self._skills)} skills from {self.skills_dir}")
        return self._skills
    
    def _load_skill_file(self, skill_file: Path) -> Optional[Skill]:
        """Load a single SKILL.md file.
        
        Args:
            skill_file: Path to SKILL.md file
            
        Returns:
            Skill object or None if invalid
        """
        content = skill_file.read_text(encoding="utf-8")
        
        # Parse YAML frontmatter
        frontmatter_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
        
        if not frontmatter_match:
            logger.warning(f"Skill file {skill_file} missing YAML frontmatter")
            return None
        
        frontmatter_text = frontmatter_match.group(1)
        markdown_content = frontmatter_match.group(2).strip()
        
        try:
            frontmatter = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML frontmatter in {skill_file}: {e}")
            return None
        
        # Extract required fields
        name = frontmatter.get("name")
        description = frontmatter.get("description", "")
        config_vars = frontmatter.get("config", {})
        
        if not name:
            logger.warning(f"Skill file {skill_file} missing 'name' in frontmatter")
            return None
        
        # Extract setup note if present
        setup_note = None
        setup_match = re.search(r'## Setup\s*\n(.*?)(?=\n##|\Z)', markdown_content, re.DOTALL)
        if setup_match:
            setup_note = setup_match.group(1).strip()
        
        return Skill(
            name=name,
            description=description,
            content=markdown_content,
            skill_dir=skill_file.parent,
            config_vars=config_vars,
            setup_note=setup_note,
        )
    
    def select_for_query(self, query: str, k: int = 2) -> List[Skill]:
        """Return up to *k* skills most relevant to *query* (RAG-style auto-select).

        Lexical relevance over each skill's name + description (token overlap,
        weighted toward the name). Deterministic and dependency-free — no DB or
        embedding call on the hot path. A future upgrade can swap in the Titan
        embeddings used by semantic memory; the call site is stable.
        """
        if not self._loaded:
            self.scan_skills()
        q_tokens = _tokenize(query)
        if not q_tokens or not self._skills:
            return []
        scored: List[tuple] = []
        for skill in self._skills.values():
            name_tokens = _tokenize(skill.name.replace("-", " ").replace("_", " "))
            desc_tokens = _tokenize(skill.description)
            # Name matches are strong signals; description matches are supporting.
            score = 2.0 * len(q_tokens & name_tokens) + len(q_tokens & desc_tokens)
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [skill for _, skill in scored[: max(0, k)]]

    def resolve_command(self, command: str) -> Optional[Skill]:
        """Resolve /command to skill.
        
        Args:
            command: Command string (with or without leading /)
            
        Returns:
            Skill object or None if not found
        """
        # Ensure skills are loaded
        if not self._loaded:
            self.scan_skills()
        
        # Strip leading slash if present
        command = command.lstrip("/")
        
        # Try exact match first
        if command in self._skills:
            return self._skills[command]
        
        # Try case-insensitive match
        command_lower = command.lower()
        for skill_name, skill in self._skills.items():
            if skill_name.lower() == command_lower:
                return skill
        
        return None
    
    def build_invocation_message(
        self,
        skill: Skill,
        user_instruction: str = "",
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build message content for skill invocation.
        
        Resolves config variables from:
        1. Environment variables (highest priority)
        2. Config overrides passed as argument
        3. Skill default config_vars
        
        Args:
            skill: Skill to invoke
            user_instruction: Additional user instruction to append
            config_overrides: Config values to override defaults
            
        Returns:
            Formatted message content with resolved config variables
        """
        # Resolve config variables (Requirement 7.4)
        config = {}
        
        # Start with skill defaults
        if isinstance(skill.config_vars, dict):
            for key, default_value in skill.config_vars.items():
                config[key] = default_value
        
        # Override with config_overrides
        if config_overrides:
            config.update(config_overrides)
        
        # Override with environment variables (highest priority)
        # Look for env vars matching config keys (case-insensitive)
        for key in list(config.keys()):
            env_key = key.upper()
            if env_key in os.environ:
                config[key] = os.environ[env_key]
                logger.debug(f"Resolved config '{key}' from environment variable '{env_key}'")
        
        # Build message
        message_parts = [
            f"# Skill: {skill.name}",
            "",
            skill.content,
        ]
        
        # Add config section if present
        if config:
            message_parts.extend([
                "",
                "## Configuration",
                "",
            ])
            for key, value in config.items():
                message_parts.append(f"- **{key}**: {value}")
        
        # Add user instruction if present
        if user_instruction:
            message_parts.extend([
                "",
                "## User Request",
                "",
                user_instruction,
            ])
        
        return "\n".join(message_parts)
    
    def list_skills(self) -> List[Dict[str, str]]:
        """List available skills with descriptions.
        
        Returns:
            List of dicts with 'name' and 'description' keys
        """
        # Ensure skills are loaded
        if not self._loaded:
            self.scan_skills()
        
        return [
            {
                "name": skill.name,
                "description": skill.description,
            }
            for skill in self._skills.values()
        ]
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name.
        
        Args:
            name: Skill name
            
        Returns:
            Skill object or None if not found
        """
        if not self._loaded:
            self.scan_skills()
        
        return self._skills.get(name)
    
    def delete_skill(self, name: str) -> bool:
        """Delete a filesystem skill by removing its directory.

        Returns True if the skill was found and deleted, False otherwise.
        Never raises — logs errors instead.
        """
        if not self._loaded:
            self.scan_skills()

        skill = self._skills.get(name)
        if skill is None:
            return False

        try:
            import shutil
            skill_dir = skill.skill_dir
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            del self._skills[name]
            logger.info("SkillManager: deleted filesystem skill '%s' from %s", name, skill_dir)
            return True
        except Exception as exc:
            logger.warning("SkillManager: failed to delete skill '%s': %s", name, exc)
            return False

    def get_preloaded_skills_content(
        self,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Get content for all preloaded skills.
        
        Preloaded skills are activated for the entire session and their
        content is injected into the system prompt or initial context.
        
        Args:
            config_overrides: Config values to override defaults
            
        Returns:
            List of formatted skill content strings (Requirements 7.7, 7.8)
        """
        if not self._loaded:
            self.scan_skills()
        
        preloaded_content = []
        
        for skill_name in self._preloaded_skills:
            skill = self._skills.get(skill_name)
            if skill:
                content = self.build_invocation_message(
                    skill,
                    user_instruction="",
                    config_overrides=config_overrides,
                )
                preloaded_content.append(content)
                logger.info(f"Preloaded skill: {skill_name}")
            else:
                logger.warning(f"Preloaded skill not found: {skill_name}")
        
        return preloaded_content
    
    def has_preloaded_skills(self) -> bool:
        """Check if any skills are configured for preloading.
        
        Returns:
            True if preloaded skills are configured
        """
        return len(self._preloaded_skills) > 0
