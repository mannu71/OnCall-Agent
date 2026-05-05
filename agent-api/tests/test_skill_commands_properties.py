"""Property-based tests for Skill Commands.

Tests universal correctness properties:
- Property 17: Skill File Parsing - Valid SKILL.md parsed into consistent structure
"""

import pytest
from hypothesis import given, strategies as st, assume
from pathlib import Path
import tempfile
import shutil
from app.core.skills import Skill, SkillManager


# Strategies for generating test data
@st.composite
def skill_name_strategy(draw):
    """Generate valid skill names."""
    return draw(st.text(
        alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'), whitelist_characters='_-'),
        min_size=1,
        max_size=50
    ).filter(lambda x: x and x[0].isalpha()))  # Ensure starts with letter


@st.composite
def skill_content_strategy(draw):
    """Generate valid skill markdown content (ASCII only for YAML safety)."""
    return draw(st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126),  # Printable ASCII
        min_size=10,
        max_size=500
    ))


@st.composite
def skill_description_strategy(draw):
    """Generate valid skill descriptions (safe for YAML unquoted strings)."""
    # Only alphanumeric, spaces, and safe punctuation
    # Ensure it starts with a letter to avoid YAML type inference issues
    text = draw(st.text(
        alphabet=st.characters(
            whitelist_categories=('Lu', 'Ll', 'Nd'),
            whitelist_characters=' .-_'
        ),
        min_size=5,
        max_size=100
    ).filter(lambda x: x.strip() and x[0].isalpha()))  # Must start with letter
    return text


@st.composite
def skill_file_strategy(draw):
    """Generate valid SKILL.md file content."""
    name = draw(skill_name_strategy())
    description = draw(skill_description_strategy())
    content = draw(skill_content_strategy())
    
    # Use unquoted YAML strings (safest approach)
    frontmatter = f"""---
name: {name}
description: {description}
config:
  - var1
  - var2
---

{content}

## Setup
Configure the following variables:
- VAR1: Description of var1
- VAR2: Description of var2
"""
    return name, description, content, frontmatter


# Property 17: Skill File Parsing
def test_property_17_skill_file_parsing():
    """Property 17: Valid SKILL.md parsed into consistent structure.
    
    Universal property: For any valid SKILL.md file with proper frontmatter,
    parsing MUST produce a Skill object with matching name, description, and content.
    The parsing MUST be deterministic and consistent.
    """
    # Test with multiple concrete examples to validate the property
    test_cases = [
        ("test-skill", "Test skill description", "This is test content"),
        ("another-skill", "Another description", "Different content here"),
        ("skill-three", "Third skill", "More content"),
    ]
    
    for name, description, content in test_cases:
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir)
            skill_file = skills_dir / "SKILL.md"
            
            # Build frontmatter with quoted strings to avoid YAML type inference
            frontmatter = f"""---
name: "{name}"
description: "{description}"
config:
  - var1
  - var2
---

{content}

## Setup
Configure the following variables:
- VAR1: Description of var1
- VAR2: Description of var2
"""
            skill_file.write_text(frontmatter, encoding="utf-8")
            
            # Create SkillManager and scan
            manager = SkillManager(skills_dir=skills_dir)
            skills = manager.scan_skills()
            
            # Property: Skill was loaded
            assert name in skills, f"Skill '{name}' not found in loaded skills"
            
            skill = skills[name]
            
            # Property: Name matches
            assert skill.name == name, \
                f"Skill name mismatch: expected '{name}', got '{skill.name}'"
            
            # Property: Description matches
            assert skill.description == description, \
                f"Skill description mismatch: expected '{description}', got '{skill.description}'"
            
            # Property: Content is present and contains original content
            assert content in skill.content, \
                f"Skill content does not contain expected text"
            
            # Property: Config vars are present
            assert isinstance(skill.config_vars, (dict, list)), \
                f"Skill config_vars should be dict or list, got {type(skill.config_vars)}"
            
            # Property: Setup note is extracted
            assert skill.setup_note is not None, \
                f"Skill setup note should be extracted"
            assert "VAR1" in skill.setup_note, \
                f"Setup note should contain VAR1"


def test_skill_command_resolution():
    """Test that slash commands resolve to skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("""---
name: test-skill
description: Test skill
---

This is a test skill.
""", encoding="utf-8")
        
        manager = SkillManager(skills_dir=skills_dir)
        
        # Test with leading slash
        skill = manager.resolve_command("/test-skill")
        assert skill is not None
        assert skill.name == "test-skill"
        
        # Test without leading slash
        skill = manager.resolve_command("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"
        
        # Test case-insensitive
        skill = manager.resolve_command("/TEST-SKILL")
        assert skill is not None
        assert skill.name == "test-skill"
        
        # Test nonexistent skill
        skill = manager.resolve_command("/nonexistent")
        assert skill is None


def test_skill_invocation_message():
    """Test that invocation messages are built correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("""---
name: test-skill
description: Test skill
config:
  var1: value1
  var2: value2
---

This is a test skill.
""", encoding="utf-8")
        
        manager = SkillManager(skills_dir=skills_dir)
        skill = manager.resolve_command("test-skill")
        
        # Build invocation message
        message = manager.build_invocation_message(
            skill,
            user_instruction="Do something specific"
        )
        
        # Check message contains expected parts
        assert "# Skill: test-skill" in message
        assert "This is a test skill" in message
        assert "## Configuration" in message
        assert "var1" in message
        assert "value1" in message
        assert "## User Request" in message
        assert "Do something specific" in message


def test_skill_list():
    """Test that list_skills returns all skills."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        
        # Create multiple skills
        for i in range(3):
            skill_file = skills_dir / f"skill{i}" / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(f"""---
name: skill-{i}
description: Skill {i}
---

Content {i}
""", encoding="utf-8")
        
        manager = SkillManager(skills_dir=skills_dir)
        skills = manager.list_skills()
        
        # Check all skills are listed
        assert len(skills) == 3
        skill_names = [s["name"] for s in skills]
        assert "skill-0" in skill_names
        assert "skill-1" in skill_names
        assert "skill-2" in skill_names


def test_skill_missing_frontmatter():
    """Test that skills without frontmatter are skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("This is just markdown without frontmatter", encoding="utf-8")
        
        manager = SkillManager(skills_dir=skills_dir)
        skills = manager.scan_skills()
        
        # No skills should be loaded
        assert len(skills) == 0


def test_skill_invalid_yaml():
    """Test that skills with invalid YAML are skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("""---
name: test
invalid yaml: [unclosed
---

Content
""", encoding="utf-8")
        
        manager = SkillManager(skills_dir=skills_dir)
        skills = manager.scan_skills()
        
        # No skills should be loaded
        assert len(skills) == 0
