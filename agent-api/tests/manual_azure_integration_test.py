"""
Manual Integration Test for Azure DevOps Integration

This script provides a manual testing procedure for the Azure DevOps integration
implemented in Tasks 1, 2, and 2A.

Requirements tested:
- Task 1: ConfigManager (credential storage and encryption)
- Task 2: AzureDevOpsClient (work items and commits)
- Task 2A: AzureWikiClient (wiki operations)

Usage:
    python tests/manual_azure_integration_test.py

Prerequisites:
    1. Set AZURE_DEVOPS_ENCRYPTION_KEY environment variable
    2. Have a valid Azure DevOps PAT with appropriate permissions
    3. Have access to an Azure DevOps organization and project
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.azure_config_manager import ConfigManager
from app.services.azure_devops_client import (
    AzureDevOpsClient,
    AzureDevOpsConnectionError,
    AzureDevOpsAuthenticationError
)
from app.services.azure_wiki_client import (
    AzureWikiClient,
    AzureWikiConnectionError,
    AzureWikiAuthenticationError
)
from app.models.azure_devops import WorkItem, Commit


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'


def print_header(text: str):
    """Print a formatted header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 80}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text.center(80)}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 80}{Colors.END}\n")


def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def print_info(text: str):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ {text}{Colors.END}")


def get_user_input(prompt: str, default: str = None) -> str:
    """Get user input with optional default value"""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    
    value = input(prompt).strip()
    return value if value else default


def test_config_manager():
    """
    Test Task 1: ConfigManager
    
    Tests:
    - Credential encryption and storage
    - Credential retrieval
    - Multiple organization support
    - List organizations
    - Delete credentials
    """
    print_header("Task 1: Testing ConfigManager")
    
    try:
        # Check encryption key
        encryption_key = os.environ.get("AZURE_DEVOPS_ENCRYPTION_KEY")
        if not encryption_key:
            print_error("AZURE_DEVOPS_ENCRYPTION_KEY environment variable not set")
            print_info("Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
            return False
        
        print_success("Encryption key found in environment")
        
        # Initialize ConfigManager with test config path
        test_config_path = "config/test_azure_devops_credentials.json"
        config_manager = ConfigManager(config_path=test_config_path)
        print_success("ConfigManager initialized")
        
        # Test 1: Save credentials
        print_info("\nTest 1: Save credentials for test organization")
        test_org = "test-org"
        test_pat = "test-pat-12345"
        test_repo_id = "test-repo-guid"
        
        config_manager.save_credentials(test_org, test_pat, test_repo_id)
        print_success(f"Saved credentials for organization: {test_org}")
        
        # Test 2: Retrieve credentials
        print_info("\nTest 2: Retrieve credentials")
        creds = config_manager.get_credentials(test_org)
        
        if creds and creds["pat"] == test_pat and creds["repository_id"] == test_repo_id:
            print_success("Retrieved credentials match saved values")
        else:
            print_error("Retrieved credentials do not match")
            return False
        
        # Test 3: List organizations
        print_info("\nTest 3: List organizations")
        orgs = config_manager.list_organizations()
        
        if test_org in orgs:
            print_success(f"Found {len(orgs)} organization(s): {', '.join(orgs)}")
        else:
            print_error("Test organization not found in list")
            return False
        
        # Test 4: Multiple organizations
        print_info("\nTest 4: Save credentials for second organization")
        test_org2 = "test-org-2"
        test_pat2 = "test-pat-67890"
        test_repo_id2 = "test-repo-guid-2"
        
        config_manager.save_credentials(test_org2, test_pat2, test_repo_id2)
        orgs = config_manager.list_organizations()
        
        if len(orgs) == 2 and test_org2 in orgs:
            print_success(f"Multiple organizations supported: {', '.join(orgs)}")
        else:
            print_error("Failed to save second organization")
            return False
        
        # Test 5: Delete credentials
        print_info("\nTest 5: Delete credentials")
        deleted = config_manager.delete_credentials(test_org)
        
        if deleted:
            orgs = config_manager.list_organizations()
            if test_org not in orgs and test_org2 in orgs:
                print_success("Successfully deleted organization credentials")
            else:
                print_error("Organization still exists after deletion")
                return False
        else:
            print_error("Failed to delete organization")
            return False
        
        # Cleanup
        config_manager.delete_credentials(test_org2)
        Path(test_config_path).unlink(missing_ok=True)
        
        print_success("\n✓ All ConfigManager tests passed!")
        return True
        
    except Exception as e:
        print_error(f"ConfigManager test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def test_azure_devops_client():
    """
    Test Task 2: AzureDevOpsClient
    
    Tests:
    - Connection validation
    - Work item retrieval by tags
    - Work item retrieval by IDs
    - Commit fetching for work items
    """
    print_header("Task 2: Testing AzureDevOpsClient")
    
    print_info("This test requires real Azure DevOps credentials and access.")
    print_info("You will be prompted for organization, project, and PAT.")
    
    proceed = get_user_input("\nProceed with Azure DevOps client test? (y/n)", "n")
    if proceed.lower() != 'y':
        print_warning("Skipping Azure DevOps client test")
        return True
    
    try:
        # Get test parameters
        organization = get_user_input("Azure DevOps organization name")
        project = get_user_input("Project name")
        pat = get_user_input("Personal Access Token (PAT)")
        
        if not all([organization, project, pat]):
            print_error("All parameters are required")
            return False
        
        # Initialize client
        async with AzureDevOpsClient(pat) as client:
            print_success("AzureDevOpsClient initialized")
            
            # Test 1: Connection validation
            print_info("\nTest 1: Validate connection")
            result = await client.validate_connection(organization, project)
            
            if result.success:
                print_success(f"Connection validated: {result.message}")
            else:
                print_error(f"Connection failed: {result.message}")
                return False
            
            # Test 2: Work item retrieval by tags
            print_info("\nTest 2: Retrieve work items by tags")
            tags_input = get_user_input("Enter tags to search (comma-separated)", "")
            
            if tags_input:
                tags = [tag.strip() for tag in tags_input.split(",")]
                work_items = await client.get_work_items_by_tags(organization, project, tags)
                
                if work_items:
                    print_success(f"Found {len(work_items)} work item(s) with tags: {', '.join(tags)}")
                    for wi in work_items[:3]:  # Show first 3
                        print(f"  - #{wi.id}: {wi.title} ({wi.state})")
                else:
                    print_warning("No work items found with specified tags")
            else:
                print_warning("Skipping tag search test")
            
            # Test 3: Work item retrieval by IDs
            print_info("\nTest 3: Retrieve work items by IDs")
            ids_input = get_user_input("Enter work item IDs (comma-separated)", "")
            
            if ids_input:
                try:
                    ids = [int(id.strip()) for id in ids_input.split(",")]
                    work_items = await client.get_work_items_by_ids(organization, project, ids)
                    
                    if work_items:
                        print_success(f"Retrieved {len(work_items)} work item(s)")
                        for wi in work_items:
                            print(f"  - #{wi.id}: {wi.title}")
                            print(f"    State: {wi.state}, Type: {wi.work_item_type}")
                            print(f"    Tags: {', '.join(wi.tags) if wi.tags else 'None'}")
                        
                        # Test 4: Commit fetching
                        print_info("\nTest 4: Fetch commits for work items")
                        test_commits = get_user_input("Test commit fetching? (y/n)", "y")
                        
                        if test_commits.lower() == 'y':
                            # Use first work item
                            test_wi = work_items[0]
                            print_info(f"Fetching commits for work item #{test_wi.id}")
                            
                            commits = await client.get_commits_for_work_item(
                                organization, project, test_wi.id
                            )
                            
                            if commits:
                                print_success(f"Found {len(commits)} commit(s)")
                                for commit in commits[:3]:  # Show first 3
                                    print(f"  - {commit.commit_id[:8]}: {commit.message[:50]}")
                                    print(f"    Author: {commit.author}, Date: {commit.commit_date}")
                            else:
                                print_warning(f"No commits found for work item #{test_wi.id}")
                    else:
                        print_warning("No work items found with specified IDs")
                        
                except ValueError:
                    print_error("Invalid work item IDs format")
                    return False
            else:
                print_warning("Skipping ID search test")
        
        print_success("\n✓ Azure DevOps client tests completed!")
        return True
        
    except AzureDevOpsAuthenticationError as e:
        print_error(f"Authentication failed: {str(e)}")
        return False
    except AzureDevOpsConnectionError as e:
        print_error(f"Connection error: {str(e)}")
        return False
    except Exception as e:
        print_error(f"Azure DevOps client test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def test_azure_wiki_client():
    """
    Test Task 2A: AzureWikiClient
    
    Tests:
    - Connection validation
    - Get project wiki
    - Create wiki page
    - Create release documentation
    - Get release list
    - Get release details
    """
    print_header("Task 2A: Testing AzureWikiClient")
    
    print_info("This test requires real Azure DevOps credentials and wiki access.")
    print_warning("This test will create actual wiki pages in your project!")
    
    proceed = get_user_input("\nProceed with Azure Wiki client test? (y/n)", "n")
    if proceed.lower() != 'y':
        print_warning("Skipping Azure Wiki client test")
        return True
    
    try:
        # Get test parameters
        organization = get_user_input("Azure DevOps organization name")
        project = get_user_input("Project name")
        pat = get_user_input("Personal Access Token (PAT)")
        
        if not all([organization, project, pat]):
            print_error("All parameters are required")
            return False
        
        # Initialize client
        async with AzureWikiClient(pat) as client:
            print_success("AzureWikiClient initialized")
            
            # Test 1: Connection validation
            print_info("\nTest 1: Validate connection")
            is_valid = await client.validate_connection(organization, project)
            
            if is_valid:
                print_success("Connection validated")
            else:
                print_error("Connection validation failed")
                return False
            
            # Test 2: Get project wiki
            print_info("\nTest 2: Get project wiki")
            wiki_info = await client.get_project_wiki(organization, project)
            
            print_success(f"Found project wiki: {wiki_info.name} (ID: {wiki_info.id})")
            
            # Test 3: Create test wiki page
            print_info("\nTest 3: Create test wiki page")
            test_page_path = "/Test/AzureIntegrationTest"
            test_content = f"""# Azure Integration Test

This is a test page created by the manual integration test script.

**Created at**: {asyncio.get_event_loop().time()}

You can safely delete this page.
"""
            
            page_result = await client.create_wiki_page(
                organization, project, wiki_info.id, test_page_path, test_content
            )
            
            if page_result.success:
                print_success(f"Created test wiki page: {page_result.url}")
            else:
                print_warning(f"Failed to create test page: {page_result.error_message}")
            
            # Test 4: Create release documentation
            print_info("\nTest 4: Create release documentation")
            create_release_doc = get_user_input("Create test release documentation? (y/n)", "n")
            
            if create_release_doc.lower() == 'y':
                from app.models.azure_devops import WorkItem, Commit
                from datetime import datetime, timezone
                
                # Create mock data
                test_work_items = [
                    WorkItem(
                        id=12345,
                        title="Test PBI 1",
                        state="Closed",
                        work_item_type="Product Backlog Item",
                        tags=["test", "integration"],
                        assigned_to="Test User",
                        created_date=datetime.now(timezone.utc)
                    ),
                    WorkItem(
                        id=12346,
                        title="Test PBI 2",
                        state="Closed",
                        work_item_type="Product Backlog Item",
                        tags=["test"],
                        assigned_to="Test User",
                        created_date=datetime.now(timezone.utc)
                    )
                ]
                
                test_commits = [
                    Commit(
                        commit_id="abc123def456",
                        author="Test Author",
                        author_email="test@example.com",
                        commit_date=datetime.now(timezone.utc),
                        message="Test commit 1",
                        work_item_ids=[12345],
                        changed_files=["file1.py"]
                    ),
                    Commit(
                        commit_id="def456ghi789",
                        author="Test Author",
                        author_email="test@example.com",
                        commit_date=datetime.now(timezone.utc),
                        message="Test commit 2",
                        work_item_ids=[12346],
                        changed_files=["file2.py"]
                    )
                ]
                
                release_name = "Test-Release-1.0"
                branch_name = "release/test-1.0"
                created_by = "Integration Test"
                
                doc_result = await client.create_release_documentation(
                    organization, project, release_name, branch_name,
                    created_by, test_work_items, test_commits
                )
                
                if doc_result.success:
                    print_success("Created release documentation")
                    print_info(f"Main page: {doc_result.main_page_url}")
                    print_info(f"Commits page: {doc_result.commits_page_url}")
                else:
                    print_warning(f"Failed to create release documentation: {doc_result.error_message}")
            
            # Test 5: Get release list
            print_info("\nTest 5: Get release list")
            releases = await client.get_release_list(organization, project, limit=10)
            
            if releases:
                print_success(f"Found {len(releases)} release(s)")
                for release in releases[:5]:  # Show first 5
                    print(f"  - {release['name']}: {release['url']}")
            else:
                print_warning("No releases found in wiki")
            
            # Test 6: Get release details
            if releases:
                print_info("\nTest 6: Get release details")
                test_release = releases[0]
                details = await client.get_release_details(
                    organization, project, test_release['name']
                )
                
                if details:
                    print_success(f"Retrieved details for release: {details['name']}")
                    print_info(f"URL: {details['url']}")
                else:
                    print_warning("Failed to retrieve release details")
        
        print_success("\n✓ Azure Wiki client tests completed!")
        return True
        
    except AzureWikiAuthenticationError as e:
        print_error(f"Authentication failed: {str(e)}")
        return False
    except AzureWikiConnectionError as e:
        print_error(f"Connection error: {str(e)}")
        return False
    except Exception as e:
        print_error(f"Azure Wiki client test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Main test runner"""
    print_header("Azure DevOps Integration Manual Test Suite")
    print_info("This script tests the Azure DevOps integration implemented in Tasks 1, 2, and 2A")
    print_info("Some tests require real Azure DevOps credentials and will create actual resources")
    
    results = {}
    
    # Test 1: ConfigManager
    results['ConfigManager'] = test_config_manager()
    
    # Test 2: AzureDevOpsClient
    results['AzureDevOpsClient'] = await test_azure_devops_client()
    
    # Test 3: AzureWikiClient
    results['AzureWikiClient'] = await test_azure_wiki_client()
    
    # Summary
    print_header("Test Summary")
    
    for component, passed in results.items():
        if passed:
            print_success(f"{component}: PASSED")
        else:
            print_error(f"{component}: FAILED")
    
    all_passed = all(results.values())
    
    if all_passed:
        print_success("\n✓ All tests passed! Azure DevOps integration is working correctly.")
    else:
        print_error("\n✗ Some tests failed. Please review the output above.")
    
    return all_passed


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print_warning("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"\nUnexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
