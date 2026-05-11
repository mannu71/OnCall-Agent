"""
Azure DevOps Client Service

This module provides integration with Azure DevOps REST API for work items and commits.
Supports PAT-based authentication with retry logic and comprehensive error handling.

Requirements: 10.1, 10.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 5.1, 5.2, 5.5, 2.1.4
"""

import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any

import httpx

from app.models.azure_devops import WorkItem, Commit, ConnectionResult


# Custom Exceptions

class AzureDevOpsError(Exception):
    """Base exception for Azure DevOps errors"""
    pass


class AzureDevOpsConnectionError(AzureDevOpsError):
    """Network/connectivity issues"""
    pass


class AzureDevOpsAuthenticationError(AzureDevOpsError):
    """Invalid PAT or permissions"""
    pass


class AzureDevOpsNotFoundError(AzureDevOpsError):
    """Work item or repository not found"""
    pass


class AzureDevOpsRateLimitError(AzureDevOpsError):
    """API rate limit exceeded"""
    pass


# Azure DevOps Client

class AzureDevOpsClient:
    """
    Client for Azure DevOps REST API.
    
    Provides methods for:
    - Work item retrieval by tags or IDs
    - Commit fetching for work items
    - Connection validation
    
    Features:
    - PAT-based authentication
    - Exponential backoff retry logic (max 3 retries)
    - Comprehensive error handling
    
    Requirements: 10.1, 10.7, 3.6, 2.1.4
    """
    
    # API version for Azure DevOps REST API
    API_VERSION = "7.1"
    
    # Retry configuration
    MAX_RETRIES = 3
    INITIAL_RETRY_DELAY = 1.0  # seconds
    MAX_RETRY_DELAY = 10.0  # seconds
    
    def __init__(self, pat: str, timeout: float = 30.0):
        """
        Initialize Azure DevOps client.
        
        Args:
            pat: Personal Access Token for authentication
            timeout: Request timeout in seconds (default: 30.0)
            
        Requirements: 10.1, 2.1.4
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
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional arguments for httpx request
            
        Returns:
            HTTP response
            
        Raises:
            AzureDevOpsConnectionError: Network/connectivity issues
            AzureDevOpsAuthenticationError: Invalid PAT or permissions
            AzureDevOpsNotFoundError: Resource not found
            AzureDevOpsRateLimitError: Rate limit exceeded
            
        Requirements: 3.6, 10.7
        """
        retry_delay = self.INITIAL_RETRY_DELAY
        last_exception = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.request(method, url, **kwargs)
                
                # Handle HTTP error status codes
                if response.status_code == 401:
                    raise AzureDevOpsAuthenticationError(
                        f"Authentication failed on {method} {url} — "
                        f"Invalid PAT or insufficient permissions. "
                        f"For branch/PR creation the PAT requires 'Code (Read & Write)' scope."
                    )
                elif response.status_code == 403:
                    raise AzureDevOpsAuthenticationError(
                        f"Access denied (403) on {method} {url} — "
                        f"The PAT lacks required permissions. "
                        f"Ensure the PAT has 'Code (Read & Write)' scope in Azure DevOps."
                    )
                elif response.status_code == 404:
                    raise AzureDevOpsNotFoundError(
                        f"Resource not found: {url}"
                    )
                elif response.status_code == 429:
                    # Rate limit - retry with backoff
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                        continue
                    else:
                        raise AzureDevOpsRateLimitError(
                            "Azure DevOps API rate limit exceeded. Please try again later."
                        )
                elif response.status_code >= 500:
                    # Server error - retry with backoff
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                        continue
                    else:
                        raise AzureDevOpsConnectionError(
                            f"Azure DevOps API server error: {response.status_code}"
                        )
                elif response.status_code >= 400:
                    # Other client errors
                    error_detail = response.text
                    raise AzureDevOpsError(
                        f"Azure DevOps API error {response.status_code}: {error_detail}"
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
                    raise AzureDevOpsConnectionError(
                        f"Request timeout after {self.MAX_RETRIES} attempts"
                    ) from e
                    
            except httpx.NetworkError as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.MAX_RETRY_DELAY)
                    continue
                else:
                    raise AzureDevOpsConnectionError(
                        f"Network error after {self.MAX_RETRIES} attempts: {str(e)}"
                    ) from e
        
        # Should not reach here, but just in case
        if last_exception:
            raise AzureDevOpsConnectionError(
                f"Request failed after {self.MAX_RETRIES} attempts"
            ) from last_exception
        raise AzureDevOpsConnectionError("Request failed")
    
    async def validate_connection(
        self,
        organization: str,
        project: str
    ) -> ConnectionResult:
        """
        Validate connection to Azure DevOps.
        
        Tests authentication and connectivity by making a simple API call.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            
        Returns:
            ConnectionResult with success status and message
            
        Requirements: 10.1, 10.7, 2.1.4
        """
        try:
            # Try to get project information
            url = f"https://dev.azure.com/{organization}/_apis/projects/{project}"
            params = {"api-version": self.API_VERSION}
            
            response = await self._make_request("GET", url, params=params)
            
            if response.status_code == 200:
                return ConnectionResult(
                    success=True,
                    message=f"Successfully connected to {organization}/{project}",
                    organization=organization,
                    project=project
                )
            else:
                return ConnectionResult(
                    success=False,
                    message=f"Connection failed with status {response.status_code}",
                    organization=organization,
                    project=project
                )
                
        except AzureDevOpsAuthenticationError as e:
            return ConnectionResult(
                success=False,
                message=f"Authentication failed: {str(e)}",
                organization=organization,
                project=project
            )
        except AzureDevOpsConnectionError as e:
            return ConnectionResult(
                success=False,
                message=f"Connection error: {str(e)}",
                organization=organization,
                project=project
            )
        except Exception as e:
            return ConnectionResult(
                success=False,
                message=f"Unexpected error: {str(e)}",
                organization=organization,
                project=project
            )
    
    async def get_work_items_by_tags(
        self,
        organization: str,
        project: str,
        tags: List[str]
    ) -> List[WorkItem]:
        """
        Retrieve work items matching any of the specified tags.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            tags: List of tags to filter by (OR logic)
            
        Returns:
            List of WorkItem objects
            
        Raises:
            AzureDevOpsError: If API call fails
            
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 2.1.4
        """
        if not tags:
            return []
        
        # Build WIQL query for tags
        # Tags in Azure DevOps are stored as semicolon-separated string
        tag_conditions = " OR ".join([
            f"[System.Tags] CONTAINS '{tag}'"
            for tag in tags
        ])
        
        wiql_query = f"""
        SELECT [System.Id]
        FROM WorkItems
        WHERE {tag_conditions}
        AND [System.TeamProject] = '{project}'
        """
        
        # Execute WIQL query
        work_item_ids = await self._execute_wiql_query(organization, project, wiql_query)
        
        if not work_item_ids:
            return []
        
        # Fetch work item details
        return await self.get_work_items_by_ids(organization, project, work_item_ids)
    
    async def get_work_items_by_ids(
        self,
        organization: str,
        project: str,
        ids: List[int]
    ) -> List[WorkItem]:
        """
        Retrieve work items by PBI numbers.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            ids: List of work item IDs
            
        Returns:
            List of WorkItem objects
            
        Raises:
            AzureDevOpsError: If API call fails
            
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 2.1.4
        """
        if not ids:
            return []
        
        # Azure DevOps API supports batch retrieval of work items
        # Maximum 200 IDs per request
        batch_size = 200
        all_work_items = []
        
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            
            # Build URL with work item IDs
            ids_param = ",".join(str(id) for id in batch_ids)
            url = f"https://dev.azure.com/{organization}/_apis/wit/workitems"
            params = {
                "ids": ids_param,
                "api-version": self.API_VERSION,
                "$expand": "all"
            }
            
            response = await self._make_request("GET", url, params=params)
            data = response.json()
            
            # Parse work items
            for item_data in data.get("value", []):
                work_item = self._parse_work_item(item_data)
                all_work_items.append(work_item)
        
        return all_work_items
    
    async def _execute_wiql_query(
        self,
        organization: str,
        project: str,
        query: str
    ) -> List[int]:
        """
        Execute WIQL query and return work item IDs.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            query: WIQL query string
            
        Returns:
            List of work item IDs
            
        Raises:
            AzureDevOpsError: If API call fails
        """
        url = f"https://dev.azure.com/{organization}/{project}/_apis/wit/wiql"
        params = {"api-version": self.API_VERSION}
        payload = {"query": query}
        
        response = await self._make_request("POST", url, params=params, json=payload)
        data = response.json()
        
        # Extract work item IDs from response
        work_items = data.get("workItems", [])
        return [item["id"] for item in work_items]
    
    def _parse_work_item(self, data: Dict[str, Any]) -> WorkItem:
        """
        Parse work item from API response.
        
        Args:
            data: Work item data from API
            
        Returns:
            WorkItem object
        """
        fields = data.get("fields", {})
        
        # Parse tags (semicolon-separated string)
        tags_str = fields.get("System.Tags", "")
        tags = [tag.strip() for tag in tags_str.split(";") if tag.strip()]
        
        # Parse created date
        created_date_str = fields.get("System.CreatedDate")
        created_date = datetime.fromisoformat(created_date_str.replace("Z", "+00:00"))
        
        return WorkItem(
            id=data["id"],
            title=fields.get("System.Title", ""),
            state=fields.get("System.State", ""),
            work_item_type=fields.get("System.WorkItemType", ""),
            tags=tags,
            assigned_to=fields.get("System.AssignedTo", {}).get("displayName") if isinstance(fields.get("System.AssignedTo"), dict) else fields.get("System.AssignedTo"),
            created_date=created_date
        )
    
    async def get_commits_for_work_item(
        self,
        organization: str,
        project: str,
        work_item_id: int,
        repository_id: Optional[str] = None
    ) -> List[Commit]:
        """
        Fetch all commits associated with a work item.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            work_item_id: Work item ID
            repository_id: Optional repository ID (if None, searches all repos)
            
        Returns:
            List of Commit objects
            
        Raises:
            AzureDevOpsError: If API call fails
            
        Requirements: 5.1, 5.2, 5.5, 2.1.4
        """
        # Get work item links to find associated commits
        url = f"https://dev.azure.com/{organization}/_apis/wit/workitems/{work_item_id}"
        params = {
            "api-version": self.API_VERSION,
            "$expand": "relations"
        }
        
        response = await self._make_request("GET", url, params=params)
        data = response.json()
        
        # Extract commit links from relations
        relations = data.get("relations", [])
        commit_urls = []
        
        for relation in relations:
            rel_type = relation.get("rel", "")
            if "ArtifactLink" in rel_type:
                # Check if it's a commit link
                url_value = relation.get("url", "")
                if "vstfs:///Git/Commit/" in url_value:
                    commit_urls.append(url_value)
        
        if not commit_urls:
            # No commits found for this work item
            return []
        
        # Parse commit information from URLs and fetch details
        commits = []
        repo_name_cache: Dict[str, str] = {}  # repo_id -> repo_name
        for commit_url in commit_urls:
            try:
                # Extract commit ID from URL
                # Format: vstfs:///Git/Commit/{project}%2F{repo}%2F{commitId}
                parts = commit_url.split("/")
                if len(parts) >= 2:
                    commit_info = parts[-1]
                    # URL decode and split
                    import urllib.parse
                    decoded = urllib.parse.unquote(commit_info)
                    commit_parts = decoded.split("/")
                    
                    if len(commit_parts) >= 3:
                        repo_id = commit_parts[1]
                        commit_id = commit_parts[2]

                        # Resolve repo name (cached per request)
                        if repo_id not in repo_name_cache:
                            try:
                                repo_url = f"https://dev.azure.com/{organization}/{project}/_apis/git/repositories/{repo_id}"
                                repo_resp = await self._make_request("GET", repo_url, params={"api-version": self.API_VERSION})
                                repo_name_cache[repo_id] = repo_resp.json().get("name", repo_id)
                            except Exception:
                                repo_name_cache[repo_id] = repo_id

                        # Fetch commit details
                        commit = await self._get_commit_details(
                            organization, project, repo_id, commit_id, work_item_id,
                            repository_name=repo_name_cache[repo_id]
                        )
                        if commit:
                            commits.append(commit)
            except Exception:
                # Skip malformed URLs
                continue
        
        return commits
    
    async def _get_commit_details(
        self,
        organization: str,
        project: str,
        repository_id: str,
        commit_id: str,
        work_item_id: int,
        repository_name: str = ""
    ) -> Optional[Commit]:
        """
        Get detailed information about a commit.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            repository_id: Repository ID
            commit_id: Commit SHA hash
            work_item_id: Associated work item ID
            
        Returns:
            Commit object or None if not found
        """
        try:
            url = f"https://dev.azure.com/{organization}/{project}/_apis/git/repositories/{repository_id}/commits/{commit_id}"
            params = {
                "api-version": self.API_VERSION,
                "changeCount": 100  # Get up to 100 changed files
            }
            
            response = await self._make_request("GET", url, params=params)
            data = response.json()
            
            return self._parse_commit(data, work_item_id, repository_id, repository_name)
            
        except AzureDevOpsNotFoundError:
            # Commit not found
            return None
        except Exception:
            # Other errors - skip this commit
            return None
    
    def _parse_commit(self, data: Dict[str, Any], work_item_id: int, repository_id: str = "", repository_name: str = "") -> Commit:
        """
        Parse commit from API response.
        
        Args:
            data: Commit data from API
            work_item_id: Associated work item ID
            
        Returns:
            Commit object
        """
        # Parse commit date
        commit_date_str = data.get("author", {}).get("date", "")
        commit_date = datetime.fromisoformat(commit_date_str.replace("Z", "+00:00"))
        
        # Get author information
        author_info = data.get("author", {})
        author = author_info.get("name", "")
        author_email = author_info.get("email", "")
        
        # Get changed files
        changes = data.get("changes", [])
        changed_files = [change.get("item", {}).get("path", "") for change in changes]
        
        return Commit(
            commit_id=data.get("commitId", ""),
            author=author,
            author_email=author_email,
            commit_date=commit_date,
            message=data.get("comment", ""),
            work_item_ids=[work_item_id],
            changed_files=changed_files,
            repository_id=repository_id,
            repository_name=repository_name
        )

    # -------------------------------------------------------------------------
    # ADO Git operations — branch creation, cherry-pick, PR
    # -------------------------------------------------------------------------

    async def create_branch_in_repo(
        self,
        organization: str,
        project: str,
        repository_id: str,
        branch_name: str,
        from_commit_id: str,
        base_branch: str = "main"
    ) -> Dict[str, Any]:
        """
        Create a branch in an Azure DevOps Git repository.
        If from_commit_id is provided, the branch is created at that commit.
        Otherwise the HEAD of base_branch is used.
        """
        refs_url = (
            f"https://dev.azure.com/{organization}/{project}"
            f"/_apis/git/repositories/{repository_id}/refs"
        )

        if from_commit_id:
            object_id = from_commit_id
        else:
            # Resolve base branch HEAD
            resp = await self._make_request(
                "GET", refs_url,
                params={"api-version": self.API_VERSION, "filter": f"heads/{base_branch}"}
            )
            refs = resp.json().get("value", [])
            if not refs:
                raise AzureDevOpsNotFoundError(
                    f"Base branch '{base_branch}' not found in repo '{repository_id}'"
                )
            object_id = refs[0]["objectId"]

        new_ref_name = f"refs/heads/{branch_name}"
        body = [{"name": new_ref_name, "newObjectId": object_id, "oldObjectId": "0" * 40}]
        resp = await self._make_request(
            "POST", refs_url,
            params={"api-version": self.API_VERSION},
            json=body
        )
        result_refs = resp.json().get("value", [])
        if result_refs and not result_refs[0].get("success", True):
            ref_result = result_refs[0]
            update_status = ref_result.get("updateStatus", "")
            custom = ref_result.get("customMessage") or ""

            # Branch already exists — update it to point at the new commit
            if update_status == "nameConflict":
                # Look up current objectId so we can send the correct oldObjectId
                existing = await self._make_request(
                    "GET", refs_url,
                    params={"api-version": self.API_VERSION, "filter": f"heads/{branch_name}"}
                )
                existing_refs = existing.json().get("value", [])
                old_object_id = existing_refs[0]["objectId"] if existing_refs else "0" * 40
                update_body = [{"name": new_ref_name, "newObjectId": object_id, "oldObjectId": old_object_id}]
                await self._make_request(
                    "POST", refs_url,
                    params={"api-version": self.API_VERSION},
                    json=update_body
                )
            else:
                raise AzureDevOpsError(
                    f"Failed to create branch '{branch_name}': {update_status or 'unknown'}"
                    + (f" — {custom}" if custom else "")
                )
        return {"success": True, "branch_name": branch_name, "object_id": object_id}

    async def create_pull_request(
        self,
        organization: str,
        project: str,
        repository_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = ""
    ) -> Dict[str, Any]:
        """
        Create a pull request in an Azure DevOps Git repository.
        Returns the PR data dict from the API.
        """
        url = (
            f"https://dev.azure.com/{organization}/{project}"
            f"/_apis/git/repositories/{repository_id}/pullrequests"
        )
        body = {
            "title": title,
            "description": description,
            "sourceRefName": f"refs/heads/{source_branch}",
            "targetRefName": f"refs/heads/{target_branch}",
        }
        resp = await self._make_request(
            "POST", url,
            params={"api-version": self.API_VERSION},
            json=body
        )
        data = resp.json()
        return {
            "pull_request_id": data.get("pullRequestId"),
            "url": (
                f"https://dev.azure.com/{organization}/{project}"
                f"/_git/{repository_id}/pullrequest/{data.get('pullRequestId')}"
            ),
            "status": data.get("status")
        }
