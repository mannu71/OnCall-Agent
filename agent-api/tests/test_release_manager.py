"""
Tests for Release Manager Service

Tests the orchestration of release creation workflow including:
- Work item validation
- Release creation
- Wiki documentation creation
- Release history retrieval

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 7.3, 7.4, 7.5, 7.6, 7.7, 8.2, 
              11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 13.1, 13.2, 13.3, 13.4, 
              13.5, 13.6, 13.7, 13.8, 13.9, 2.1.4, 2.1.5
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from datetime import datetime, timezone

from app.services.release_manager import (
    ReleaseManager,
    ReleaseManagerError,
    ReleaseValidationError,
    ReleaseCreationError,
    ReleaseCredentialsError
)
from app.models.azure_devops import (
    WorkItem,
    Commit,
    ValidationResult,
    ValidationWarning,
    ReleaseResult,
    WikiDocumentationResult
)
from app.services.azure_devops_client import (
    AzureDevOpsAuthenticationError,
    AzureDevOpsConnectionError
)
from app.services.git_operations_service import (
    GitBranchError,
    GitOperationsError
)


# Fixtures

@pytest.fixture
def mock_config_manager():
    """Mock ConfigManager"""
    manager = Mock()
    manager.get_credentials.return_value = {
        "pat": "test-pat-token",
        "repository_id": "test-repo-id"
    }
    return manager


@pytest.fixture
def mock_git_service():
    """Mock GitOperationsService"""
    service = Mock()
    service.create_branch.return_value = {
        "success": True,
        "branch_name": "release/test-release"
    }
    service.apply_commits.return_value = {
        "success": True,
        "applied_commits": ["commit1", "commit2"],
        "conflicts": None
    }
    return service


@pytest.fixture
def mock_ado_client():
    """Mock AzureDevOpsClient"""
    client = AsyncMock()
    client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        ),
        WorkItem(
            id=12346,
            title="Test PBI 2",
            state="Active",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    client.get_commits_for_work_item.return_value = [
        Commit(
            commit_id="commit1",
            author="Test Author",
            author_email="author@example.com",
            commit_date=datetime.now(timezone.utc),
            message="Test commit 1",
            work_item_ids=[12345],
            changed_files=["file1.py"]
        )
    ]
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_wiki_client():
    """Mock AzureWikiClient"""
    client = AsyncMock()
    client.create_release_documentation.return_value = WikiDocumentationResult(
        success=True,
        main_page_url="https://wiki.example.com/release",
        commits_page_url="https://wiki.example.com/release/commits",
        error_message=None
    )
    client.get_release_list.return_value = [
        {
            "name": "test-release",
            "branch_name": "release/test-release",
            "created_date": "2024-01-15T10:30:00Z",
            "organization": "test-org",
            "project": "test-project"
        }
    ]
    client.get_release_details.return_value = {
        "name": "test-release",
        "branch_name": "release/test-release",
        "created_date": "2024-01-15T10:30:00Z",
        "created_by": "test-user",
        "work_items": [12345, 12346],
        "commits": ["commit1", "commit2"]
    }
    client.close = AsyncMock()
    return client


@pytest.fixture
def release_manager(mock_config_manager, mock_git_service):
    """Create ReleaseManager instance with mocked dependencies"""
    return ReleaseManager(
        config_manager=mock_config_manager,
        git_service=mock_git_service,
        default_user="test-user"
    )


# Tests for validate_work_items (Task 6.2)

@pytest.mark.asyncio
async def test_validate_work_items_all_closed(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test work item validation when all items are closed.
    
    Requirements: 4.1, 4.2, 2.1.4
    """
    # Setup: All work items closed
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        ),
        WorkItem(
            id=12346,
            title="Test PBI 2",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.validate_work_items(
            organization="test-org",
            project="test-project",
            work_item_ids=[12345, 12346]
        )
    
    # Verify
    assert result.valid is True
    assert len(result.warnings) == 0
    mock_ado_client.get_work_items_by_ids.assert_called_once_with(
        "test-org", "test-project", [12345, 12346]
    )


@pytest.mark.asyncio
async def test_validate_work_items_with_non_closed(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test work item validation with non-closed items.
    
    Requirements: 4.1, 4.2, 4.3, 4.4, 2.1.4
    """
    # Setup: Mix of closed and non-closed
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        ),
        WorkItem(
            id=12346,
            title="Test PBI 2",
            state="Active",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        ),
        WorkItem(
            id=12347,
            title="Test PBI 3",
            state="Resolved",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.validate_work_items(
            organization="test-org",
            project="test-project",
            work_item_ids=[12345, 12346, 12347]
        )
    
    # Verify
    assert result.valid is False
    assert len(result.warnings) == 2
    
    # Check warning details
    warning_ids = [w.work_item_id for w in result.warnings]
    assert 12346 in warning_ids
    assert 12347 in warning_ids
    
    # Check warning messages
    for warning in result.warnings:
        assert warning.current_state in ["Active", "Resolved"]
        assert "not 'Closed'" in warning.message


@pytest.mark.asyncio
async def test_validate_work_items_no_credentials(release_manager):
    """
    Test validation fails when credentials not found.
    
    Requirements: 10.5, 10.6
    """
    # Setup: No credentials
    release_manager.config_manager.get_credentials.return_value = None
    
    # Execute and verify
    with pytest.raises(ReleaseCredentialsError) as exc_info:
        await release_manager.validate_work_items(
            organization="unknown-org",
            project="test-project",
            work_item_ids=[12345]
        )
    
    assert "No credentials found" in str(exc_info.value)


# Tests for create_release (Task 6.3)

@pytest.mark.asyncio
async def test_create_release_success(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test successful release creation.
    
    Requirements: 7.3, 7.4, 7.5, 7.6, 7.7, 2.1.4, 2.1.5
    """
    # Setup: All work items closed
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.create_release(
            name="test-release",
            organization="test-org",
            project="test-project",
            work_item_ids=[12345],
            commit_ids=["commit1", "commit2"],
            base_branch="main",
            created_by="test-user"
        )
    
    # Verify
    assert result.success is True
    assert result.release_id is not None
    assert result.branch_name == "release/test-release"
    assert result.conflicts is None
    assert result.error_message is None
    assert result.wiki_urls is not None
    assert "main_page" in result.wiki_urls
    assert "commits_page" in result.wiki_urls
    
    # Verify Git operations called
    release_manager.git_service.create_branch.assert_called_once_with(
        branch_name="release/test-release",
        base_branch="main"
    )
    release_manager.git_service.apply_commits.assert_called_once_with(
        branch_name="release/test-release",
        commit_ids=["commit1", "commit2"]
    )


@pytest.mark.asyncio
async def test_create_release_with_conflicts(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test release creation with merge conflicts.
    
    Requirements: 8.2, 7.4
    """
    # Setup: Conflicts during commit application
    release_manager.git_service.apply_commits.return_value = {
        "success": False,
        "applied_commits": ["commit1"],
        "conflicts": [
            {
                "file_path": "test.py",
                "conflict_type": "content",
                "our_commit": "commit1",
                "their_commit": "commit2"
            }
        ],
        "failed_commit": "commit2",
        "error_message": "Merge conflict detected"
    }
    
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.create_release(
            name="test-release",
            organization="test-org",
            project="test-project",
            work_item_ids=[12345],
            commit_ids=["commit1", "commit2"],
            base_branch="main"
        )
    
    # Verify
    assert result.success is False
    assert result.conflicts is not None
    assert len(result.conflicts) == 1
    assert result.conflicts[0]["file_path"] == "test.py"
    assert result.branch_name == "release/test-release"


@pytest.mark.asyncio
async def test_create_release_branch_creation_fails(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test release creation when branch creation fails.
    
    Requirements: 7.3, 7.7
    """
    # Setup: Branch creation fails
    release_manager.git_service.create_branch.return_value = {
        "success": False,
        "error_message": "Branch already exists"
    }
    
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.create_release(
            name="test-release",
            organization="test-org",
            project="test-project",
            work_item_ids=[12345],
            commit_ids=["commit1"],
            base_branch="main"
        )
    
    # Verify
    assert result.success is False
    assert "Branch already exists" in result.error_message
    assert result.branch_name is None


@pytest.mark.asyncio
async def test_create_release_authentication_error(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test release creation with authentication error.
    
    Requirements: 10.7
    """
    # Setup: Authentication error
    mock_ado_client.get_work_items_by_ids.side_effect = AzureDevOpsAuthenticationError(
        "Invalid PAT"
    )
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.create_release(
            name="test-release",
            organization="test-org",
            project="test-project",
            work_item_ids=[12345],
            commit_ids=["commit1"],
            base_branch="main"
        )
    
    # Verify
    assert result.success is False
    assert "Authentication error" in result.error_message


# Tests for create_wiki_documentation (Task 6.4)

@pytest.mark.asyncio
async def test_create_wiki_documentation_success(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test successful wiki documentation creation.
    
    Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9
    """
    # Setup
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test", "feature"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    mock_ado_client.get_commits_for_work_item.return_value = [
        Commit(
            commit_id="commit1",
            author="Test Author",
            author_email="author@example.com",
            commit_date=datetime.now(timezone.utc),
            message="Test commit 1",
            work_item_ids=[12345],
            changed_files=["file1.py"]
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.create_wiki_documentation(
            organization="test-org",
            project="test-project",
            release_name="test-release",
            branch_name="release/test-release",
            created_by="test-user",
            work_item_ids=[12345],
            commit_ids=["commit1"]
        )
    
    # Verify
    assert result.success is True
    assert result.main_page_url is not None
    assert result.commits_page_url is not None
    assert result.error_message is None
    
    # Verify wiki client called
    mock_wiki_client.create_release_documentation.assert_called_once()
    call_args = mock_wiki_client.create_release_documentation.call_args
    assert call_args.kwargs["organization"] == "test-org"
    assert call_args.kwargs["project"] == "test-project"
    assert call_args.kwargs["release_name"] == "test-release"
    assert call_args.kwargs["branch_name"] == "release/test-release"
    assert call_args.kwargs["created_by"] == "test-user"


@pytest.mark.asyncio
async def test_create_wiki_documentation_failure_graceful(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test wiki documentation creation handles failures gracefully.
    
    Requirements: 13.9
    """
    # Setup: Wiki creation fails
    mock_wiki_client.create_release_documentation.return_value = WikiDocumentationResult(
        success=False,
        main_page_url=None,
        commits_page_url=None,
        error_message="Wiki API error"
    )
    
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    mock_ado_client.get_commits_for_work_item.return_value = []
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        result = await release_manager.create_wiki_documentation(
            organization="test-org",
            project="test-project",
            release_name="test-release",
            branch_name="release/test-release",
            created_by="test-user",
            work_item_ids=[12345],
            commit_ids=[]
        )
    
    # Verify: Failure is returned but doesn't raise exception
    assert result.success is False
    assert result.error_message == "Wiki API error"


# Tests for get_release_history (Task 6.5)

@pytest.mark.asyncio
async def test_get_release_history_success(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test successful release history retrieval.
    
    Requirements: 11.1, 11.2, 11.3, 11.4, 11.6
    """
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        releases = await release_manager.get_release_history(
            organization="test-org",
            project="test-project",
            limit=50,
            offset=0
        )
    
    # Verify
    assert len(releases) == 1
    assert releases[0]["name"] == "test-release"
    assert releases[0]["branch_name"] == "release/test-release"
    assert releases[0]["organization"] == "test-org"
    assert releases[0]["project"] == "test-project"
    
    # Verify wiki client called
    mock_wiki_client.get_release_list.assert_called_once_with(
        "test-org", "test-project", 50, 0
    )


@pytest.mark.asyncio
async def test_get_release_history_with_pagination(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test release history retrieval with pagination.
    
    Requirements: 11.5
    """
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        releases = await release_manager.get_release_history(
            organization="test-org",
            project="test-project",
            limit=20,
            offset=10
        )
    
    # Verify pagination parameters passed
    mock_wiki_client.get_release_list.assert_called_once_with(
        "test-org", "test-project", 20, 10
    )


@pytest.mark.asyncio
async def test_get_release_details_success(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test successful release details retrieval.
    
    Requirements: 11.1, 11.2, 11.3, 11.4, 11.6
    """
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        details = await release_manager.get_release_details(
            organization="test-org",
            project="test-project",
            release_name="test-release"
        )
    
    # Verify
    assert details is not None
    assert details["name"] == "test-release"
    assert details["branch_name"] == "release/test-release"
    assert details["created_by"] == "test-user"
    assert len(details["work_items"]) == 2
    assert len(details["commits"]) == 2
    
    # Verify wiki client called
    mock_wiki_client.get_release_details.assert_called_once_with(
        "test-org", "test-project", "test-release"
    )


@pytest.mark.asyncio
async def test_get_release_details_not_found(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test release details retrieval when release not found.
    
    Requirements: 11.4
    """
    # Setup: Release not found
    mock_wiki_client.get_release_details.return_value = None
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        details = await release_manager.get_release_details(
            organization="test-org",
            project="test-project",
            release_name="nonexistent-release"
        )
    
    # Verify
    assert details is None


# Tests for credential management

@pytest.mark.asyncio
async def test_get_azure_clients_success(release_manager):
    """
    Test successful Azure client creation.
    
    Requirements: 10.5
    """
    ado_client, wiki_client = release_manager._get_azure_clients("test-org")
    
    # Verify
    assert ado_client is not None
    assert wiki_client is not None
    release_manager.config_manager.get_credentials.assert_called_once_with("test-org")


def test_get_azure_clients_no_credentials(release_manager):
    """
    Test Azure client creation fails without credentials.
    
    Requirements: 10.6
    """
    # Setup: No credentials
    release_manager.config_manager.get_credentials.return_value = None
    
    # Execute and verify
    with pytest.raises(ReleaseCredentialsError) as exc_info:
        release_manager._get_azure_clients("unknown-org")
    
    assert "No credentials found" in str(exc_info.value)


# Integration-style tests

@pytest.mark.asyncio
async def test_full_release_workflow(
    release_manager,
    mock_ado_client,
    mock_wiki_client
):
    """
    Test complete release workflow from validation to wiki creation.
    
    Requirements: 7.3, 7.4, 7.5, 7.6, 7.7, 13.1
    """
    # Setup: All work items closed
    mock_ado_client.get_work_items_by_ids.return_value = [
        WorkItem(
            id=12345,
            title="Test PBI 1",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        ),
        WorkItem(
            id=12346,
            title="Test PBI 2",
            state="Closed",
            work_item_type="Product Backlog Item",
            tags=["test"],
            assigned_to="user@example.com",
            created_date=datetime.now(timezone.utc)
        )
    ]
    
    mock_ado_client.get_commits_for_work_item.return_value = [
        Commit(
            commit_id="commit1",
            author="Test Author",
            author_email="author@example.com",
            commit_date=datetime.now(timezone.utc),
            message="Test commit 1",
            work_item_ids=[12345],
            changed_files=["file1.py"]
        )
    ]
    
    with patch.object(
        release_manager,
        '_get_azure_clients',
        return_value=(mock_ado_client, mock_wiki_client)
    ):
        # Execute full workflow
        result = await release_manager.create_release(
            name="integration-test-release",
            organization="test-org",
            project="test-project",
            work_item_ids=[12345, 12346],
            commit_ids=["commit1", "commit2", "commit3"],
            base_branch="main",
            created_by="integration-test-user"
        )
    
    # Verify complete workflow
    assert result.success is True
    assert result.release_id is not None
    assert result.branch_name == "release/integration-test-release"
    assert result.wiki_urls is not None
    
    # Verify all steps executed
    assert mock_ado_client.get_work_items_by_ids.call_count >= 1
    release_manager.git_service.create_branch.assert_called_once()
    release_manager.git_service.apply_commits.assert_called_once()
    mock_wiki_client.create_release_documentation.assert_called_once()
