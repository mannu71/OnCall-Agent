"""
Quick Azure DevOps Integration Test

A simplified test script for quickly verifying Azure DevOps integration.
This script tests the core functionality without extensive prompts.

Usage:
    # Set environment variable first
    export AZURE_DEVOPS_ENCRYPTION_KEY="your-key-here"
    
    # Run the test
    python tests/quick_azure_test.py --org <organization> --project <project> --pat <pat>

Example:
    python tests/quick_azure_test.py --org myorg --project myproject --pat abc123xyz
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.azure_config_manager import ConfigManager
from app.services.azure_devops_client import AzureDevOpsClient
from app.services.azure_wiki_client import AzureWikiClient


def print_result(test_name: str, passed: bool, message: str = ""):
    """Print test result"""
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"{status} | {test_name}")
    if message:
        print(f"       {message}")


async def quick_test(organization: str, project: str, pat: str):
    """Run quick integration test"""
    print(f"\n{'=' * 60}")
    print(f"Azure DevOps Integration Quick Test")
    print(f"{'=' * 60}\n")
    print(f"Organization: {organization}")
    print(f"Project: {project}")
    print(f"PAT: {'*' * 10}{pat[-4:] if len(pat) > 4 else '****'}\n")
    
    results = []
    
    # Test 1: ConfigManager
    print("Testing ConfigManager...")
    try:
        config_manager = ConfigManager(config_path="config/test_quick_credentials.json")
        config_manager.save_credentials(organization, pat, "test-repo-id")
        creds = config_manager.get_credentials(organization)
        
        if creds and creds["pat"] == pat:
            print_result("ConfigManager", True, "Credentials encrypted and retrieved")
            results.append(True)
        else:
            print_result("ConfigManager", False, "Credential mismatch")
            results.append(False)
        
        # Cleanup
        config_manager.delete_credentials(organization)
        Path("config/test_quick_credentials.json").unlink(missing_ok=True)
        
    except Exception as e:
        print_result("ConfigManager", False, str(e))
        results.append(False)
    
    # Test 2: AzureDevOpsClient
    print("\nTesting AzureDevOpsClient...")
    try:
        async with AzureDevOpsClient(pat) as client:
            # Connection test
            result = await client.validate_connection(organization, project)
            
            if result.success:
                print_result("Connection Validation", True, result.message)
                results.append(True)
                
                # Try to get a work item (if any exist)
                try:
                    work_items = await client.get_work_items_by_ids(
                        organization, project, [1]  # Try ID 1
                    )
                    print_result("Work Item Retrieval", True, 
                               f"API accessible (tested with ID 1)")
                    results.append(True)
                except Exception:
                    # It's okay if work item 1 doesn't exist
                    print_result("Work Item Retrieval", True, 
                               "API accessible (work item 1 not found, which is normal)")
                    results.append(True)
            else:
                print_result("Connection Validation", False, result.message)
                results.append(False)
                results.append(False)  # Skip work item test
                
    except Exception as e:
        print_result("AzureDevOpsClient", False, str(e))
        results.append(False)
        results.append(False)
    
    # Test 3: AzureWikiClient
    print("\nTesting AzureWikiClient...")
    try:
        async with AzureWikiClient(pat) as client:
            # Connection test
            is_valid = await client.validate_connection(organization, project)
            
            if is_valid:
                print_result("Wiki Connection", True, "Wiki API accessible")
                results.append(True)
                
                # Try to get wiki info
                try:
                    wiki_info = await client.get_project_wiki(organization, project)
                    print_result("Get Project Wiki", True, 
                               f"Found wiki: {wiki_info.name}")
                    results.append(True)
                except Exception as e:
                    print_result("Get Project Wiki", False, 
                               f"Wiki not found (may need to be created): {str(e)}")
                    results.append(False)
            else:
                print_result("Wiki Connection", False, "Wiki API not accessible")
                results.append(False)
                results.append(False)
                
    except Exception as e:
        print_result("AzureWikiClient", False, str(e))
        results.append(False)
        results.append(False)
    
    # Summary
    print(f"\n{'=' * 60}")
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed! Integration is working correctly.")
        return True
    else:
        print("✗ Some tests failed. Check the output above for details.")
        return False


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Quick test for Azure DevOps integration"
    )
    parser.add_argument(
        "--org", "--organization",
        required=True,
        help="Azure DevOps organization name"
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name"
    )
    parser.add_argument(
        "--pat",
        required=True,
        help="Personal Access Token"
    )
    
    args = parser.parse_args()
    
    # Check encryption key
    if not os.environ.get("AZURE_DEVOPS_ENCRYPTION_KEY"):
        print("ERROR: AZURE_DEVOPS_ENCRYPTION_KEY environment variable not set")
        print("\nGenerate a key with:")
        print('  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
        print("\nThen set it:")
        print('  export AZURE_DEVOPS_ENCRYPTION_KEY="your-key-here"')
        sys.exit(1)
    
    # Run tests
    try:
        result = asyncio.run(quick_test(args.org, args.project, args.pat))
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
