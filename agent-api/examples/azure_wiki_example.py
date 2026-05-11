"""
Azure Wiki Client Usage Example

This example demonstrates how to use the Azure Wiki Client to create
release documentation and retrieve release history.

Requirements:
- Azure DevOps PAT with Wiki read/write permissions
- Existing Azure DevOps project with wiki enabled
"""

import asyncio
from datetime import datetime
from app.services.azure_wiki_client import AzureWikiClient
from app.models.azure_devops import WorkItem, Commit


async def main():
    """Main example function"""
    
    # Configuration
    PAT = "your_pat_token_here"  # Replace with your PAT
    ORGANIZATION = "your-org"     # Replace with your organization
    PROJECT = "your-project"      # Replace with your project
    
    # Initialize client
    async with AzureWikiClient(pat=PAT) as client:
        print("Azure Wiki Client Example")
        print("=" * 50)
        
        # 1. Validate connection
        print("\n1. Validating connection...")
        is_valid = await client.validate_connection(ORGANIZATION, PROJECT)
        print(f"   Connection valid: {is_valid}")
        
        if not is_valid:
            print("   ERROR: Cannot connect to Azure Wiki")
            return
        
        # 2. Get project wiki
        print("\n2. Getting project wiki...")
        try:
            wiki_info = await client.get_project_wiki(ORGANIZATION, PROJECT)
            print(f"   Wiki ID: {wiki_info.id}")
            print(f"   Wiki Name: {wiki_info.name}")
            print(f"   Project ID: {wiki_info.project_id}")
        except Exception as e:
            print(f"   ERROR: {e}")
            return
        
        # 3. Create sample work items
        print("\n3. Creating sample work items...")
        work_items = [
            WorkItem(
                id=12345,
                title="Implement user authentication",
                state="Closed",
                work_item_type="Product Backlog Item",
                tags=["authentication", "security"],
                assigned_to="John Doe",
                created_date=datetime(2024, 1, 15, 10, 30, 0)
            ),
            WorkItem(
                id=12346,
                title="Add password reset feature",
                state="Closed",
                work_item_type="Product Backlog Item",
                tags=["authentication"],
                assigned_to="Jane Smith",
                created_date=datetime(2024, 1, 16, 14, 20, 0)
            )
        ]
        print(f"   Created {len(work_items)} sample work items")
        
        # 4. Create sample commits
        print("\n4. Creating sample commits...")
        commits = [
            Commit(
                commit_id="abc123def456789012345678901234567890abcd",
                author="John Doe",
                author_email="john@example.com",
                commit_date=datetime(2024, 1, 15, 10, 30, 0),
                message="Add login endpoint #12345",
                work_item_ids=[12345],
                changed_files=["src/auth/login.py", "tests/test_login.py"]
            ),
            Commit(
                commit_id="def456ghi789012345678901234567890abcdef",
                author="Jane Smith",
                author_email="jane@example.com",
                commit_date=datetime(2024, 1, 16, 14, 20, 0),
                message="Add password reset #12346",
                work_item_ids=[12346],
                changed_files=["src/auth/reset.py", "tests/test_reset.py"]
            )
        ]
        print(f"   Created {len(commits)} sample commits")
        
        # 5. Create release documentation
        print("\n5. Creating release documentation...")
        result = await client.create_release_documentation(
            organization=ORGANIZATION,
            project=PROJECT,
            release_name="Example-Release-1.0",
            branch_name="release/example-1.0",
            created_by="Example User",
            work_items=work_items,
            commits=commits
        )
        
        if result.success:
            print("   ✓ Release documentation created successfully!")
            print(f"   Main page URL: {result.main_page_url}")
            print(f"   Commits page URL: {result.commits_page_url}")
        else:
            print(f"   ✗ Failed to create documentation: {result.error_message}")
            return
        
        # 6. Get release list
        print("\n6. Getting release list...")
        releases = await client.get_release_list(
            organization=ORGANIZATION,
            project=PROJECT,
            limit=10,
            offset=0
        )
        print(f"   Found {len(releases)} releases:")
        for release in releases[:5]:  # Show first 5
            print(f"   - {release['name']}")
        
        # 7. Get release details
        print("\n7. Getting release details...")
        details = await client.get_release_details(
            organization=ORGANIZATION,
            project=PROJECT,
            release_name="Example-Release-1.0"
        )
        
        if details:
            print(f"   Release: {details['name']}")
            print(f"   Path: {details['path']}")
            print(f"   URL: {details['url']}")
            print(f"   Content preview: {details['content'][:100]}...")
        else:
            print("   Release not found")
        
        # 8. Create a simple wiki page
        print("\n8. Creating a simple wiki page...")
        page_result = await client.create_wiki_page(
            organization=ORGANIZATION,
            project=PROJECT,
            wiki_id=wiki_info.id,
            path="/Examples/Test-Page",
            content="# Test Page\n\nThis is a test page created by the example script."
        )
        
        if page_result.success:
            print("   ✓ Page created successfully!")
            print(f"   Page URL: {page_result.url}")
        else:
            print(f"   ✗ Failed to create page: {page_result.error_message}")
        
        print("\n" + "=" * 50)
        print("Example completed successfully!")


if __name__ == "__main__":
    # Run the example
    asyncio.run(main())
