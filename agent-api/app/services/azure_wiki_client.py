"""
Azure Wiki Client Service

This module provides integration with Azure Wiki REST API for creating and managing wiki pages.
Supports PAT-based authentication with retry logic and comprehensive error handling.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8
"""

import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any

import httpx

from app.models.azure_devops import (
    WikiInfo,
    WikiPageResult,
    WikiDocumentationResult,
    WorkItem,
    Commit
)


# Custom Exceptions

class AzureWikiError(Exception):
    """Base exception for Azure Wiki errors"""
    pass


class AzureWikiConnectionError(AzureWikiError):
    """Network/connectivity issues"""
    pass


class AzureWikiAuthenticationError(AzureWikiError):
    """Invalid PAT or permissions"""
    pass


class AzureWikiNotFoundError(AzureWikiError):
    """Wiki or page not found"""
    pass


class AzureWikiConflictError(AzureWikiError):
    """Page already exists or version conflict"""
    pass


# Azure Wiki Client

class AzureWikiClient:
    """
    Client for Azure Wiki REST API.
    
    Provides methods for:
    - Wiki page retrieval
    - Wiki page creation and updates
    - Release documentation generation
    - Release history retrieval from wiki
    
    Features:
    - PAT-based authentication
    - Exponential backoff retry logic (max 3 retries)
    - Comprehensive error handling
    
    Requirements: 13.8
    """
    
    # API version for Azure Wiki REST API
    API_VERSION = "7.1"
    
    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_RETRY_DELAY = 1.0  # seconds
    MAX_RETRY_DELAY = 10.0  # seconds
    
    def __init__(self, pat: str, timeout: float = 30.0):
        """
        Initialize Azure Wiki client.
        
        Args:
            pat: Personal Access Token for authentication
            timeout: Request timeout in seconds (default: 30.0)
            
        Requirements: 13.8
        """
        self.pat = pat
        self.timeout = timeout
        
        # Create HTTP client with authentication
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Basic {self._encode_pat(pat)}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        )
    
    def _encode_pat(self, pat: str) -> str:
        """
        Encode PAT for Basic authentication.
        
        Azure DevOps uses Basic auth with empty username and PAT as password.
        
        Args:
            pat: Personal Access Token
            
        Returns:
            Base64-encoded credentials
        """
        import base64
        # Azure DevOps PAT auth uses empty username
        credentials = f":{pat}"
        return base64.b64encode(credentials.encode()).decode()
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
    
    async def __aenter__(self):
        """Async context manager entry"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()
    
    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """
        Make HTTP request with retry logic.
        
        Implements exponential backoff for transient failures.
        
        Args:
            method: HTTP method (GET, POST, PUT, etc.)
            url: Request URL
            **kwargs: Additional arguments for httpx request
            
        Returns:
            HTTP response
            
        Raises:
            AzureWikiConnectionError: Network/connectivity issues
            AzureWikiAuthenticationError: Invalid PAT or permissions
            AzureWikiNotFoundError: Resource not found
            AzureWikiConflictError: Page already exists or version conflict
            
        Requirements: 13.8
        """
        retry_delay = self.INITIAL_RETRY_DELAY
        last_exception = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.request(method, url, **kwargs)
                
                # Handle HTTP error status codes
                if response.status_code == 401:
                    raise AzureWikiAuthenticationError(
                        "Authentication failed. Invalid PAT or insufficient permissions."
                    )
                elif response.status_code == 404:
                    raise AzureWikiNotFoundError(
                        f"Resource not found: {url}"
                    )
                elif response.status_code == 409:
                    raise AzureWikiConflictError(
                        "Page already exists or version conflict"
                    )
                elif response.status_code == 429:
                    # Rate limit - retry with backoff
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                        continue
                    else:
                        raise AzureWikiConnectionError(
                            "Azure Wiki API rate limit exceeded. Please try again later."
                        )
                elif response.status_code >= 500:
                    # Server error - retry with backoff
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                        continue
                    else:
                        raise AzureWikiConnectionError(
                            f"Azure Wiki API server error: {response.status_code} — {response.text[:400]}"
                        )
                elif response.status_code >= 400:
                    # Other client errors
                    error_detail = response.text
                    raise AzureWikiError(
                        f"Azure Wiki API error {response.status_code}: {error_detail}"
                    )
                
                # Success
                return response
                
            except httpx.TimeoutException as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                    continue
                else:
                    raise AzureWikiConnectionError(
                        f"Request timeout after {self.MAX_RETRIES} attempts"
                    ) from e
                    
            except httpx.NetworkError as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                    continue
                else:
                    raise AzureWikiConnectionError(
                        f"Network error after {self.MAX_RETRIES} attempts: {str(e)}"
                    ) from e
        
        # Should not reach here, but just in case
        if last_exception:
            raise AzureWikiConnectionError(
                f"Request failed after {self.MAX_RETRIES} attempts"
            ) from last_exception
        raise AzureWikiConnectionError("Request failed")
    
    async def validate_connection(
        self,
        organization: str,
        project: str
    ) -> bool:
        """
        Validate connection to Azure Wiki.
        
        Tests authentication and connectivity by attempting to get project wiki.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            
        Returns:
            True if connection is valid, False otherwise
            
        Requirements: 13.8
        """
        try:
            await self.get_project_wiki(organization, project)
            return True
        except (AzureWikiAuthenticationError, AzureWikiConnectionError):
            return False
        except AzureWikiNotFoundError:
            # Wiki not found is still a valid connection
            return True
    
    async def get_project_wiki(
        self,
        organization: str,
        project: str
    ) -> WikiInfo:
        """
        Get the project wiki information.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            
        Returns:
            WikiInfo object
            
        Raises:
            AzureWikiNotFoundError: If project wiki doesn't exist
            AzureWikiError: If API call fails
            
        Requirements: 13.8
        """
        url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis"
        params = {"api-version": self.API_VERSION}
        
        response = await self._make_request("GET", url, params=params)
        data = response.json()
        
        # Find the project wiki (type = "projectWiki")
        wikis = data.get("value", [])
        project_wiki = None
        
        for wiki in wikis:
            if wiki.get("type") == "projectWiki":
                project_wiki = wiki
                break
        
        if not project_wiki:
            raise AzureWikiNotFoundError(
                f"Project wiki not found for {organization}/{project}"
            )
        
        return WikiInfo(
            id=project_wiki["id"],
            name=project_wiki["name"],
            project_id=project_wiki["projectId"],
            repository_id=project_wiki.get("repositoryId", "")
        )
    
    async def create_wiki_page(
        self,
        organization: str,
        project: str,
        wiki_id: str,
        path: str,
        content: str
    ) -> WikiPageResult:
        """
        Create a new wiki page with markdown content.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            wiki_id: Wiki ID
            path: Page path (e.g., "/Releases/Release-1.0")
            content: Markdown content for the page
            
        Returns:
            WikiPageResult with success status and page URL
            
        Requirements: 13.1, 13.5, 13.6
        """
        try:
            import logging as _log
            _logger = _log.getLogger(__name__)

            # Ensure path starts with / and has no double slashes
            if not path.startswith("/"):
                path = f"/{path}"
            while "//" in path:
                path = path.replace("//", "/")

            _logger.info(f"Creating wiki page at path: '{path}' in wiki '{wiki_id}'")

            base_url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki_id}/pages"

            # ADO does not auto-create parent pages — ensure every ancestor exists first
            parts = [p for p in path.split("/") if p]
            for i in range(1, len(parts)):
                parent_path = "/" + "/".join(parts[:i])
                try:
                    await self._make_request(
                        "PUT", base_url,
                        params={"api-version": self.API_VERSION, "path": parent_path},
                        json={"content": f"# {parts[i - 1]}"}
                    )
                    _logger.info(f"Created/verified parent page: '{parent_path}'")
                except Exception as e:
                    _logger.info(f"Parent page '{parent_path}' skipped: {e}")

            params = {
                "api-version": self.API_VERSION,
                "path": path
            }
            
            payload = {
                "content": content
            }

            response = await self._make_request("PUT", base_url, params=params, json=payload)
            data = response.json()

            # Build page URL
            import urllib.parse
            encoded_path = urllib.parse.quote(path, safe="/")
            page_url = f"https://dev.azure.com/{organization}/{project}/_wiki/wikis/{wiki_id}?pagePath={encoded_path}"

            return WikiPageResult(
                success=True,
                page_id=data.get("id"),
                page_path=data.get("path"),
                url=page_url,
                error_message=None
            )

        except AzureWikiConflictError:
            # Page already exists — treat as success and return URL
            page_url = f"https://dev.azure.com/{organization}/{project}/_wiki/wikis/{wiki_id}?pagePath={path}"
            return WikiPageResult(
                success=True,
                page_id=None,
                page_path=path,
                url=page_url,
                error_message=None
            )
        except Exception as e:
            return WikiPageResult(
                success=False,
                page_id=None,
                page_path=path,
                url=None,
                error_message=str(e)
            )

    async def update_wiki_page(
        self,
        organization: str,
        project: str,
        wiki_id: str,
        path: str,
        content: str,
        version: str
    ) -> WikiPageResult:
        """
        Update an existing wiki page.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            wiki_id: Wiki ID
            path: Page path
            content: New markdown content
            version: ETag version for optimistic concurrency
            
        Returns:
            WikiPageResult with success status and page URL
            
        Requirements: 13.1, 13.5, 13.6
        """
        try:
            # Ensure path starts with /
            if not path.startswith("/"):
                path = f"/{path}"
            
            url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki_id}/pages"
            params = {
                "api-version": self.API_VERSION,
                "path": path
            }
            
            # Add If-Match header for version control
            headers = {
                "If-Match": version
            }
            
            payload = {
                "content": content
            }
            
            response = await self._make_request(
                "PUT", url, params=params, json=payload, headers=headers
            )
            data = response.json()
            
            # Build page URL
            page_url = f"https://dev.azure.com/{organization}/{project}/_wiki/wikis/{wiki_id}?pagePath={path}"
            
            return WikiPageResult(
                success=True,
                page_id=data.get("id"),
                page_path=data.get("path"),
                url=page_url,
                error_message=None
            )
            
        except AzureWikiConflictError:
            return WikiPageResult(
                success=False,
                page_id=None,
                page_path=path,
                url=None,
                error_message="Version conflict - page was modified by another user"
            )
        except Exception as e:
            return WikiPageResult(
                success=False,
                page_id=None,
                page_path=path,
                url=None,
                error_message=str(e)
            )
    
    async def create_release_documentation(
        self,
        organization: str,
        project: str,
        release_name: str,
        branch_name: str,
        created_by: str,
        work_items: List[WorkItem],
        commits: List[Commit],
        wiki_id: str
    ) -> WikiDocumentationResult:
        """
        Create complete release documentation in Azure Wiki.
        
        Creates:
        1. Main release page with PBI table and metadata
        2. Commits sub-page with commit details and approval section
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            release_name: Name of the release
            branch_name: Git branch name
            created_by: User who created the release
            work_items: List of work items in the release
            commits: List of commits in the release
            
        Returns:
            WikiDocumentationResult with URLs to created pages
            
        Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7
        """
        try:
            import logging as _logging
            _log = _logging.getLogger(__name__)

            resolved_wiki_id = wiki_id
            parent_path_prefix = ""  # path prefix when wiki_id is a page ID

            def _looks_like_wiki_id(wid: str) -> bool:
                import re
                guid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                return '.' in wid or bool(re.match(guid_pattern, wid, re.I))

            # Step 1: always resolve the actual wiki name
            if not wiki_id or not _looks_like_wiki_id(wiki_id):
                wikis_url = (
                    f"https://dev.azure.com/{organization}/{project}"
                    f"/_apis/wiki/wikis"
                )
                wikis_resp = await self._make_request(
                    "GET", wikis_url,
                    params={"api-version": self.API_VERSION}
                )
                wikis = wikis_resp.json().get("value", [])
                if not wikis:
                    return WikiDocumentationResult(
                        success=False,
                        main_page_url=None,
                        commits_page_url=None,
                        error_message=(
                            f"No wikis found in project '{project}'. "
                            f"Please create a wiki first in Azure DevOps."
                        )
                    )
                resolved_wiki_id = wikis[0].get("name") or wikis[0].get("id")
                _log.info(f"Auto-resolved wiki_id '{wiki_id}' → '{resolved_wiki_id}'")

                # Step 2: if original value was a numeric page ID, look up that
                # page's path and use it as the parent prefix for new pages
                if wiki_id and wiki_id.isdigit():
                    try:
                        page_url = (
                            f"https://dev.azure.com/{organization}/{project}"
                            f"/_apis/wiki/wikis/{resolved_wiki_id}/pages/{wiki_id}"
                        )
                        page_resp = await self._make_request(
                            "GET", page_url,
                            params={"api-version": self.API_VERSION}
                        )
                        parent_path_prefix = page_resp.json().get("path", "")
                        _log.info(f"Parent page path resolved: '{parent_path_prefix}'")
                    except Exception as e:
                        _log.warning(f"Could not resolve parent page path for id {wiki_id}: {e}")

            # Generate main release page content
            main_page_content = self._generate_main_release_page(
                release_name, branch_name, created_by, work_items
            )

            # Create main release page (under parent if given)
            main_page_path = f"{parent_path_prefix}/{release_name}"
            main_page_result = await self.create_wiki_page(
                organization, project, resolved_wiki_id, main_page_path, main_page_content
            )

            if not main_page_result.success:
                return WikiDocumentationResult(
                    success=False,
                    main_page_url=None,
                    commits_page_url=None,
                    error_message=f"Failed to create main page: {main_page_result.error_message}"
                )

            # Generate commits sub-page content
            commits_page_content = self._generate_commits_page(
                release_name, work_items, commits
            )
            
            # Create commits sub-page
            commits_page_path = f"{parent_path_prefix}/{release_name}/Commits"
            commits_page_result = await self.create_wiki_page(
                organization, project, resolved_wiki_id, commits_page_path, commits_page_content
            )
            
            if not commits_page_result.success:
                return WikiDocumentationResult(
                    success=False,
                    main_page_url=main_page_result.url,
                    commits_page_url=None,
                    error_message=f"Failed to create commits page: {commits_page_result.error_message}"
                )
            
            return WikiDocumentationResult(
                success=True,
                main_page_url=main_page_result.url,
                commits_page_url=commits_page_result.url,
                error_message=None
            )
            
        except Exception as e:
            return WikiDocumentationResult(
                success=False,
                main_page_url=None,
                commits_page_url=None,
                error_message=str(e)
            )
    
    def _generate_main_release_page(
        self,
        release_name: str,
        branch_name: str,
        created_by: str,
        work_items: List[WorkItem]
    ) -> str:
        """
        Generate markdown content for main release page.
        
        Args:
            release_name: Name of the release
            branch_name: Git branch name
            created_by: User who created the release
            work_items: List of work items
            
        Returns:
            Markdown content
            
        Requirements: 13.1, 13.2, 13.3, 13.4
        """
        from datetime import timezone
        created_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Build PBI table rows
        pbi_rows = []
        for wi in work_items:
            tags_str = ", ".join(wi.tags) if wi.tags else "None"
            pbi_rows.append(
                f"| {wi.id} | {wi.title} | {wi.state} | {tags_str} |"
            )
        
        pbi_table = "\n".join(pbi_rows) if pbi_rows else "| - | No work items | - | - |"
        
        content = f"""# Release: {release_name}

## Release Information

| Property | Value |
|----------|-------|
| Branch Name | {branch_name} |
| Created Date | {created_date} |
| Created By | {created_by} |
| Status | Created |

## Product Backlog Items

| PBI ID | Title | State | Tags |
|--------|-------|-------|------|
{pbi_table}

## Links

- [Commits Details](./{release_name}/Commits)
"""
        
        return content
    
    def _generate_commits_page(
        self,
        release_name: str,
        work_items: List[WorkItem],
        commits: List[Commit]
    ) -> str:
        """
        Generate markdown content for commits page with approval section.
        
        Args:
            release_name: Name of the release
            work_items: List of work items
            commits: List of commits
            
        Returns:
            Markdown content
            
        Requirements: 13.1, 13.5, 13.6, 13.7
        """
        from datetime import timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Group commits by work item
        commits_by_wi: Dict[int, List[Commit]] = {}
        for commit in commits:
            for wi_id in commit.work_item_ids:
                if wi_id not in commits_by_wi:
                    commits_by_wi[wi_id] = []
                commits_by_wi[wi_id].append(commit)
        
        # Build commit sections
        commit_sections = []
        approval_sections = []
        
        for wi in work_items:
            wi_commits = commits_by_wi.get(wi.id, [])
            
            if wi_commits:
                # Commit list section
                commit_list = []
                for commit in wi_commits:
                    commit_date = commit.commit_date.strftime("%Y-%m-%d %H:%M:%S")
                    commit_list.append(
                        f"- **{commit.commit_id[:8]}** - {commit.message}\n"
                        f"  - Author: {commit.author} ({commit.author_email})\n"
                        f"  - Date: {commit_date}"
                    )
                
                commit_sections.append(
                    f"### PBI #{wi.id}: {wi.title}\n\n" + "\n".join(commit_list)
                )
                
                # Approval section
                approval_list = []
                for commit in wi_commits:
                    commit_date = commit.commit_date.strftime("%Y-%m-%d %H:%M:%S")
                    approval_list.append(
                        f"- [ ] **{commit.commit_id[:8]}** - {commit.message}\n"
                        f"  - **Author**: {commit.author} ({commit.author_email})\n"
                        f"  - **Date**: {commit_date}\n"
                        f"  - **Approved by**: _Pending approval from {commit.author}_"
                    )
                
                approval_sections.append(
                    f"#### PBI #{wi.id}: {wi.title}\n\n" + "\n\n".join(approval_list)
                )
        
        commit_sections_str = "\n\n".join(commit_sections) if commit_sections else "No commits found."
        approval_sections_str = "\n\n".join(approval_sections) if approval_sections else "No commits to approve."
        
        total_commits = len(commits)
        
        content = f"""# Commits for Release: {release_name}

## Commit List

{commit_sections_str}

## Developer Approval Section

### Instructions for Developers
Please check the box next to your commits to approve them for this release.
You can edit this page directly in Azure Wiki to mark your approvals.

### Commit Approvals

{approval_sections_str}

### Approval Status Summary
- Total Commits: {total_commits}
- Approved: 0
- Pending: {total_commits}

---
*Last Updated: {timestamp}*

*Developers: Edit this page to check boxes and add your name next to "Approved by"*
"""
        
        return content
    
    async def get_release_list(
        self,
        organization: str,
        project: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get list of releases from wiki pages.
        
        Retrieves release pages from /Releases/ path in wiki.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            limit: Maximum number of releases to return
            offset: Number of releases to skip
            
        Returns:
            List of release information dictionaries
            
        Requirements: 11.1, 11.2, 11.3, 11.4, 11.6
        """
        try:
            # Get project wiki
            wiki_info = await self.get_project_wiki(organization, project)
            
            # Get pages under /Releases/ path
            url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki_info.id}/pages"
            params = {
                "api-version": self.API_VERSION,
                "path": "/Releases",
                "recursionLevel": "oneLevel"
            }
            
            response = await self._make_request("GET", url, params=params)
            data = response.json()
            
            # Extract release pages
            releases = []
            sub_pages = data.get("subPages", [])
            
            for page in sub_pages[offset:offset + limit]:
                release_name = page.get("path", "").split("/")[-1]
                releases.append({
                    "name": release_name,
                    "path": page.get("path"),
                    "url": f"https://dev.azure.com/{organization}/{project}/_wiki/wikis/{wiki_info.id}?pagePath={page.get('path')}"
                })
            
            return releases
            
        except AzureWikiNotFoundError:
            # No releases found
            return []
        except Exception:
            # Error retrieving releases
            return []
    
    async def get_release_details(
        self,
        organization: str,
        project: str,
        release_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific release from wiki.
        
        Parses wiki page content to extract release information.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            release_name: Name of the release
            
        Returns:
            Dictionary with release details or None if not found
            
        Requirements: 11.1, 11.2, 11.3, 11.4, 11.6
        """
        try:
            # Get project wiki
            wiki_info = await self.get_project_wiki(organization, project)
            
            # Get release page content
            page_path = f"/Releases/{release_name}"
            url = f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis/{wiki_info.id}/pages"
            params = {
                "api-version": self.API_VERSION,
                "path": page_path
            }
            
            response = await self._make_request("GET", url, params=params)
            data = response.json()
            
            # Parse page content
            content = data.get("content", "")
            
            # Extract basic information (simple parsing)
            release_info = {
                "name": release_name,
                "path": page_path,
                "content": content,
                "url": f"https://dev.azure.com/{organization}/{project}/_wiki/wikis/{wiki_info.id}?pagePath={page_path}"
            }
            
            return release_info
            
        except AzureWikiNotFoundError:
            return None
        except Exception:
            return None
