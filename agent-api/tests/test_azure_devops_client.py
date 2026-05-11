"""
Unit tests for Azure DevOps Client Service

Tests cover:
- Authentication and connection validation
- Work item retrieval by tags and IDs
- Commit fetching for work items
- Error handling and retry logic
- Response parsing

Requirements: 10.1, 10.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 5.1, 5.2, 5.5, 2.1.4
"""

import base64
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.services.azure_devops_client import (
    AzureDevOpsClient,
    AzureDevOpsConnectionError,
    AzureDevOpsAuthenticationError,
    AzureDevOpsNotFoundError,
    AzureDevOpsRateLimitError,
)
from app.models.azure_devops import WorkItem, Commit, ConnectionResult


# Test Fixtures

@pytest.fixture
def mock_pat():
    """Mock Personal Access Token"""
    return "test_pat_token_12345"


@pytest.fixture
def azure_client(mock_pat):
    """Create Azure DevOps client with mock PAT"""
    return AzureDevOpsClient(pat=mock_pat, timeout=10.0)


@pytest.fixture
def mock_work_item_response():
    """Mock work item API response"""
    return {
        "value": [
            {
                "id": 12345,
                "fields": {
                    "System.Title": "Implement user authentication",
                    "System.State": "Closed",
                    "System.WorkItemType": "Product Backlog Item",
                    "System.Tags": "authentication; security",
                    "System.AssignedTo": {"displayName": "John Doe"},
                    "System.CreatedDate": "2024-01-15T10:30:00Z"
                }
            },
            {
                "id": 12346,
                "fields": {
                    "System.Title": "Add password reset feature",
                    "System.State": "Active",
                    "System.WorkItemType": "Product Backlog Item",
                    "System.Tags": "authentication",
                    "System.AssignedTo": "Jane Smith",
                    "System.CreatedDate": "2024-01-16T14:20:00Z"
                }
            }
        ]
    }


@pytest.fixture
def mock_wiql_response():
    """Mock WIQL query response"""
    return {
        "workItems": [
            {"id": 12345},
            {"id": 12346}
        ]
    }


@pytest.fixture
def mock_commit_response():
    """Mock commit API response"""
    return {
        "commitId": "abc123def456",
        "comment": "Add login endpoint #12345",
        "author": {
            "name": "John Doe",
            "email": "john@example.com",
            "date": "2024-01-15T10:30:00Z"
        },
        "changes": [
            {"item": {"path": "/src/auth/login.py"}},
            {"item": {"path": "/src/auth/utils.py"}}
        ]
    }


@pytest.fixture
def mock_work_item_with_commits():
    """Mock work item with commit relations"""
    return {
        "id": 12345,
        "fields": {
            "System.Title": "Test work item",
            "System.State": "Closed",
            "System.WorkItemType": "Product Backlog Item",
            "System.Tags": "",
            "System.CreatedDate": "2024-01-15T10:30:00Z"
        },
        "relations": [
            {
                "rel": "ArtifactLink",
                "url": "vstfs:///Git/Commit/TestProject%2Frepo-id%2Fabc123def456"
            }
        ]
    }


# Test Authentication and Initialization

def test_client_initialization(mock_pat):
    """Test client initializes with correct configuration"""
    client = AzureDevOpsClient(pat=mock_pat, timeout=30.0)
    
    assert client.pat == mock_pat
    assert client.timeout == 30.0
    assert client.MAX_RETRIES == 3
    assert client.API_VERSION == "7.1"


def test_pat_encoding(azure_client, mock_pat):
    """Test PAT is correctly encoded for Basic auth"""
    encoded = azure_client._encode_pat(mock_pat)
    
    # Decode and verify
    decoded = base64.b64decode(encoded).decode()
    assert decoded == f":{mock_pat}"


def test_client_headers(azure_client, mock_pat):
    """Test HTTP client has correct headers"""
    headers = azure_client.client.headers
    
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"


# Test Connection Validation

@pytest.mark.asyncio
async def test_validate_connection_success(azure_client):
    """Test successful connection validation"""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"name": "TestProject"}
    
    with patch.object(azure_client, '_make_request', return_value=mock_response):
        result = await azure_client.validate_connection("test-org", "test-project")
    
    assert result.success is True
    assert result.organization == "test-org"
    assert result.project == "test-project"
    assert "Successfully connected" in result.message


@pytest.mark.asyncio
async def test_validate_connection_auth_failure(azure_client):
    """Test connection validation with authentication error"""
    with patch.object(
        azure_client, 
        '_make_request', 
        side_effect=AzureDevOpsAuthenticationError("Invalid PAT")
    ):
        result = await azure_client.validate_connection("test-org", "test-project")
    
    assert result.success is False
    assert "Authentication failed" in result.message


@pytest.mark.asyncio
async def test_validate_connection_network_error(azure_client):
    """Test connection validation with network error"""
    with patch.object(
        azure_client, 
        '_make_request', 
        side_effect=AzureDevOpsConnectionError("Network error")
    ):
        result = await azure_client.validate_connection("test-org", "test-project")
    
    assert result.success is False
    assert "Connection error" in result.message


# Test Work Item Retrieval by Tags

@pytest.mark.asyncio
async def test_get_work_items_by_tags_success(azure_client, mock_wiql_response, mock_work_item_response):
    """Test successful work item retrieval by tags"""
    # Mock WIQL query
    wiql_mock = MagicMock()
    wiql_mock.json.return_value = mock_wiql_response
    
    # Mock work item details
    wi_mock = MagicMock()
    wi_mock.json.return_value = mock_work_item_response
    
    with patch.object(azure_client, '_make_request', side_effect=[wiql_mock, wi_mock]):
        work_items = await azure_client.get_work_items_by_tags(
            "test-org", 
            "test-project", 
            ["authentication", "security"]
        )
    
    assert len(work_items) == 2
    assert work_items[0].id == 12345
    assert work_items[0].title == "Implement user authentication"
    assert work_items[0].state == "Closed"
    assert "authentication" in work_items[0].tags
    assert "security" in work_items[0].tags


@pytest.mark.asyncio
async def test_get_work_items_by_tags_empty_tags(azure_client):
    """Test work item retrieval with empty tags list"""
    work_items = await azure_client.get_work_items_by_tags(
        "test-org", 
        "test-project", 
        []
    )
    
    assert work_items == []


@pytest.mark.asyncio
async def test_get_work_items_by_tags_no_results(azure_client):
    """Test work item retrieval when no items match tags"""
    wiql_mock = MagicMock()
    wiql_mock.json.return_value = {"workItems": []}
    
    with patch.object(azure_client, '_make_request', return_value=wiql_mock):
        work_items = await azure_client.get_work_items_by_tags(
            "test-org", 
            "test-project", 
            ["nonexistent-tag"]
        )
    
    assert work_items == []


# Test Work Item Retrieval by IDs

@pytest.mark.asyncio
async def test_get_work_items_by_ids_success(azure_client, mock_work_item_response):
    """Test successful work item retrieval by IDs"""
    mock_response = MagicMock()
    mock_response.json.return_value = mock_work_item_response
    
    with patch.object(azure_client, '_make_request', return_value=mock_response):
        work_items = await azure_client.get_work_items_by_ids(
            "test-org", 
            "test-project", 
            [12345, 12346]
        )
    
    assert len(work_items) == 2
    assert work_items[0].id == 12345
    assert work_items[1].id == 12346


@pytest.mark.asyncio
async def test_get_work_items_by_ids_empty_list(azure_client):
    """Test work item retrieval with empty IDs list"""
    work_items = await azure_client.get_work_items_by_ids(
        "test-org", 
        "test-project", 
        []
    )
    
    assert work_items == []


@pytest.mark.asyncio
async def test_get_work_items_by_ids_batch_processing(azure_client):
    """Test work item retrieval handles batching for large ID lists"""
    # Create 250 IDs to test batching (batch size is 200)
    ids = list(range(1, 251))
    
    mock_response_1 = MagicMock()
    mock_response_1.json.return_value = {
        "value": [
            {
                "id": i,
                "fields": {
                    "System.Title": f"Work Item {i}",
                    "System.State": "Closed",
                    "System.WorkItemType": "PBI",
                    "System.Tags": "",
                    "System.CreatedDate": "2024-01-15T10:30:00Z"
                }
            }
            for i in range(1, 201)
        ]
    }
    
    mock_response_2 = MagicMock()
    mock_response_2.json.return_value = {
        "value": [
            {
                "id": i,
                "fields": {
                    "System.Title": f"Work Item {i}",
                    "System.State": "Closed",
                    "System.WorkItemType": "PBI",
                    "System.Tags": "",
                    "System.CreatedDate": "2024-01-15T10:30:00Z"
                }
            }
            for i in range(201, 251)
        ]
    }
    
    with patch.object(azure_client, '_make_request', side_effect=[mock_response_1, mock_response_2]):
        work_items = await azure_client.get_work_items_by_ids(
            "test-org", 
            "test-project", 
            ids
        )
    
    assert len(work_items) == 250


# Test Work Item Parsing

def test_parse_work_item_with_dict_assigned_to(azure_client):
    """Test parsing work item with assigned_to as dict"""
    data = {
        "id": 12345,
        "fields": {
            "System.Title": "Test Item",
            "System.State": "Active",
            "System.WorkItemType": "PBI",
            "System.Tags": "tag1; tag2",
            "System.AssignedTo": {"displayName": "John Doe"},
            "System.CreatedDate": "2024-01-15T10:30:00Z"
        }
    }
    
    work_item = azure_client._parse_work_item(data)
    
    assert work_item.id == 12345
    assert work_item.assigned_to == "John Doe"
    assert work_item.tags == ["tag1", "tag2"]


def test_parse_work_item_with_string_assigned_to(azure_client):
    """Test parsing work item with assigned_to as string"""
    data = {
        "id": 12345,
        "fields": {
            "System.Title": "Test Item",
            "System.State": "Active",
            "System.WorkItemType": "PBI",
            "System.Tags": "",
            "System.AssignedTo": "Jane Smith",
            "System.CreatedDate": "2024-01-15T10:30:00Z"
        }
    }
    
    work_item = azure_client._parse_work_item(data)
    
    assert work_item.assigned_to == "Jane Smith"


def test_parse_work_item_no_tags(azure_client):
    """Test parsing work item with no tags"""
    data = {
        "id": 12345,
        "fields": {
            "System.Title": "Test Item",
            "System.State": "Active",
            "System.WorkItemType": "PBI",
            "System.Tags": "",
            "System.CreatedDate": "2024-01-15T10:30:00Z"
        }
    }
    
    work_item = azure_client._parse_work_item(data)
    
    assert work_item.tags == []


# Test Commit Fetching

@pytest.mark.asyncio
async def test_get_commits_for_work_item_success(azure_client, mock_work_item_with_commits, mock_commit_response):
    """Test successful commit fetching for work item"""
    wi_mock = MagicMock()
    wi_mock.json.return_value = mock_work_item_with_commits
    
    commit_mock = MagicMock()
    commit_mock.json.return_value = mock_commit_response
    
    with patch.object(azure_client, '_make_request', side_effect=[wi_mock, commit_mock]):
        commits = await azure_client.get_commits_for_work_item(
            "test-org",
            "test-project",
            12345
        )
    
    assert len(commits) == 1
    assert commits[0].commit_id == "abc123def456"
    assert commits[0].author == "John Doe"
    assert commits[0].author_email == "john@example.com"
    assert 12345 in commits[0].work_item_ids
    assert len(commits[0].changed_files) == 2


@pytest.mark.asyncio
async def test_get_commits_for_work_item_no_commits(azure_client):
    """Test commit fetching when work item has no commits"""
    wi_mock = MagicMock()
    wi_mock.json.return_value = {
        "id": 12345,
        "fields": {
            "System.Title": "Test",
            "System.State": "Closed",
            "System.WorkItemType": "PBI",
            "System.Tags": "",
            "System.CreatedDate": "2024-01-15T10:30:00Z"
        },
        "relations": []
    }
    
    with patch.object(azure_client, '_make_request', return_value=wi_mock):
        commits = await azure_client.get_commits_for_work_item(
            "test-org",
            "test-project",
            12345
        )
    
    assert commits == []


@pytest.mark.asyncio
async def test_get_commits_for_work_item_malformed_url(azure_client):
    """Test commit fetching handles malformed commit URLs"""
    wi_mock = MagicMock()
    wi_mock.json.return_value = {
        "id": 12345,
        "fields": {
            "System.Title": "Test",
            "System.State": "Closed",
            "System.WorkItemType": "PBI",
            "System.Tags": "",
            "System.CreatedDate": "2024-01-15T10:30:00Z"
        },
        "relations": [
            {
                "rel": "ArtifactLink",
                "url": "vstfs:///Git/Commit/malformed"
            }
        ]
    }
    
    with patch.object(azure_client, '_make_request', return_value=wi_mock):
        commits = await azure_client.get_commits_for_work_item(
            "test-org",
            "test-project",
            12345
        )
    
    # Should handle malformed URL gracefully
    assert commits == []


# Test Commit Parsing

def test_parse_commit(azure_client, mock_commit_response):
    """Test parsing commit from API response"""
    commit = azure_client._parse_commit(mock_commit_response, 12345)
    
    assert commit.commit_id == "abc123def456"
    assert commit.author == "John Doe"
    assert commit.author_email == "john@example.com"
    assert commit.message == "Add login endpoint #12345"
    assert 12345 in commit.work_item_ids
    assert "/src/auth/login.py" in commit.changed_files
    assert "/src/auth/utils.py" in commit.changed_files


# Test Error Handling and Retry Logic

@pytest.mark.asyncio
async def test_make_request_authentication_error(azure_client):
    """Test request handling for 401 authentication error"""
    mock_response = MagicMock()
    mock_response.status_code = 401
    
    with patch.object(azure_client.client, 'request', return_value=mock_response):
        with pytest.raises(AzureDevOpsAuthenticationError) as exc_info:
            await azure_client._make_request("GET", "https://test.com")
        
        assert "Authentication failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_make_request_not_found_error(azure_client):
    """Test request handling for 404 not found error"""
    mock_response = MagicMock()
    mock_response.status_code = 404
    
    with patch.object(azure_client.client, 'request', return_value=mock_response):
        with pytest.raises(AzureDevOpsNotFoundError) as exc_info:
            await azure_client._make_request("GET", "https://test.com")
        
        assert "Resource not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_make_request_rate_limit_retry(azure_client):
    """Test request retries on rate limit (429) error"""
    # First two attempts return 429, third succeeds
    mock_response_429 = MagicMock()
    mock_response_429.status_code = 429
    
    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    
    with patch.object(
        azure_client.client, 
        'request', 
        side_effect=[mock_response_429, mock_response_429, mock_response_200]
    ):
        with patch('asyncio.sleep'):  # Mock sleep to speed up test
            response = await azure_client._make_request("GET", "https://test.com")
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_make_request_rate_limit_exhausted(azure_client):
    """Test request fails after max retries on rate limit"""
    mock_response = MagicMock()
    mock_response.status_code = 429
    
    with patch.object(azure_client.client, 'request', return_value=mock_response):
        with patch('asyncio.sleep'):  # Mock sleep to speed up test
            with pytest.raises(AzureDevOpsRateLimitError) as exc_info:
                await azure_client._make_request("GET", "https://test.com")
        
        assert "rate limit exceeded" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_make_request_server_error_retry(azure_client):
    """Test request retries on server error (500)"""
    # First two attempts return 500, third succeeds
    mock_response_500 = MagicMock()
    mock_response_500.status_code = 500
    
    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    
    with patch.object(
        azure_client.client, 
        'request', 
        side_effect=[mock_response_500, mock_response_500, mock_response_200]
    ):
        with patch('asyncio.sleep'):  # Mock sleep to speed up test
            response = await azure_client._make_request("GET", "https://test.com")
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_make_request_timeout_retry(azure_client):
    """Test request retries on timeout"""
    # First two attempts timeout, third succeeds
    mock_response = MagicMock()
    mock_response.status_code = 200
    
    with patch.object(
        azure_client.client, 
        'request', 
        side_effect=[httpx.TimeoutException("Timeout"), httpx.TimeoutException("Timeout"), mock_response]
    ):
        with patch('asyncio.sleep'):  # Mock sleep to speed up test
            response = await azure_client._make_request("GET", "https://test.com")
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_make_request_network_error_retry(azure_client):
    """Test request retries on network error"""
    # First two attempts fail with network error, third succeeds
    mock_response = MagicMock()
    mock_response.status_code = 200
    
    with patch.object(
        azure_client.client, 
        'request', 
        side_effect=[httpx.NetworkError("Network error"), httpx.NetworkError("Network error"), mock_response]
    ):
        with patch('asyncio.sleep'):  # Mock sleep to speed up test
            response = await azure_client._make_request("GET", "https://test.com")
    
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_make_request_timeout_exhausted(azure_client):
    """Test request fails after max retries on timeout"""
    with patch.object(
        azure_client.client, 
        'request', 
        side_effect=httpx.TimeoutException("Timeout")
    ):
        with patch('asyncio.sleep'):  # Mock sleep to speed up test
            with pytest.raises(AzureDevOpsConnectionError) as exc_info:
                await azure_client._make_request("GET", "https://test.com")
        
        assert "timeout" in str(exc_info.value).lower()


# Test Async Context Manager

@pytest.mark.asyncio
async def test_async_context_manager(mock_pat):
    """Test client works as async context manager"""
    async with AzureDevOpsClient(pat=mock_pat) as client:
        assert client.pat == mock_pat
        assert client.client is not None
    
    # Client should be closed after context exit
    # Note: We can't easily test if client is closed without accessing internals


# Test Edge Cases

@pytest.mark.asyncio
async def test_get_work_items_by_tags_with_special_characters(azure_client):
    """Test work item retrieval with tags containing special characters"""
    wiql_mock = MagicMock()
    wiql_mock.json.return_value = {"workItems": []}
    
    with patch.object(azure_client, '_make_request', return_value=wiql_mock):
        # Should not raise exception
        work_items = await azure_client.get_work_items_by_tags(
            "test-org",
            "test-project",
            ["tag-with-dash", "tag_with_underscore", "tag.with.dot"]
        )
    
    assert work_items == []


def test_parse_work_item_missing_optional_fields(azure_client):
    """Test parsing work item with missing optional fields"""
    data = {
        "id": 12345,
        "fields": {
            "System.Title": "Test Item",
            "System.State": "Active",
            "System.WorkItemType": "PBI",
            "System.CreatedDate": "2024-01-15T10:30:00Z"
            # Missing: Tags, AssignedTo
        }
    }
    
    work_item = azure_client._parse_work_item(data)
    
    assert work_item.id == 12345
    assert work_item.tags == []
    assert work_item.assigned_to is None


@pytest.mark.asyncio
async def test_get_commit_details_not_found(azure_client):
    """Test commit details retrieval when commit not found"""
    with patch.object(
        azure_client, 
        '_make_request', 
        side_effect=AzureDevOpsNotFoundError("Commit not found")
    ):
        commit = await azure_client._get_commit_details(
            "test-org",
            "test-project",
            "repo-id",
            "nonexistent-commit",
            12345
        )
    
    assert commit is None


@pytest.mark.asyncio
async def test_get_commit_details_generic_error(azure_client):
    """Test commit details retrieval handles generic errors gracefully"""
    with patch.object(
        azure_client, 
        '_make_request', 
        side_effect=Exception("Unexpected error")
    ):
        commit = await azure_client._get_commit_details(
            "test-org",
            "test-project",
            "repo-id",
            "commit-id",
            12345
        )
    
    # Should return None instead of raising exception
    assert commit is None
