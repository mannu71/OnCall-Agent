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


# Ceiling on how many body-token matches can count toward a skill's score, so a
# long runbook can't win on verbosity alone (see ``select_for_query``).
_BODY_OVERLAP_CAP = 4


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
    # When the skill applies, in the model's own trigger vocabulary. Ranked by
    # ``search_skills`` and shown in its results so the model can decide
    # whether to load the skill.
    when_to_use: str = ""
    # If True the skill is hidden from the model-facing map, search and skill
    # tool (still invocable by an explicit user /slash-command).
    disable_model_invocation: bool = False
    # Optional hint about the arguments the skill expects (shown to authors).
    argument_hint: str = ""


class SkillManager:
    """Manage skill loading and invocation.

    Skills are markdown files with YAML frontmatter that define
    reusable prompt templates for common workflows.

    Supports:
    - Dynamic skill loading from SKILL.md files
    - A per-turn map (names only) + query ranking for two-stage disclosure
    - Slash command resolution (/skill-name)
    - Full-body invocation with config-variable and $ARGUMENTS resolution
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        """Initialize SkillManager.

        Args:
            skills_dir: Directory containing skill files (default: data/skills)
        """
        self.skills_dir = skills_dir or Path("data/skills")
        self._skills: Dict[str, Skill] = {}
        self._loaded = False
        # Signature of the on-disk skill set at last scan (mtimes of the source
        # dirs + each SKILL.md + the disabled marker). Lets ``maybe_rescan``
        # cheaply detect out-of-band file changes without a full re-parse.
        self._signature: Optional[tuple] = None
    
    def scan_skills(self) -> Dict[str, Skill]:
        """Scan the skills directory for SKILL.md files.

        Skills are entirely user-authored under ``self.skills_dir`` — nothing
        ships with the app, so this is the single source. (An earlier bundled
        "seed cookbook" and its hide-a-packaged-skill marker file were removed:
        every skill now lives in a writable directory and is deleted by simply
        removing it.)

        Returns:
            Dict mapping skill names to Skill objects
        """
        self._skills.clear()

        source_dir = self.skills_dir
        if not source_dir.exists():
            logger.warning(f"Skills directory does not exist: {source_dir}")
        else:
            for skill_file in source_dir.rglob("SKILL.md"):
                try:
                    skill = self._load_skill_file(skill_file)
                    if skill:
                        self._skills[skill.name] = skill
                        logger.info(f"Loaded skill: {skill.name} (from {source_dir})")
                except Exception as e:
                    logger.error(f"Failed to load skill from {skill_file}: {e}")

        self._loaded = True
        self._signature = self._dir_signature()
        logger.info(f"Loaded {len(self._skills)} skills (from {self.skills_dir})")
        return self._skills

    def _dir_signature(self) -> tuple:
        """A cheap fingerprint of the on-disk skill set (paths + mtimes).

        Walks the same ``SKILL.md`` files ``scan_skills`` would, recording each
        path and mtime. Comparing two signatures detects added/removed/edited
        skills without re-reading or re-parsing any file — used by
        :meth:`maybe_rescan`. Best-effort: any stat error is folded into the
        signature so it simply forces a rescan.
        """
        parts: List[tuple] = []
        source_dir = self.skills_dir
        try:
            if source_dir.exists():
                for skill_file in sorted(source_dir.rglob("SKILL.md")):
                    try:
                        parts.append((str(skill_file), skill_file.stat().st_mtime_ns))
                    except OSError as exc:
                        parts.append((str(skill_file), repr(exc)))
        except OSError as exc:
            parts.append((str(source_dir), repr(exc)))
        return tuple(parts)

    def maybe_rescan(self) -> bool:
        """Re-scan only if the on-disk skill set changed since the last scan.

        Cheap (a directory walk + stat per file, no read/parse) so it is safe on
        API-facing list paths; the agent hot path deliberately does NOT call this
        (it runs on the cached set, refreshed by API writes or an explicit
        rescan) to keep per-turn overhead at zero. Returns True if a rescan ran.
        """
        if not self._loaded:
            self.scan_skills()
            return True
        if self._dir_signature() != self._signature:
            logger.info("SkillManager: on-disk skills changed — rescanning")
            self.scan_skills()
            return True
        return False

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

        # Optional disclosure fields (all default-absent so existing seed skills
        # with only name/description/config load unchanged). Accept both hyphen
        # and underscore spellings for author convenience.
        def _fm(*keys: str, default: Any = "") -> Any:
            for k in keys:
                if k in frontmatter and frontmatter[k] is not None:
                    return frontmatter[k]
            return default

        when_to_use = str(_fm("when_to_use", "when-to-use") or "").strip()
        argument_hint = str(_fm("argument_hint", "argument-hint") or "").strip()
        disable_model_invocation = bool(
            _fm("disable_model_invocation", "disable-model-invocation", default=False)
        )

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
            when_to_use=when_to_use,
            disable_model_invocation=disable_model_invocation,
            argument_hint=argument_hint,
        )
    
    def select_for_query(
        self, query: str, k: int = 2, allowed: Optional[Set[str]] = None,
    ) -> List[Skill]:
        """Return up to *k* skills most relevant to *query* — the ``search_skills`` ranker.

        Stage one of skill disclosure is a names-only map, so this is how the
        model turns an intent ("triage errors in logs") into the right skill
        name to load. Lexical relevance over each skill's name, description,
        ``when_to_use`` and body (token overlap, weighted toward the name).
        Deterministic and dependency-free — no DB or embedding call on the hot
        path. A future upgrade can swap in the Titan embeddings used by semantic
        memory; the call site is stable.

        ``allowed`` scopes the candidate set (per-agent Skills picker):
        ``None`` = no scoping, every loaded skill is a candidate (the
        global-default behavior); an explicit ``set()`` = nothing is invocable,
        so the result is empty. The empty-set case must NOT read as "no scoping"
        — a scoped agent whose effective set is empty would otherwise see every
        skill. Callers therefore pass the exact invocable set, not a falsy stand-in.
        """
        if not self._loaded:
            self.scan_skills()
        q_tokens = _tokenize(query)
        if not q_tokens or not self._skills:
            return []
        candidates = (
            list(self._skills.values())
            if allowed is None
            else [s for s in self._skills.values() if s.name in allowed]
        )
        scored: List[tuple] = []
        for skill in candidates:
            name_tokens = _tokenize(skill.name.replace("-", " ").replace("_", " "))
            desc_tokens = _tokenize(skill.description)
            # when_to_use is authored as trigger vocabulary ("when the user asks
            # about X") — exactly the words a search query uses, so it scores
            # alongside the description rather than being ignored.
            wtu_tokens = _tokenize(skill.when_to_use)
            # Body tokens let the skill's own "When this triggers" section drive
            # matching — critical when the frontmatter description is thin or a
            # placeholder (the body carries the real trigger vocabulary).
            body_tokens = _tokenize(skill.content)
            # Name = strong signal; description/when_to_use = supporting; body =
            # weak but wide. The body term is CLAMPED: overlap is a set
            # intersection, so a long runbook has a wide token set and would
            # otherwise accrue body points from incidental word matches — enough
            # to outvote a genuine name/description hit purely by being verbose.
            # Capping it keeps the body a tie-breaker, not a length reward.
            score = (
                2.0 * len(q_tokens & name_tokens)
                + 1.0 * len(q_tokens & desc_tokens)
                + 1.0 * len(q_tokens & wtu_tokens)
                + 0.5 * min(len(q_tokens & body_tokens), _BODY_OVERLAP_CAP)
            )
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
        
        # Body: substitute ``$ARGUMENTS`` inline when the runbook uses it,
        # otherwise fall back to appending a "## User Request" section below.
        body = skill.content
        args_substituted = False
        if "$ARGUMENTS" in body:
            body = body.replace("$ARGUMENTS", user_instruction or "")
            args_substituted = True

        # Build message
        message_parts = [
            f"# Skill: {skill.name}",
            "",
            body,
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

        # Add user instruction if present (and not already substituted inline)
        if user_instruction and not args_substituted:
            message_parts.extend([
                "",
                "## User Request",
                "",
                user_instruction,
            ])

        return "\n".join(message_parts)

    def _invocable_skills(self, allowed: Optional[Set[str]] = None) -> List["Skill"]:
        """Loaded skills the model may invoke, honouring per-agent ``allowed`` scoping.

        ``allowed=None`` = unscoped (all non-hidden skills); an explicit
        ``set()`` = nothing invocable (empty result). Distinguishing the two
        matters for a scoped agent whose effective set is empty — it must see
        no skills, not every skill.
        """
        if not self._loaded:
            self.scan_skills()
        return [
            s for s in self._skills.values()
            if not s.disable_model_invocation
            and (allowed is None or s.name in allowed)
        ]

    def model_invocable_names(self, allowed: Optional[Set[str]] = None) -> Set[str]:
        """Names of skills the model may invoke (``disable_model_invocation`` +
        ``allowed`` filtered). The candidate set behind ``search_skills``."""
        return {s.name for s in self._invocable_skills(allowed)}

    def build_map(
        self,
        allowed: Optional[Set[str]] = None,
        char_budget: int = 1500,
    ) -> str:
        """Return the model-facing skill MAP: invocable skill names, comma-joined.

        Stage one of skill disclosure — names only (no descriptions), so the
        per-turn cost stays near zero. The model turns an intent into a name via
        the ``search_skills`` tool, then loads the runbook with ``skill``.
        Skills flagged ``disable_model_invocation`` are excluded — the model
        cannot invoke them, so naming them is noise.

        Over ``char_budget`` the map degrades to a count-only hint (``"<N> skills
        available"``): the names stop being useful once there are too many to
        read, but the model must still know the library exists and is
        searchable. Empty (no invocable skills) → ``""``.

        ``allowed``: when non-empty, only skills whose name is in this set are
        mapped (per-agent scoping via the Agent node's Skills picker).
        """
        skills = self._invocable_skills(allowed)
        if not skills:
            return ""

        # Alphabetical — a stable order so the block doesn't churn between turns.
        skills.sort(key=lambda s: s.name)

        names = ", ".join(s.name for s in skills)
        if len(names) > char_budget:
            return f"{len(skills)} skills available"
        return names

    def list_skills(self) -> List[Dict[str, Any]]:
        """List available skills with descriptions.

        Every skill is user-authored (nothing ships with the app), so there is
        no origin/bundled distinction to report.

        Returns:
            List of dicts with 'name', 'description', 'when_to_use',
            'argument_hint' and 'disable_model_invocation' keys.
        """
        # Ensure skills are loaded (first use). NB: no maybe_rescan() here — this
        # is called on the agent's conversational fast-path too, so it must stay
        # zero-I/O once loaded. The REST list endpoint calls maybe_rescan()
        # explicitly to surface out-of-band file drops in the UI.
        if not self._loaded:
            self.scan_skills()

        return [
            {
                "name": skill.name,
                "description": skill.description,
                "when_to_use": skill.when_to_use,
                "argument_hint": skill.argument_hint,
                "disable_model_invocation": skill.disable_model_invocation,
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
    
    def get_skill_source(self, name: str) -> Optional[Dict[str, Any]]:
        """Return the raw SKILL.md source for *name*, for viewing/editing.

        ``editable`` is always True: every skill is user-authored under the
        writable ``skills_dir`` (nothing ships read-only inside the package),
        so the UI can always edit in place. The key is kept in the payload so
        existing callers/UI don't need to special-case its absence.
        """
        if not self._loaded:
            self.scan_skills()

        skill = self._skills.get(name)
        if skill is None:
            return None

        skill_file = skill.skill_dir / "SKILL.md"
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("SkillManager: failed to read %s: %s", skill_file, exc)
            return None

        return {
            "name": skill.name,
            "content": content,
            "editable": True,
        }

    def write_skill(self, name: str, content: str) -> Skill:
        """Create or overwrite a user-authored SKILL.md file under ``skills_dir``.

        Always writes under ``self.skills_dir`` — the single, writable skill
        source. Raises ``ValueError`` if *content* has no valid YAML
        frontmatter or is missing the required ``name`` field.
        """
        slug = re.sub(r"[^\w-]", "_", name.strip().lower()) or "unnamed_skill"
        skill_dir = self.skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")

        loaded = self._load_skill_file(skill_file)
        if loaded is None:
            skill_file.unlink(missing_ok=True)
            raise ValueError(
                "Invalid SKILL.md content — requires YAML frontmatter with a 'name' field"
            )

        # Frontmatter lint: the per-turn map carries names only, so a skill is
        # found via ``search_skills`` — which ranks the description (+ optional
        # when_to_use) far above the body. A skill with no description is
        # therefore near-unfindable noise, so reject it outright. A thin
        # description or a missing when_to_use still works but triggers poorly,
        # so warn rather than block (authors/drafters may iterate).
        if not (loaded.description or "").strip():
            skill_file.unlink(missing_ok=True)
            raise ValueError(
                "Skill 'description' is required and cannot be empty — it is the only "
                "text the agent sees when deciding whether to use this skill."
            )
        if len(loaded.description.strip()) < 20:
            logger.warning(
                "SkillManager: skill '%s' has a very short description (%d chars) — "
                "the agent may not reliably select it; describe when to use it.",
                loaded.name, len(loaded.description.strip()),
            )
        if not (loaded.when_to_use or "").strip():
            logger.info(
                "SkillManager: skill '%s' has no 'when_to_use' — consider adding one "
                "to improve the agent's selection accuracy.", loaded.name,
            )

        # Rename guard: an edit that changes the frontmatter ``name`` writes the
        # SAME directory but loads under a NEW name. Drop any other cached entry
        # backed by this directory, or the old name lingers in the map as a ghost
        # pointing at the rewritten file until the next full rescan.
        for _stale in [
            n for n, s in self._skills.items()
            if n != loaded.name and s.skill_dir == loaded.skill_dir
        ]:
            del self._skills[_stale]
            logger.info(
                "SkillManager: dropped stale entry '%s' after rename to '%s'",
                _stale, loaded.name,
            )

        self._skills[loaded.name] = loaded
        self._loaded = True
        logger.info("SkillManager: wrote filesystem skill '%s' to %s", loaded.name, skill_file)
        return loaded

    def delete_skill(self, name: str) -> bool:
        """Delete a skill. Returns True if found and removed, False otherwise.

        Never raises — logs errors instead. Every skill is user-authored under
        the writable ``skills_dir``, so delete simply removes its directory —
        no packaged/read-only case to hide around.
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
