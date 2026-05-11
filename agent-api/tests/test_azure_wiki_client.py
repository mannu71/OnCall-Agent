"""
Tests for Azure Wiki Client Service

Tests cover:
- Authentication and connection validation
- Wiki page retrieval
- Wiki page creation and updates
- Release documentation generation
- Release history retrieval
- Error handling and retry logic
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.services.azure_wiki_client import (
    AzureWikiClient,
    AzureWikiError,
    AzureWikiConnectionError,
    AzureWikiAuthenticationError,
    AzureWikiNotFoundError,
    AzureWikiConflictError
)
from app.models.azure_devops import (
    WikiInfo,
    WikiPageResult,
    WikiDocumentationResult,
    WorkItem,
    Commit
)


@pytest.fixture
def wiki_client():
    """Create Azure Wiki client with test PAT"""
    return AzureWikiClient(pat="test_pat_token")


@pytest.fixture
def sample_work_items():
    """Sample work items for testing"""
    return [
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


@pytest.fixture
def sample_commits():
    """Sample commits for testing"""
    return [
        Commit(
            commit_id="abc123def456",
            author="John Doe",
            author_email="john@example.com",
            commit_date=datetime(2024, 1, 15, 10, 30, 0),
            message="Add login endpoint #12345",
            work_item_ids=[12345],
            changed_files=["src/auth/login.py"]
        ),
        Commit(
            commit_id="def456ghi789",
            author="Jane Smith",
            author_email="jane@example.com",
            commit_date=datetime(2024, 1, 16, 14, 20, 0),
            message="Add password reset #12346",
            work_item_ids=[12346],
            changed_files=["src/auth/reset.py"]
        )
    ]


class TestAzureWikiClientInitialization:
    """Test client initialization and authentication"""
    
    def test_client_initialization(self):
        """Test client initializes with PAT"""
        client = AzureWikiClient(pat="test_pat")
        assert client.pat == "test_pat"
        assert client.timeout == 30.0
        assert client.client is not None
    
    def test_client_custom_timeout(self):
        """Test client with custom timeout"""
        client = AzureWikiClient(pat="test_pat", timeout=60.0)
        assert client.timeout == 60.0
    
    def test_pat_encoding(self):
        """Test PAT encoding for Basic auth"""
        client = AzureWikiClient(pat="test_pat")
        encoded = client._encode_pat("test_pat")
        
        import base64
        expected = base64.b64encode(b":test_pat").decode()
        assert encoded == expected
    
    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager"""
        async with AzureWikiClient(pat="test_pat") as client:
            assert client is not None
        # Client should be closed after context


class TestConnectionValidation:
    """Test connection validation"""
    
    @pytest.mark.asyncio
    async def test_validate_connection_success(self, wiki_client):
        """Test successful connection validation"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "id": "wiki-123",
                    "name": "TestWiki",
                    "type": "projectWiki",
                    "projectId": "project-123",
                    "repositoryId": "repo-123"
                }
            ]
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response):
            result = await wiki_client.validate_connection("test-org", "test-project")
            assert result is True
    
    @pytest.mark.asyncio
    async def test_validate_connection_auth_failure(self, wiki_client):
        """Test connection validation with auth failure"""
        with patch.object(
            wiki_client, 
            '_make_request', 
            side_effect=AzureWikiAuthenticationError("Auth failed")
        ):
            result = await wiki_client.validate_connection("test-org", "test-project")
            assert result is False
    
    @pytest.mark.asyncio
    async def test_validate_connection_not_found(self, wiki_client):
        """Test connection validation when wiki not found"""
        with patch.object(
            wiki_client, 
            '_make_request', 
            side_effect=AzureWikiNotFoundError("Wiki not found")
        ):
            result = await wiki_client.validate_connection("test-org", "test-project")
            assert result is True  # Not found is still a valid connection


class TestWikiPageRetrieval:
    """Test wiki page retrieval methods"""
    
    @pytest.mark.asyncio
    async def test_get_project_wiki_success(self, wiki_client):
        """Test successful project wiki retrieval"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "id": "wiki-123",
                    "name": "TestWiki",
                    "type": "projectWiki",
                    "projectId": "project-123",
                    "repositoryId": "repo-123"
                }
            ]
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response):
            wiki_info = await wiki_client.get_project_wiki("test-org", "test-project")
            
            assert isinstance(wiki_info, WikiInfo)
            assert wiki_info.id == "wiki-123"
            assert wiki_info.name == "TestWiki"
            assert wiki_info.project_id == "project-123"
            assert wiki_info.repository_id == "repo-123"
    
    @pytest.mark.asyncio
    async def test_get_project_wiki_not_found(self, wiki_client):
        """Test project wiki not found"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": []  # No wikis
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response):
            with pytest.raises(AzureWikiNotFoundError):
                await wiki_client.get_project_wiki("test-org", "test-project")
    
    @pytest.mark.asyncio
    async def test_get_project_wiki_only_code_wiki(self, wiki_client):
        """Test when only code wiki exists (not project wiki)"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "value": [
                {
                    "id": "wiki-456",
                    "name": "CodeWiki",
                    "type": "codeWiki",  # Not projectWiki
                    "projectId": "project-123"
                }
            ]
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response):
            with pytest.raises(AzureWikiNotFoundError):
                await wiki_client.get_project_wiki("test-org", "test-project")


class TestWikiPageCreation:
    """Test wiki page creation and update methods"""
    
    @pytest.mark.asyncio
    async def test_create_wiki_page_success(self, wiki_client):
        """Test successful wiki page creation"""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "page-123",
            "path": "/Releases/Release-1.0"
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response):
            result = await wiki_client.create_wiki_page(
                "test-org",
                "test-project",
                "wiki-123",
                "/Releases/Release-1.0",
                "# Release 1.0\n\nContent here"
            )
            
            assert isinstance(result, WikiPageResult)
            assert result.success is True
            assert result.page_id == "page-123"
            assert result.page_path == "/Releases/Release-1.0"
            assert result.url is not None
            assert "test-org" in result.url
            assert "test-project" in result.url
    
    @pytest.mark.asyncio
    async def test_create_wiki_page_adds_leading_slash(self, wiki_client):
        """Test that leading slash is added to path if missing"""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "page-123",
            "path": "/Releases/Release-1.0"
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response) as mock_request:
            await wiki_client.create_wiki_page(
                "test-org",
                "test-project",
                "wiki-123",
                "Releases/Release-1.0",  # No leading slash
                "Content"
            )
            
            # Check that the request was made with leading slash
            call_args = mock_request.call_args
            assert call_args[1]['params']['path'] == "/Releases/Release-1.0"
    
    @pytest.mark.asyncio
    async def test_create_wiki_page_conflict(self, wiki_client):
        """Test wiki page creation when page already exists"""
        with patch.object(
            wiki_client, 
            '_make_request', 
            side_effect=AzureWikiConflictError("Page exists")
        ):
            result = await wiki_client.create_wiki_page(
                "test-org",
                "test-project",
                "wiki-123",
                "/Releases/Release-1.0",
                "Content"
            )
            
            assert result.success is False
            assert "already exists" in result.error_message
    
    @pytest.mark.asyncio
    async def test_update_wiki_page_success(self, wiki_client):
        """Test successful wiki page update"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "page-123",
            "path": "/Releases/Release-1.0"
        }
        
        with patch.object(wiki_client, '_make_request', return_value=mock_response):
            result = await wiki_client.update_wiki_page(
                "test-org",
                "test-project",
                "wiki-123",
                "/Releases/Release-1.0",
                "# Updated content",
                "version-etag-123"
            )
            
            assert result.success is True
            assert result.page_id == "page-123"
    
    @pytest.mark.asyncio
    async def test_update_wiki_page_version_conflict(self, wiki_client):
        """Test wiki page update with version conflict"""
        with patch.object(
            wiki_client, 
            '_make_request', 
            side_effect=AzureWikiConflictError("Version conflict")
        ):
            result = await wiki_client.update_wiki_page(
                "test-org",
                "test-project",
                "wiki-123",
                "/Releases/Release-1.0",
                "Content",
                "old-version"
            )
            
            assert result.success is False
            assert "conflict" in result.error_message.lower()


class TestReleaseDocumentation:
    """Test release documentation generation"""
    
    @pytest.mark.asyncio
    async def test_create_release_documentation_success(
        self, wiki_client, sample_work_items, sample_commits
    ):
        """Test successful release documentation creation"""
        # Mock get_project_wiki
        mock_wiki_info = WikiInfo(
            id="wiki-123",
            name="TestWiki",
            project_id="project-123",
            repository_id="repo-123"
        )
        
        # Mock create_wiki_page responses
        mock_main_response = MagicMock()
        mock_main_response.status_code = 201
        mock_main_response.json.return_value = {
            "id": "main-page-123",
            "path": "/Releases/Release-1.0"
        }
        
        mock_commits_response = MagicMock()
        mock_commits_response.status_code = 201
        mock_commits_response.json.return_value = {
            "id": "commits-page-123",
            "path": "/Releases/Release-1.0/Commits"
        }
        
        with patch.object(wiki_client, 'get_project_wiki', return_value=mock_wiki_info):
            with patch.object(
                wiki_client, 
                '_make_request', 
                side_effect=[mock_main_response, mock_commits_response]
            ):
                result = await wiki_client.create_release_documentation(
                    "test-org",
                    "test-project",
                    "Release-1.0",
                    "release/1.0",
                    "John Doe",
                    sample_work_items,
                    sample_commits
                )
                
                assert isinstance(result, WikiDocumentationResult)
                assert result.success is True
                assert result.main_page_url is not None
                assert result.commits_page_url is not None
                assert "Release-1.0" in result.main_page_url
    
    @pytest.mark.asyncio
    async def test_create_release_documentation_main_page_fails(
        self, wiki_client, sample_work_items, sample_commits
    ):
        """Test release documentation when main page creation fails"""
        mock_wiki_info = WikiInfo(
            id="wiki-123",
            name="TestWiki",
            project_id="project-123",
            repository_id="repo-123"
        )
        
        with patch.object(wiki_client, 'get_project_wiki', return_value=mock_wiki_info):
            with patch.object(
                wiki_client, 
                '_make_request', 
                side_effect=AzureWikiError("Creation failed")
            ):
                result = await wiki_client.create_release_documentation(
                    "test-org",
                    "test-project",
                    "Release-1.0",
                    "release/1.0",
                    "John Doe",
                    sample_work_items,
                    sample_commits
                )
                
                assert result.success is False
                assert result.main_page_url is None
                assert "main page" in result.error_message.lower()
    
    def test_generate_main_release_page(self, wiki_client, sample_work_items):
        """Test main release page content generation"""
        content = wiki_client._generate_main_release_page(
            "Release-1.0",
            "release/1.0",
            "John Doe",
            sample_work_items
        )
        
        assert "# Release: Release-1.0" in content
        assert "release/1.0" in content
        assert "John Doe" in content
        assert "12345" in content
        assert "Implement user authentication" in content
        assert "12346" in content
        assert "Add password reset feature" in content
        assert "[Commits Details]" in content
    
    def test_generate_commits_page(self, wiki_client, sample_work_items, sample_commits):
        """Test commits page content generation"""
        content = wiki_client._generate_commits_page(
            "Release-1.0",
            sample_work_items,
            sample_commits
        )
        
        assert "# Commits for Release: Release-1.0" in content
        assert "abc123de" in content  # Short commit hash
        assert "Add login endpoint" in content
        assert "John Doe" in content
        assert "john@example.com" in content
        assert "Developer Approval Section" in content
        assert "- [ ]" in content  # Checkbox
        assert "Pending approval" in content
        assert "Total Commits: 2" in content


class TestReleaseHistory:
    """Test release history retrieval"""
    
    @pytest.mark.asyncio
    async def test_get_release_list_success(self, wiki_client):
        """Test successful release list retrieval"""
        mock_wiki_info = WikiInfo(
            id="wiki-123",
            name="TestWiki",
            project_id="project-123",
            repository_id="repo-123"
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "subPages": [
                {"path": "/Releases/Release-1.0"},
                {"path": "/Releases/Release-1.1"},
                {"path": "/Releases/Release-2.0"}
            ]
        }
        
        with patch.object(wiki_client, 'get_project_wiki', return_value=mock_wiki_info):
            with patch.object(wiki_client, '_make_request', return_value=mock_response):
                releases = await wiki_client.get_release_list("test-org", "test-project")
                
                assert len(releases) == 3
                assert releases[0]["name"] == "Release-1.0"
                assert releases[1]["name"] == "Release-1.1"
                assert releases[2]["name"] == "Release-2.0"
    
    @pytest.mark.asyncio
    async def test_get_release_list_with_pagination(self, wiki_client):
        """Test release list with pagination"""
        mock_wiki_info = WikiInfo(
            id="wiki-123",
            name="TestWiki",
            project_id="project-123",
            repository_id="repo-123"
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "subPages": [
                {"path": "/Releases/Release-1.0"},
                {"path": "/Releases/Release-1.1"},
                {"path": "/Releases/Release-2.0"},
                {"path": "/Releases/Release-2.1"}
            ]
        }
        
        with patch.object(wiki_client, 'get_project_wiki', return_value=mock_wiki_info):
            with patch.object(wiki_client, '_make_request', return_value=mock_response):
                # Get second page with limit 2
                releases = await wiki_client.get_release_list(
                    "test-org", "test-project", limit=2, offset=2
                )
                
                assert len(releases) == 2
                assert releases[0]["name"] == "Release-2.0"
                assert releases[1]["name"] == "Release-2.1"
    
    @pytest.mark.asyncio
    async def test_get_release_list_not_found(self, wiki_client):
        """Test release list when no releases exist"""
        with patch.object(
            wiki_client, 
            'get_project_wiki', 
            side_effect=AzureWikiNotFoundError("Not found")
        ):
            releases = await wiki_client.get_release_list("test-org", "test-project")
            assert releases == []
    
    @pytest.mark.asyncio
    async def test_get_release_details_success(self, wiki_client):
        """Test successful release details retrieval"""
        mock_wiki_info = WikiInfo(
            id="wiki-123",
            name="TestWiki",
            project_id="project-123",
            repository_id="repo-123"
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "content": "# Release: Release-1.0\n\nRelease content here"
        }
        
        with patch.object(wiki_client, 'get_project_wiki', return_value=mock_wiki_info):
            with patch.object(wiki_client, '_make_request', return_value=mock_response):
                details = await wiki_client.get_release_details(
                    "test-org", "test-project", "Release-1.0"
                )
                
                assert details is not None
                assert details["name"] == "Release-1.0"
                assert "Release content here" in details["content"]
                assert details["url"] is not None
    
    @pytest.mark.asyncio
    async def test_get_release_details_not_found(self, wiki_client):
        """Test release details when release doesn't exist"""
        mock_wiki_info = WikiInfo(
            id="wiki-123",
            name="TestWiki",
            project_id="project-123",
            repository_id="repo-123"
        )
        
        with patch.object(wiki_client, 'get_project_wiki', return_value=mock_wiki_info):
            with patch.object(
                wiki_client, 
                '_make_request', 
                side_effect=AzureWikiNotFoundError("Not found")
            ):
                details = await wiki_client.get_release_details(
                    "test-org", "test-project", "NonExistent"
                )
                assert details is None


class TestErrorHandling:
    """Test error handling and retry logic"""
    
    @pytest.mark.asyncio
    async def test_authentication_error(self, wiki_client):
        """Test authentication error handling"""
        mock_response = MagicMock()
        mock_response.status_code = 401
        
        with patch.object(wiki_client.client, 'request', return_value=mock_response):
            with pytest.raises(AzureWikiAuthenticationError):
                await wiki_client._make_request("GET", "https://test.com")
    
    @pytest.mark.asyncio
    async def test_not_found_error(self, wiki_client):
        """Test not found error handling"""
        mock_response = MagicMock()
        mock_response.status_code = 404
        
        with patch.object(wiki_client.client, 'request', return_value=mock_response):
            with pytest.raises(AzureWikiNotFoundError):
                await wiki_client._make_request("GET", "https://test.com")
    
    @pytest.mark.asyncio
    async def test_conflict_error(self, wiki_client):
        """Test conflict error handling"""
        mock_response = MagicMock()
        mock_response.status_code = 409
        
        with patch.object(wiki_client.client, 'request', return_value=mock_response):
            with pytest.raises(AzureWikiConflictError):
                await wiki_client._make_request("GET", "https://test.com")
    
    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self, wiki_client):
        """Test retry logic on rate limit"""
        # First two calls return 429, third succeeds
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200
        
        with patch.object(
            wiki_client.client, 
            'request', 
            side_effect=[mock_response_429, mock_response_429, mock_response_200]
        ):
            response = await wiki_client._make_request("GET", "https://test.com")
            assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_retry_exhausted(self, wiki_client):
        """Test when all retries are exhausted"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        
        with patch.object(wiki_client.client, 'request', return_value=mock_response):
            with pytest.raises(AzureWikiConnectionError):
                await wiki_client._make_request("GET", "https://test.com")
    
    @pytest.mark.asyncio
    async def test_network_error_retry(self, wiki_client):
        """Test retry on network error"""
        # First two calls raise network error, third succeeds
        mock_response = MagicMock()
        mock_response.status_code = 200
        
        with patch.object(
            wiki_client.client, 
            'request', 
            side_effect=[
                httpx.NetworkError("Connection failed"),
                httpx.NetworkError("Connection failed"),
                mock_response
            ]
        ):
            response = await wiki_client._make_request("GET", "https://test.com")
            assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_timeout_error(self, wiki_client):
        """Test timeout error handling"""
        with patch.object(
            wiki_client.client, 
            'request', 
            side_effect=httpx.TimeoutException("Timeout")
        ):
            with pytest.raises(AzureWikiConnectionError):
                await wiki_client._make_request("GET", "https://test.com")
