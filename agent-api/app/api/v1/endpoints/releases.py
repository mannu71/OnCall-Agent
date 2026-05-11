"""
Release Management API Endpoints

This module provides RESTful endpoints for Azure Release Management functionality.

Endpoints:
- POST /api/releases/work-items/search - Search for work items by tags or IDs
- GET /api/releases/work-items/{id}/commits - Get commits for a work item
- POST /api/releases - Create a new release
- POST /api/releases/{id}/conflicts/resolve - Resolve merge conflicts
- GET /api/releases - Get release history
- GET /api/releases/{organization}/{project}/{name} - Get release details
- POST /api/settings/azure-devops - Save Azure DevOps credentials
- POST /api/settings/azure-devops/test - Test Azure DevOps connection
- GET /api/settings/azure-devops - List configured organizations

Requirements: 10.6
"""

import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query, Path as PathParam, Body
from pydantic import BaseModel, Field

from app.models.azure_devops import (
    WorkItem,
    Commit,
    ConnectionResult,
    ReleaseResult,
    ValidationWarning
)
from app.services.azure_config_manager import ConfigManager
from app.services.azure_devops_client import (
    AzureDevOpsClient,
    AzureDevOpsError,
    AzureDevOpsAuthenticationError,
    AzureDevOpsConnectionError,
    AzureDevOpsNotFoundError
)
from app.services.azure_wiki_client import AzureWikiClient
from app.services.release_manager import (
    ReleaseManager,
    ReleaseCredentialsError,
    ReleaseManagerError
)


# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/releases", tags=["releases"])


# Request/Response Models

class SearchWorkItemsRequest(BaseModel):
    """Request model for work item search."""
    organization: str = Field(..., min_length=1, description="Azure DevOps organization name")
    project: str = Field(..., min_length=1, description="Project name")
    tags: Optional[List[str]] = Field(None, description="List of tags to filter by")
    pbi_numbers: Optional[List[int]] = Field(None, description="List of PBI numbers to filter by")


class SearchWorkItemsResponse(BaseModel):
    """Response model for work item search."""
    work_items: List[WorkItem] = Field(..., description="List of work items")
    total_count: int = Field(..., description="Total number of work items found")
    validation_warnings: List[ValidationWarning] = Field(
        default_factory=list,
        description="Validation warnings for non-closed work items"
    )


class GetCommitsResponse(BaseModel):
    """Response model for commit retrieval."""
    commits: List[Commit] = Field(..., description="List of commits")
    work_item_id: int = Field(..., description="Work item ID")


class CreateReleaseRequest(BaseModel):
    """Request model for release creation."""
    name: str = Field(..., min_length=1, max_length=255, description="Release name")
    organization: str = Field(..., min_length=1, description="Azure DevOps organization name")
    project: str = Field(..., min_length=1, description="Project name")
    work_item_ids: List[int] = Field(..., min_items=1, description="List of work item IDs")
    commit_ids: List[str] = Field(..., min_items=1, description="List of commit IDs")
    base_branch: str = Field(default="main", description="Base branch to create release from")
    created_by: Optional[str] = Field(None, description="User creating the release")
    wiki_id: str = Field(..., min_length=1, description="Wiki ID to publish release pages under")


class CreateReleaseResponse(BaseModel):
    """Response model for release creation."""
    success: bool = Field(..., description="Whether release was created successfully")
    release_id: Optional[str] = Field(None, description="Release UUID")
    branch_name: Optional[str] = Field(None, description="Created branch name")
    conflicts: Optional[List[Dict[str, Any]]] = Field(None, description="List of merge conflicts if any")
    message: str = Field(..., description="Status message")
    wiki_urls: Optional[Dict[str, str]] = Field(None, description="URLs to created wiki pages")


class ResolveConflictRequest(BaseModel):
    """Request model for conflict resolution."""
    file_path: str = Field(..., description="Path to the conflicting file")
    resolution_type: str = Field(..., description="Resolution type: 'ours', 'theirs', or 'manual'")
    content: Optional[str] = Field(None, description="Custom content for manual resolution")


class ResolveConflictResponse(BaseModel):
    """Response model for conflict resolution."""
    success: bool = Field(..., description="Whether conflict was resolved successfully")
    file_path: str = Field(..., description="Path to the resolved file")
    message: str = Field(..., description="Status message")


class ReleaseHistoryResponse(BaseModel):
    """Response model for release history."""
    releases: List[Dict[str, Any]] = Field(..., description="List of releases")
    total_count: int = Field(..., description="Total number of releases")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")


class SaveAzureDevOpsConfigRequest(BaseModel):
    """Request model for saving Azure DevOps configuration."""
    pat: str = Field(..., min_length=1, description="Personal Access Token")


class TestAzureDevOpsConnectionRequest(BaseModel):
    """Request model for testing Azure DevOps connection."""
    organization: str = Field(..., min_length=1, description="Azure DevOps organization name")
    project: str = Field(..., min_length=1, description="Project name")


class AzureDevOpsConfigResponse(BaseModel):
    """Response model for Azure DevOps configuration operations."""
    success: bool = Field(..., description="Whether operation was successful")
    message: str = Field(..., description="Status message")


class ListOrganizationsResponse(BaseModel):
    """Response model for listing configured organizations."""
    organizations: List[str] = Field(..., description="List of configured organization names")


# Dependency injection helpers

def get_config_manager() -> ConfigManager:
    """Get ConfigManager instance."""
    import os
    config_path = os.environ.get(
        "AZURE_CONFIG_PATH",
        os.path.join(os.path.dirname(__file__), "../../../../config/azure_devops_credentials.json")
    )
    return ConfigManager(config_path=config_path)


def get_release_manager() -> ReleaseManager:
    """Get ReleaseManager instance."""
    config_manager = get_config_manager()
    return ReleaseManager(
        config_manager=config_manager,
        default_user="api_user"
    )


# API Endpoints

# Sub-task 8.2: Work item endpoints

@router.post("/work-items/search", response_model=SearchWorkItemsResponse)
async def search_work_items(request: SearchWorkItemsRequest) -> SearchWorkItemsResponse:
    """
    Search for work items by tags or PBI numbers.
    
    Retrieves work items from Azure DevOps based on the provided criteria
    and validates their states.
    
    Args:
        request: Search criteria including organization, project, tags, or PBI numbers
        
    Returns:
        SearchWorkItemsResponse with work items and validation warnings
        
    Raises:
        HTTPException: If credentials not found or API call fails
        
    Requirements: 3.1, 3.2, 3.3, 3.4, 4.3, 2.1.1, 2.1.2, 2.1.3, 2.1.4
    """
    logger.info(
        f"Searching work items for {request.organization}/{request.project} "
        f"with tags={request.tags}, pbi_numbers={request.pbi_numbers}"
    )
    
    # Validate that at least one search criteria is provided
    if not request.tags and not request.pbi_numbers:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'tags' or 'pbi_numbers' must be provided"
        )
    
# Get credentials for work item search
    config_manager = get_config_manager()
    pat = config_manager.get_pat()

    if not pat:
        raise HTTPException(
            status_code=401,
            detail="No Azure DevOps PAT configured. Please configure credentials in settings."
        )

    # Create Azure DevOps client
    ado_client = AzureDevOpsClient(pat=pat)
    
    try:
        # Fetch work items based on criteria
        work_items = []
        
        if request.tags:
            logger.info(f"Fetching work items by tags: {request.tags}")
            work_items = await ado_client.get_work_items_by_tags(
                organization=request.organization,
                project=request.project,
                tags=request.tags
            )
        elif request.pbi_numbers:
            logger.info(f"Fetching work items by IDs: {request.pbi_numbers}")
            work_items = await ado_client.get_work_items_by_ids(
                organization=request.organization,
                project=request.project,
                ids=request.pbi_numbers
            )
        
        # Validate work item states
        validation_warnings = []
        for work_item in work_items:
            if work_item.state != "Closed":
                warning = ValidationWarning(
                    work_item_id=work_item.id,
                    work_item_title=work_item.title,
                    current_state=work_item.state,
                    message=f"Work item {work_item.id} is in '{work_item.state}' state, not 'Closed'"
                )
                validation_warnings.append(warning)
        
        logger.info(
            f"Found {len(work_items)} work items with {len(validation_warnings)} warnings"
        )
        
        return SearchWorkItemsResponse(
            work_items=work_items,
            total_count=len(work_items),
            validation_warnings=validation_warnings
        )
        
    except AzureDevOpsAuthenticationError as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {str(e)}"
        )
    except AzureDevOpsConnectionError as e:
        logger.error(f"Connection error: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to Azure DevOps: {str(e)}"
        )
    except AzureDevOpsNotFoundError as e:
        logger.error(f"Not found error: {str(e)}")
        raise HTTPException(
            status_code=404,
            detail=f"Resource not found: {str(e)}"
        )
    except AzureDevOpsError as e:
        logger.error(f"Azure DevOps error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Azure DevOps error: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
    finally:
        await ado_client.close()


# Sub-task 8.3: Commit endpoints

@router.get("/work-items/{work_item_id}/commits", response_model=GetCommitsResponse)
async def get_work_item_commits(
    work_item_id: int = PathParam(..., description="Work item ID"),
    organization: str = Query(..., description="Azure DevOps organization name"),
    project: str = Query(..., description="Project name")
) -> GetCommitsResponse:
    """
    Get commits associated with a work item.
    
    Retrieves all commits linked to the specified work item from Azure DevOps.
    
    Args:
        work_item_id: Work item ID
        organization: Azure DevOps organization name
        project: Project name
        
    Returns:
        GetCommitsResponse with list of commits
        
    Raises:
        HTTPException: If credentials not found or API call fails
        
    Requirements: 5.1, 5.2, 5.3, 5.4, 2.1.4
    """
    logger.info(
        f"Fetching commits for work item {work_item_id} "
        f"in {organization}/{project}"
    )
    
# Get credentials for commits lookup
    config_manager = get_config_manager()
    pat = config_manager.get_pat()

    if not pat:
        raise HTTPException(
            status_code=401,
            detail="No Azure DevOps PAT configured. Please configure credentials in settings."
        )

    # Create Azure DevOps client
    ado_client = AzureDevOpsClient(pat=pat)
    
    try:
        # Fetch commits for work item
        commits = await ado_client.get_commits_for_work_item(
            organization=organization,
            project=project,
            work_item_id=work_item_id
        )
        
        logger.info(f"Found {len(commits)} commits for work item {work_item_id}")
        
        return GetCommitsResponse(
            commits=commits,
            work_item_id=work_item_id
        )
        
    except AzureDevOpsAuthenticationError as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {str(e)}"
        )
    except AzureDevOpsConnectionError as e:
        logger.error(f"Connection error: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to connect to Azure DevOps: {str(e)}"
        )
    except AzureDevOpsNotFoundError as e:
        logger.error(f"Not found error: {str(e)}")
        raise HTTPException(
            status_code=404,
            detail=f"Work item {work_item_id} not found: {str(e)}"
        )
    except AzureDevOpsError as e:
        logger.error(f"Azure DevOps error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Azure DevOps error: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
    finally:
        await ado_client.close()


# Sub-task 8.4: Release creation endpoint

@router.post("", response_model=CreateReleaseResponse)
async def create_release(request: CreateReleaseRequest) -> CreateReleaseResponse:
    """
    Create a new release branch with selected commits.
    
    Orchestrates the complete release workflow:
    1. Validates work items
    2. Creates release branch
    3. Applies commits (cherry-pick)
    4. Handles conflicts if any
    5. Creates wiki documentation
    
    Args:
        request: Release creation request with name, organization, project, work items, and commits
        
    Returns:
        CreateReleaseResponse with success status, branch name, or conflicts
        
    Raises:
        HTTPException: If credentials not found or release creation fails
        
    Requirements: 7.3, 7.4, 7.6, 7.7, 8.2, 12.1, 12.2, 2.1.4, 2.1.5
    """
    logger.info(
        f"Creating release '{request.name}' for {request.organization}/{request.project} "
        f"with {len(request.work_item_ids)} work items and {len(request.commit_ids)} commits"
    )
    
    # Fetch commit details (with repository_id) so the release manager can
    # group commits by repo for ADO cherry-pick.
    config_manager = get_config_manager()
    pat = config_manager.get_pat()
    if not pat:
        raise HTTPException(status_code=401, detail="No Azure DevOps PAT configured.")

    ado_client = AzureDevOpsClient(pat=pat)
    all_commits: List[Commit] = []
    commit_id_set = set(request.commit_ids)
    try:
        for wi_id in request.work_item_ids:
            wi_commits = await ado_client.get_commits_for_work_item(
                organization=request.organization,
                project=request.project,
                work_item_id=wi_id
            )
            for c in wi_commits:
                if c.commit_id in commit_id_set:
                    all_commits.append(c)
    finally:
        await ado_client.close()

    release_manager = get_release_manager()

    try:
        # Create release
        result = await release_manager.create_release(
            name=request.name,
            organization=request.organization,
            project=request.project,
            work_item_ids=request.work_item_ids,
            commit_ids=request.commit_ids,
            commits=all_commits,
            base_branch=request.base_branch,
            created_by=request.created_by,
            wiki_id=request.wiki_id
        )
        
        # Convert result to response
        if result.success:
            logger.info(f"Release '{request.name}' created successfully")
            return CreateReleaseResponse(
                success=True,
                release_id=result.release_id,
                branch_name=result.branch_name,
                conflicts=None,
                message=f"Release '{request.name}' created successfully",
                wiki_urls=result.wiki_urls
            )
        else:
            # Check if conflicts occurred
            if result.conflicts:
                logger.warning(f"Release creation encountered conflicts")
                return CreateReleaseResponse(
                    success=False,
                    release_id=result.release_id,
                    branch_name=result.branch_name,
                    conflicts=result.conflicts,
                    message="Merge conflicts detected. Please resolve conflicts to continue.",
                    wiki_urls=None
                )
            else:
                # Other error
                logger.error(f"Release creation failed: {result.error_message}")
                return CreateReleaseResponse(
                    success=False,
                    release_id=None,
                    branch_name=None,
                    conflicts=None,
                    message=result.error_message or "Release creation failed",
                    wiki_urls=None
                )
        
    except ReleaseCredentialsError as e:
        logger.error(f"Credentials error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail=f"Credentials not found: {str(e)}"
        )
    except ReleaseManagerError as e:
        logger.error(f"Release manager error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Release creation failed: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )



# Sub-task 8.6: Release history endpoints

@router.get("", response_model=ReleaseHistoryResponse)
async def get_release_history(
    organization: str = Query(..., description="Azure DevOps organization name"),
    project: str = Query(..., description="Project name"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=100, description="Number of items per page")
) -> ReleaseHistoryResponse:
    """
    Get release history from Azure Wiki.
    
    Retrieves a paginated list of releases for the specified organization and project.
    
    Args:
        organization: Azure DevOps organization name
        project: Project name
        page: Page number (default: 1)
        page_size: Number of items per page (default: 50, max: 100)
        
    Returns:
        ReleaseHistoryResponse with list of releases
        
    Raises:
        HTTPException: If credentials not found or API call fails
        
    Requirements: 11.2, 11.3, 11.4, 11.6
    """
    logger.info(
        f"Fetching release history for {organization}/{project} "
        f"(page: {page}, page_size: {page_size})"
    )
    
    # Calculate offset
    offset = (page - 1) * page_size
    
    # Get release manager
    release_manager = get_release_manager()
    
    try:
        # Fetch release history
        releases = await release_manager.get_release_history(
            organization=organization,
            project=project,
            limit=page_size,
            offset=offset
        )
        
        logger.info(f"Retrieved {len(releases)} releases")
        
        return ReleaseHistoryResponse(
            releases=releases,
            total_count=len(releases),  # Note: This is the count for current page
            page=page,
            page_size=page_size
        )
        
    except ReleaseCredentialsError as e:
        logger.error(f"Credentials error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail=f"Credentials not found: {str(e)}"
        )
    except Exception as e:
        logger.exception(f"Error fetching release history: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release history: {str(e)}"
        )


@router.get("/{organization}/{project}/{release_name}")
async def get_release_details(
    organization: str = PathParam(..., description="Azure DevOps organization name"),
    project: str = PathParam(..., description="Project name"),
    release_name: str = PathParam(..., description="Release name")
) -> Dict[str, Any]:
    """
    Get detailed information about a specific release.
    
    Retrieves release details from Azure Wiki including work items and commits.
    
    Args:
        organization: Azure DevOps organization name
        project: Project name
        release_name: Release name
        
    Returns:
        Dictionary with release details
        
    Raises:
        HTTPException: If credentials not found, release not found, or API call fails
        
    Requirements: 11.2, 11.3, 11.4, 11.6
    """
    logger.info(
        f"Fetching release details for '{release_name}' "
        f"in {organization}/{project}"
    )
    
    # Get release manager
    release_manager = get_release_manager()
    
    try:
        # Fetch release details
        release_details = await release_manager.get_release_details(
            organization=organization,
            project=project,
            release_name=release_name
        )
        
        if not release_details:
            logger.warning(f"Release '{release_name}' not found")
            raise HTTPException(
                status_code=404,
                detail=f"Release '{release_name}' not found"
            )
        
        logger.info(f"Retrieved details for release '{release_name}'")
        
        return release_details
        
    except ReleaseCredentialsError as e:
        logger.error(f"Credentials error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail=f"Credentials not found: {str(e)}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.exception(f"Error fetching release details: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch release details: {str(e)}"
        )


# Sub-task 8.7: Azure DevOps settings endpoints

# Create a separate router for settings endpoints
settings_router = APIRouter(prefix="/settings/azure-devops", tags=["azure-devops-settings"])


# Wiki test endpoint
@router.post("/test-wiki")
async def test_wiki_creation(
    organization: str = Query(...),
    project: str = Query(...),
    wiki_id: str = Query(...),
    page_path: str = Query(default="/Releases/TestPage")
) -> Dict[str, Any]:
    """Test wiki page creation with current PAT."""
    config_manager = get_config_manager()
    pat = config_manager.get_pat()
    if not pat:
        raise HTTPException(status_code=401, detail="No PAT configured.")

    from app.services.azure_wiki_client import AzureWikiClient
    wiki_client = AzureWikiClient(pat=pat)
    try:
        # Auto-discover wiki ID if needed
        import re
        def _looks_like_wiki_id(wid: str) -> bool:
            guid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            return '.' in wid or bool(re.match(guid_pattern, wid, re.I))

        resolved_wiki_id = wiki_id
        if not _looks_like_wiki_id(wiki_id):
            resp = await wiki_client._make_request(
                "GET",
                f"https://dev.azure.com/{organization}/{project}/_apis/wiki/wikis",
                params={"api-version": "7.1"}
            )
            wikis = resp.json().get("value", [])
            if not wikis:
                raise HTTPException(status_code=404, detail="No wikis found in project.")
            wiki = wikis[0]
            # Prefer the wiki name (e.g. "Compliance.wiki") over the GUID id
            resolved_wiki_id = wiki.get("name") or wiki.get("id")
            logger.info(f"Wiki discovery — all wikis: {[w.get('name') for w in wikis]}, using: {resolved_wiki_id}")

        result = await wiki_client.create_wiki_page(
            organization=organization,
            project=project,
            wiki_id=resolved_wiki_id,
            path=page_path,
            content="## Test\n\nWiki creation test from KYC Protect oncall agent."
        )
        return {
            "success": result.success,
            "resolved_wiki_id": resolved_wiki_id,
            "url": result.url,
            "error": result.error_message
        }
    finally:
        await wiki_client.close()



@settings_router.post("", response_model=AzureDevOpsConfigResponse)
async def save_azure_devops_config(
    request: SaveAzureDevOpsConfigRequest
) -> AzureDevOpsConfigResponse:
    """
    Save Azure DevOps credentials for an organization.
    
    Encrypts and stores the Personal Access Token securely in the config file.
    
    Args:
        request: Configuration request with organization, PAT, and repository ID
        
    Returns:
        AzureDevOpsConfigResponse with success status
        
    Raises:
        HTTPException: If saving credentials fails
        
    Requirements: 10.2, 10.3, 10.4, 10.5, 10.6
    """
    logger.info("Saving Azure DevOps credentials")
    
    # Get config manager
    config_manager = get_config_manager()
    
    try:
        # Save PAT globally (no organization key required)
        config_manager.save_pat(request.pat)
        
        logger.info("Azure DevOps PAT saved successfully")
        
        return AzureDevOpsConfigResponse(
            success=True,
            message="PAT saved successfully"
        )
        
    except Exception as e:
        logger.exception(f"Error saving credentials: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save credentials: {str(e)}"
        )


@settings_router.post("/test", response_model=ConnectionResult)
async def test_azure_devops_connection(
    request: TestAzureDevOpsConnectionRequest
) -> ConnectionResult:
    """
    Test Azure DevOps connection for an organization and project.
    
    Validates that the stored credentials can successfully connect to Azure DevOps.
    
    Args:
        request: Test request with organization and project
        
    Returns:
        ConnectionResult with success status
        
    Raises:
        HTTPException: If credentials not found or connection test fails
        
    Requirements: 10.2, 10.4, 10.5, 10.6
    """
    logger.info(
        f"Testing Azure DevOps connection for {request.organization}/{request.project}"
    )
    
    # Get credentials
    config_manager = get_config_manager()
    pat = config_manager.get_pat()

    if not pat:
        raise HTTPException(
            status_code=401,
            detail="No Azure DevOps PAT configured. Please configure credentials in settings."
        )
    
    # Create Azure DevOps client
    ado_client = AzureDevOpsClient(pat=pat)
    
    try:
        # Test connection
        result = await ado_client.validate_connection(
            organization=request.organization,
            project=request.project
        )
        
        if result.success:
            logger.info(f"Connection test successful for {request.organization}/{request.project}")
        else:
            logger.warning(f"Connection test failed: {result.message}")
        
        return result
        
    except AzureDevOpsAuthenticationError as e:
        logger.error(f"Authentication error: {str(e)}")
        return ConnectionResult(
            success=False,
            message=f"Authentication failed: {str(e)}",
            organization=request.organization,
            project=request.project
        )
    except AzureDevOpsConnectionError as e:
        logger.error(f"Connection error: {str(e)}")
        return ConnectionResult(
            success=False,
            message=f"Connection failed: {str(e)}",
            organization=request.organization,
            project=request.project
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {str(e)}")
        return ConnectionResult(
            success=False,
            message=f"Unexpected error: {str(e)}",
            organization=request.organization,
            project=request.project
        )
    finally:
        await ado_client.close()


@settings_router.get("", response_model=ListOrganizationsResponse)
async def list_configured_organizations() -> ListOrganizationsResponse:
    """
    List all configured Azure DevOps organizations.
    
    Returns a list of organization names that have stored credentials.
    
    Returns:
        ListOrganizationsResponse with list of organization names
        
    Raises:
        HTTPException: If listing organizations fails
        
    Requirements: 10.4, 10.5
    """
    logger.info("Listing configured Azure DevOps organizations")
    
    # Get config manager
    config_manager = get_config_manager()
    
    try:
        # List organizations
        organizations = config_manager.list_organizations()
        
        logger.info(f"Found {len(organizations)} configured organizations")
        
        return ListOrganizationsResponse(
            organizations=organizations
        )
        
    except Exception as e:
        logger.exception(f"Error listing organizations: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list organizations: {str(e)}"
        )


@settings_router.delete("/{organization}")
async def delete_azure_devops_config(
    organization: str = PathParam(..., description="Azure DevOps organization name")
) -> AzureDevOpsConfigResponse:
    """
    Delete Azure DevOps credentials for an organization.
    
    Removes the stored credentials for the specified organization.
    
    Args:
        organization: Azure DevOps organization name
        
    Returns:
        AzureDevOpsConfigResponse with success status
        
    Raises:
        HTTPException: If deleting credentials fails
        
    Requirements: 10.4, 10.5
    """
    logger.info(f"Deleting Azure DevOps credentials for organization '{organization}'")
    
    # Get config manager
    config_manager = get_config_manager()
    
    try:
        # Delete credentials
        config_manager.delete_credentials(organization)
        
        logger.info(f"Credentials deleted successfully for organization '{organization}'")
        
        return AzureDevOpsConfigResponse(
            success=True,
            message=f"Credentials deleted successfully for organization '{organization}'"
        )
        
    except Exception as e:
        logger.exception(f"Error deleting credentials: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete credentials: {str(e)}"
        )


# Include settings router in main router
router.include_router(settings_router)


