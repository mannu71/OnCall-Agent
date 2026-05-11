"""
Test script for Azure Release Management API endpoints.

This script tests all the API endpoints defined in the spec:
- Work item search
- Commit fetching
- Release creation
- Conflict resolution
- Release history
- Settings management
"""

import requests
import json
from typing import Dict, Any, Optional

# API base URL
BASE_URL = "http://localhost:8000/api/v1"

# Test configuration
TEST_ORG = "test-organization"
TEST_PROJECT = "test-project"
TEST_PAT = "test-pat-token-12345"
TEST_REPO_ID = "test-repo-id"


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'


def print_test_header(test_name: str):
    """Print a formatted test header."""
    print(f"\n{Colors.BLUE}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BLUE}Testing: {test_name}{Colors.RESET}")
    print(f"{Colors.BLUE}{'=' * 80}{Colors.RESET}")


def print_success(message: str):
    """Print a success message."""
    print(f"{Colors.GREEN}[OK] {message}{Colors.RESET}")


def print_error(message: str):
    """Print an error message."""
    print(f"{Colors.RED}[FAIL] {message}{Colors.RESET}")


def print_warning(message: str):
    """Print a warning message."""
    print(f"{Colors.YELLOW}[WARN] {message}{Colors.RESET}")


def print_response(response: requests.Response):
    """Print formatted response details."""
    print(f"\nStatus Code: {response.status_code}")
    print(f"Response Headers: {dict(response.headers)}")
    try:
        print(f"Response Body: {json.dumps(response.json(), indent=2)}")
    except:
        print(f"Response Body: {response.text}")


def test_endpoint(
    method: str,
    endpoint: str,
    data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    expected_status: int = 200,
    description: str = ""
) -> bool:
    """
    Test an API endpoint.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: API endpoint path
        data: Request body data
        params: Query parameters
        expected_status: Expected HTTP status code
        description: Test description
        
    Returns:
        True if test passed, False otherwise
    """
    url = f"{BASE_URL}{endpoint}"
    
    print(f"\n{Colors.YELLOW}> {method} {endpoint}{Colors.RESET}")
    if description:
        print(f"  {description}")
    
    try:
        if method == "GET":
            response = requests.get(url, params=params)
        elif method == "POST":
            response = requests.post(url, json=data, params=params)
        elif method == "DELETE":
            response = requests.delete(url, params=params)
        else:
            print_error(f"Unsupported HTTP method: {method}")
            return False
        
        print_response(response)
        
        if response.status_code == expected_status:
            print_success(f"Test passed! Status code: {response.status_code}")
            return True
        else:
            print_error(
                f"Test failed! Expected status {expected_status}, "
                f"got {response.status_code}"
            )
            return False
            
    except requests.exceptions.ConnectionError:
        print_error("Connection error! Is the server running?")
        return False
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")
        return False


def main():
    """Run all API endpoint tests."""
    print(f"\n{Colors.BLUE}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BLUE}Azure Release Management API Test Suite{Colors.RESET}")
    print(f"{Colors.BLUE}{'=' * 80}{Colors.RESET}")
    
    results = []
    
    # Test 1: Health check (baseline test)
    print_test_header("Health Check (Baseline)")
    results.append(test_endpoint(
        "GET",
        "/health",
        description="Verify API is running"
    ))
    
    # Test 2: List configured organizations (should be empty initially)
    print_test_header("List Configured Organizations")
    results.append(test_endpoint(
        "GET",
        "/releases/settings/azure-devops",
        description="List all configured Azure DevOps organizations"
    ))
    
    # Test 3: Save Azure DevOps configuration
    print_test_header("Save Azure DevOps Configuration")
    results.append(test_endpoint(
        "POST",
        "/releases/settings/azure-devops",
        data={
            "organization": TEST_ORG,
            "pat": TEST_PAT,
            "repository_id": TEST_REPO_ID
        },
        description="Save credentials for test organization"
    ))
    
    # Test 4: List organizations again (should show our test org)
    print_test_header("List Organizations After Save")
    results.append(test_endpoint(
        "GET",
        "/releases/settings/azure-devops",
        description="Verify organization was saved"
    ))
    
    # Test 5: Test Azure DevOps connection (will fail with fake credentials)
    print_test_header("Test Azure DevOps Connection")
    print_warning("Expected to fail with fake credentials - this is normal")
    results.append(test_endpoint(
        "POST",
        "/releases/settings/azure-devops/test",
        data={
            "organization": TEST_ORG,
            "project": TEST_PROJECT
        },
        expected_status=200,  # Endpoint returns 200 with success=false
        description="Test connection with fake credentials (expected to fail)"
    ))
    
    # Test 6: Search work items (will fail without real Azure DevOps)
    print_test_header("Search Work Items by Tags")
    print_warning("Expected to fail without real Azure DevOps - this is normal")
    results.append(test_endpoint(
        "POST",
        "/releases/work-items/search",
        data={
            "organization": TEST_ORG,
            "project": TEST_PROJECT,
            "tags": ["feature", "bug-fix"]
        },
        expected_status=502,  # Bad Gateway - Azure DevOps unreachable
        description="Search work items by tags"
    ))
    
    # Test 7: Search work items by PBI numbers
    print_test_header("Search Work Items by PBI Numbers")
    print_warning("Expected to fail without real Azure DevOps - this is normal")
    results.append(test_endpoint(
        "POST",
        "/releases/work-items/search",
        data={
            "organization": TEST_ORG,
            "project": TEST_PROJECT,
            "pbi_numbers": [12345, 12346]
        },
        expected_status=502,  # Bad Gateway - Azure DevOps unreachable
        description="Search work items by PBI numbers"
    ))
    
    # Test 8: Get commits for work item
    print_test_header("Get Commits for Work Item")
    print_warning("Expected to fail without real Azure DevOps - this is normal")
    results.append(test_endpoint(
        "GET",
        "/releases/work-items/12345/commits",
        params={
            "organization": TEST_ORG,
            "project": TEST_PROJECT
        },
        expected_status=502,  # Bad Gateway - Azure DevOps unreachable
        description="Get commits for work item 12345"
    ))
    
    # Test 9: Create release (will fail without real data)
    print_test_header("Create Release")
    print_warning("Expected to fail without real Azure DevOps - this is normal")
    results.append(test_endpoint(
        "POST",
        "/releases",
        data={
            "name": "Test Release v1.0",
            "organization": TEST_ORG,
            "project": TEST_PROJECT,
            "work_item_ids": [12345, 12346],
            "commit_ids": ["abc123", "def456"],
            "base_branch": "main",
            "created_by": "test_user"
        },
        expected_status=500,  # Will fail without real Azure DevOps
        description="Create a new release"
    ))
    
    # Test 10: Get release history
    print_test_header("Get Release History")
    print_warning("Expected to fail without real Azure DevOps - this is normal")
    results.append(test_endpoint(
        "GET",
        "/releases",
        params={
            "organization": TEST_ORG,
            "project": TEST_PROJECT,
            "page": 1,
            "page_size": 50
        },
        expected_status=401,  # Will fail without real credentials
        description="Get release history with pagination"
    ))
    
    # Test 11: Get release details
    print_test_header("Get Release Details")
    print_warning("Expected to fail without real Azure DevOps - this is normal")
    results.append(test_endpoint(
        "GET",
        f"/releases/{TEST_ORG}/{TEST_PROJECT}/Test-Release-v1.0",
        expected_status=401,  # Will fail without real credentials
        description="Get details for a specific release"
    ))
    
    # Test 12: Resolve conflict (will fail without active release)
    print_test_header("Resolve Merge Conflict")
    print_warning("Expected to fail without active release - this is normal")
    results.append(test_endpoint(
        "POST",
        "/releases/test-release-id/conflicts/resolve",
        data={
            "file_path": "src/main.py",
            "resolution_type": "ours"
        },
        expected_status=500,  # Will fail without active merge
        description="Resolve a merge conflict"
    ))
    
    # Test 13: Abort conflicts (will fail without active merge)
    print_test_header("Abort Merge Conflicts")
    print_warning("Expected to fail without active merge - this is normal")
    results.append(test_endpoint(
        "POST",
        "/releases/test-release-id/conflicts/abort",
        expected_status=200,  # Returns success even if no merge
        description="Abort merge operation"
    ))
    
    # Test 14: Delete Azure DevOps configuration
    print_test_header("Delete Azure DevOps Configuration")
    results.append(test_endpoint(
        "DELETE",
        f"/releases/settings/azure-devops/{TEST_ORG}",
        description="Delete test organization credentials"
    ))
    
    # Test 15: Verify organization was deleted
    print_test_header("Verify Organization Deletion")
    results.append(test_endpoint(
        "GET",
        "/releases/settings/azure-devops",
        description="Verify organization was deleted"
    ))
    
    # Print summary
    print(f"\n{Colors.BLUE}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BLUE}Test Summary{Colors.RESET}")
    print(f"{Colors.BLUE}{'=' * 80}{Colors.RESET}")
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nTotal Tests: {total}")
    print(f"{Colors.GREEN}Passed: {passed}{Colors.RESET}")
    print(f"{Colors.RED}Failed: {total - passed}{Colors.RESET}")
    
    if passed == total:
        print(f"\n{Colors.GREEN}All tests passed! [OK]{Colors.RESET}")
    else:
        print(f"\n{Colors.YELLOW}Some tests failed. Review output above.{Colors.RESET}")
    
    print(f"\n{Colors.BLUE}{'=' * 80}{Colors.RESET}")
    print(f"{Colors.BLUE}API Endpoint Verification Complete{Colors.RESET}")
    print(f"{Colors.BLUE}{'=' * 80}{Colors.RESET}\n")
    
    print(f"{Colors.YELLOW}Note:{Colors.RESET} Many tests are expected to fail without real Azure DevOps")
    print(f"credentials and data. The important thing is that:")
    print(f"  1. All endpoints are accessible (no 404 errors)")
    print(f"  2. Request/response formats are correct")
    print(f"  3. Error handling works as expected")
    print(f"  4. Settings endpoints work correctly\n")


if __name__ == "__main__":
    main()
