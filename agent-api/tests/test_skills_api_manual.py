"""Manual integration test for Skills API endpoints.

This test can be run manually when the server is running to verify
the skills API endpoints work correctly.

Usage:
    python tests/test_skills_api_manual.py
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings
from app.core.skills.manager import SkillManager, Skill


async def test_skill_manager():
    """Test the SkillManager directly."""
    print("Testing SkillManager...")
    
    # Create a test skill directory
    test_skills_dir = Path("data/skills_test")
    test_skills_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a test skill file
    test_skill_path = test_skills_dir / "test-skill" / "SKILL.md"
    test_skill_path.parent.mkdir(parents=True, exist_ok=True)
    
    test_skill_content = """---
name: test-skill
description: A test skill for verification
config:
  api_key: default_key
  region: us-east-1
---

# Test Skill

This is a test skill content for verification purposes.

## Setup
Set the following environment variables:
- `API_KEY`: Your API key
- `REGION`: Your AWS region
"""
    
    test_skill_path.write_text(test_skill_content, encoding="utf-8")
    
    # Initialize manager
    manager = SkillManager(skills_dir=test_skills_dir)
    
    # Test scan_skills
    print("\n1. Testing scan_skills()...")
    skills = manager.scan_skills()
    print(f"   Found {len(skills)} skills")
    assert len(skills) == 1, f"Expected 1 skill, found {len(skills)}"
    print("   ✓ scan_skills() works")
    
    # Test list_skills
    print("\n2. Testing list_skills()...")
    skills_list = manager.list_skills()
    print(f"   Skills: {skills_list}")
    assert len(skills_list) == 1, f"Expected 1 skill, found {len(skills_list)}"
    assert skills_list[0]["name"] == "test-skill"
    print("   ✓ list_skills() works")
    
    # Test get_skill
    print("\n3. Testing get_skill()...")
    skill = manager.get_skill("test-skill")
    assert skill is not None, "Skill not found"
    assert skill.name == "test-skill"
    assert skill.description == "A test skill for verification"
    print(f"   Skill: {skill.name} - {skill.description}")
    print("   ✓ get_skill() works")
    
    # Test build_invocation_message
    print("\n4. Testing build_invocation_message()...")
    message = manager.build_invocation_message(
        skill=skill,
        user_instruction="Test instruction",
        config_overrides={"region": "eu-west-1"}
    )
    print(f"   Message length: {len(message)} chars")
    assert "# Skill: test-skill" in message
    assert "Test instruction" in message
    assert "eu-west-1" in message
    print("   ✓ build_invocation_message() works")
    
    # Test resolve_command
    print("\n5. Testing resolve_command()...")
    resolved = manager.resolve_command("/test-skill")
    assert resolved is not None, "Command not resolved"
    assert resolved.name == "test-skill"
    print(f"   Resolved: {resolved.name}")
    print("   ✓ resolve_command() works")
    
    # Cleanup
    import shutil
    shutil.rmtree(test_skills_dir)
    print("\n✓ All SkillManager tests passed!")


def test_api_endpoints_structure():
    """Test that the API endpoints are properly structured."""
    print("\nTesting API endpoint structure...")
    
    from app.api.v1.endpoints import skills
    
    # Check router exists
    assert hasattr(skills, "router"), "Router not found"
    print("   ✓ Router exists")
    
    # Check endpoints are registered
    routes = [route.path for route in skills.router.routes]
    print(f"   Routes: {routes}")
    
    expected_routes = ["/skills", "/skills/{skill_name}/invoke", "/skills/{skill_name}"]
    for expected in expected_routes:
        assert expected in routes, f"Route {expected} not found"
    print("   ✓ All expected routes registered")
    
    # Check response models
    from app.api.v1.endpoints.skills import SkillInfo, SkillInvocationRequest, SkillInvocationResponse
    print("   ✓ Response models defined")
    
    print("\n✓ API endpoint structure tests passed!")


if __name__ == "__main__":
    print("=" * 60)
    print("Skills API Manual Integration Test")
    print("=" * 60)
    
    try:
        # Test SkillManager
        asyncio.run(test_skill_manager())
        
        # Test API structure
        test_api_endpoints_structure()
        
        print("\n" + "=" * 60)
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
