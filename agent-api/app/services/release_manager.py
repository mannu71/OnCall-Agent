"""
Release Manager Service

This module orchestrates the release creation workflow, integrating Azure DevOps,
Azure Wiki, Git operations, and configuration management.

Requirements: 12.4, 10.5
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict

from app.models.azure_devops import (
    Release,
    ReleaseResult,
    ValidationResult,
    ValidationWarning,
    WorkItem,
    Commit,
    WikiDocumentationResult
)
from app.services.azure_devops_client import (
    AzureDevOpsClient,
    AzureDevOpsError,
    AzureDevOpsConnectionError,
    AzureDevOpsAuthenticationError
)
from app.services.azure_wiki_client import (
    AzureWikiClient,
    AzureWikiError
)
from app.services.azure_config_manager import ConfigManager


# Configure logging
logger = logging.getLogger(__name__)


# Custom Exceptions

class ReleaseManagerError(Exception):
    """Base exception for Release Manager errors"""
    pass


class ReleaseValidationError(ReleaseManagerError):
    """Validation failed"""
    pass


class ReleaseCreationError(ReleaseManagerError):
    """Release creation failed"""
    pass


class ReleaseCredentialsError(ReleaseManagerError):
    """Credentials not found or invalid"""
    pass


# Release Manager Service

class ReleaseManager:
    """
    Orchestrates the release creation workflow.

    Integrates:
    - Azure DevOps client for work items, commits, and Git operations
    - Azure Wiki client for documentation
    - Config manager for credential lookup
    """

    def __init__(
        self,
        config_manager: ConfigManager,
        default_user: str = "system"
    ):
        self.config_manager = config_manager
        self.default_user = default_user
        self.logger = logging.getLogger(f"{__name__}.ReleaseManager")
        self.logger.setLevel(logging.INFO)
        self.logger.info("ReleaseManager initialized")
    
    def _get_pat(self) -> str:
        """Get the stored PAT, raising ReleaseCredentialsError if missing."""
        pat = self.config_manager.get_pat()
        if not pat:
            raise ReleaseCredentialsError(
                "No Azure DevOps PAT configured. Please configure credentials in settings."
            )
        return pat

    def _get_azure_clients(self, organization: str) -> tuple:
        """Return (AzureDevOpsClient, AzureWikiClient) using the stored PAT."""
        pat = self._get_pat()
        return AzureDevOpsClient(pat=pat), AzureWikiClient(pat=pat)
    
    async def validate_work_items(
        self,
        organization: str,
        project: str,
        work_item_ids: List[int]
    ) -> ValidationResult:
        """
        Validate that all work items are in Closed state.
        
        Checks each work item's state and generates warnings for non-closed items.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            work_item_ids: List of work item IDs to validate
            
        Returns:
            ValidationResult with warnings for non-closed items
            
        Raises:
            ReleaseCredentialsError: If credentials not found
            AzureDevOpsError: If API call fails
            
        Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 2.1.4
        """
        self.logger.info(
            f"Validating {len(work_item_ids)} work items for "
            f"{organization}/{project}"
        )
        
        # Get Azure clients
        ado_client, _ = self._get_azure_clients(organization)
        
        try:
            # Fetch work items
            work_items = await ado_client.get_work_items_by_ids(
                organization, project, work_item_ids
            )
            
            # Check each work item state
            warnings = []
            for work_item in work_items:
                if work_item.state != "Closed":
                    warning = ValidationWarning(
                        work_item_id=work_item.id,
                        work_item_title=work_item.title,
                        current_state=work_item.state,
                        message=f"Work item {work_item.id} is in '{work_item.state}' state, not 'Closed'"
                    )
                    warnings.append(warning)
                    self.logger.warning(
                        f"Work item {work_item.id} is not closed: {work_item.state}"
                    )
            
            # Determine if validation passed
            valid = len(warnings) == 0
            
            if valid:
                self.logger.info("All work items are in Closed state")
            else:
                self.logger.warning(
                    f"Validation completed with {len(warnings)} warnings"
                )
            
            return ValidationResult(valid=valid, warnings=warnings)
            
        finally:
            # Clean up clients
            await ado_client.close()
    
    async def create_release(
        self,
        name: str,
        organization: str,
        project: str,
        work_item_ids: List[int],
        commit_ids: List[str],
        commits: Optional[List[Commit]] = None,
        base_branch: str = "main",
        created_by: Optional[str] = None,
        wiki_id: str = ""
    ) -> ReleaseResult:
        """
        Create a release branch per repository using ADO REST API cherry-pick,
        then open a PR for each repository, and create wiki documentation.
        """
        release_id = str(uuid.uuid4())
        # ADO branch names cannot contain spaces — replace with hyphens
        branch_name = name.strip().replace(" ", "-")
        user = created_by or self.default_user

        self.logger.info(
            f"Creating release '{name}' (ID: {release_id}) for "
            f"{organization}/{project} by {user}"
        )

        ado_client, wiki_client = self._get_azure_clients(organization)

        try:
            # Step 1: Validate work items
            self.logger.info("Step 1: Validating work items")
            validation_result = await self.validate_work_items(
                organization, project, work_item_ids
            )
            if not validation_result.valid:
                self.logger.warning(
                    f"Work item validation produced {len(validation_result.warnings)} warnings"
                )

            # Step 2: Group commits by repository_id
            # commits list (with repository_id) is preferred; fall back to commit_ids only
            commit_id_set = set(commit_ids)
            repo_commit_map: Dict[str, List[str]] = {}
            if commits:
                for c in commits:
                    if c.commit_id in commit_ids:
                        repo_id = c.repository_id or "unknown"
                        repo_commit_map.setdefault(repo_id, []).append(c.commit_id)
            else:
                # No repo info — cannot proceed with ADO cherry-pick
                return ReleaseResult(
                    success=False,
                    release_id=None,
                    branch_name=None,
                    conflicts=None,
                    error_message=(
                        "Commit details (with repository_id) are required to create "
                        "a release branch. Please re-fetch commits."
                    ),
                    wiki_urls=None
                )

            self.logger.info(
                f"Commits span {len(repo_commit_map)} repositories: {list(repo_commit_map.keys())}"
            )

            # Step 3: For each repo — create branch from latest selected commit + open PR
            pr_urls: Dict[str, str] = {}
            branch_results: list[str] = []

            # Build a mapping of repo_id -> ordered list of Commit objects so we
            # can pick the latest commit SHA to create the branch from.
            repo_commit_objects: Dict[str, List[Commit]] = {}
            if commits:
                for c in commits:
                    if c.commit_id in commit_id_set:
                        repo_id = c.repository_id or "unknown"
                        repo_commit_objects.setdefault(repo_id, []).append(c)

            for repo_id, repo_commits in repo_commit_map.items():
                # Pick the latest commit in this repo to use as the branch tip
                objects_for_repo = repo_commit_objects.get(repo_id, [])
                if objects_for_repo:
                    # Sort by commit_date descending and take the most recent
                    sorted_commits = sorted(
                        objects_for_repo,
                        key=lambda c: c.commit_date or "",
                        reverse=True
                    )
                    from_commit_id = sorted_commits[0].commit_id
                else:
                    from_commit_id = repo_commits[0]  # fallback: use first ID

                self.logger.info(
                    f"Step 3 [{repo_id}]: Creating branch '{branch_name}' "
                    f"from commit '{from_commit_id}'"
                )
                await ado_client.create_branch_in_repo(
                    organization, project, repo_id, branch_name,
                    from_commit_id=from_commit_id, base_branch=base_branch
                )
                branch_results.append(branch_name)

                self.logger.info(
                    f"Step 3 [{repo_id}]: Opening PR '{branch_name}' → '{base_branch}'"
                )
                pr_data = await ado_client.create_pull_request(
                    organization, project, repo_id,
                    source_branch=branch_name,
                    target_branch=base_branch,
                    title=f"Release: {name}",
                    description=(
                        f"Automated release branch created by KYC Protect oncall agent.\n\n"
                        f"Work items: {work_item_ids}\n"
                        f"Commits included: {repo_commits}"
                    )
                )
                pr_urls[repo_id] = pr_data.get("url", "")
                self.logger.info(f"PR created: {pr_data.get('url')}")

            # Step 4: Create wiki documentation
            self.logger.info("Step 4: Creating wiki documentation")
            wiki_result = await self.create_wiki_documentation(
                organization=organization,
                project=project,
                release_name=name,
                branch_name=branch_name,
                created_by=user,
                work_item_ids=work_item_ids,
                commit_ids=commit_ids,
                wiki_id=wiki_id
            )

            wiki_urls = None
            if wiki_result.success:
                wiki_urls = {
                    "main_page": wiki_result.main_page_url,
                    "commits_page": wiki_result.commits_page_url,
                    **{f"pr_{k}": v for k, v in pr_urls.items()}
                }
                self.logger.info("Wiki documentation created successfully")
            else:
                self.logger.warning(
                    f"Wiki documentation creation failed: {wiki_result.error_message}"
                )
                wiki_urls = {f"pr_{k}": v for k, v in pr_urls.items()} if pr_urls else None

            self.logger.info(f"Release '{name}' created successfully")
            return ReleaseResult(
                success=True,
                release_id=release_id,
                branch_name=branch_name,
                conflicts=None,
                error_message=None,
                wiki_urls=wiki_urls
            )

        except AzureDevOpsAuthenticationError as e:
            self.logger.error(f"Authentication error: {str(e)}")
            return ReleaseResult(
                success=False, release_id=None, branch_name=None,
                conflicts=None, error_message=f"Authentication error: {str(e)}", wiki_urls=None
            )
        except AzureDevOpsConnectionError as e:
            self.logger.error(f"Connection error: {str(e)}")
            return ReleaseResult(
                success=False, release_id=None, branch_name=None,
                conflicts=None, error_message=f"Connection error: {str(e)}", wiki_urls=None
            )
        except AzureDevOpsError as e:
            self.logger.error(f"Azure DevOps error: {str(e)}")
            return ReleaseResult(
                success=False, release_id=None, branch_name=None,
                conflicts=None, error_message=f"Azure DevOps error: {str(e)}", wiki_urls=None
            )
        except Exception as e:
            self.logger.exception(f"Unexpected error during release creation: {str(e)}")
            return ReleaseResult(
                success=False, release_id=None, branch_name=None,
                conflicts=None, error_message=f"Unexpected error: {str(e)}", wiki_urls=None
            )
        finally:
            await ado_client.close()
            await wiki_client.close()
    
    async def create_wiki_documentation(
        self,
        organization: str,
        project: str,
        release_name: str,
        branch_name: str,
        created_by: str,
        work_item_ids: List[int],
        commit_ids: List[str],
        wiki_id: str = ""
    ) -> WikiDocumentationResult:
        """
        Create wiki documentation for a release.
        
        Generates main wiki page with PBI table and commits sub-page with
        developer approval section.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            release_name: Name of the release
            branch_name: Git branch name
            created_by: User who created the release
            work_item_ids: List of work item IDs
            commit_ids: List of commit IDs
            
        Returns:
            WikiDocumentationResult with URLs to created pages
            
        Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9
        """
        self.logger.info(
            f"Creating wiki documentation for release '{release_name}' "
            f"in {organization}/{project}"
        )
        
        # Get Azure clients
        ado_client, wiki_client = self._get_azure_clients(organization)
        
        try:
            # Fetch work items
            work_items = await ado_client.get_work_items_by_ids(
                organization, project, work_item_ids
            )
            
            # Fetch commits
            commits = []
            for work_item_id in work_item_ids:
                wi_commits = await ado_client.get_commits_for_work_item(
                    organization, project, work_item_id
                )
                # Filter to only include commits in commit_ids
                for commit in wi_commits:
                    if commit.commit_id in commit_ids:
                        commits.append(commit)
            
            # Create wiki documentation
            result = await wiki_client.create_release_documentation(
                organization=organization,
                project=project,
                release_name=release_name,
                branch_name=branch_name,
                created_by=created_by,
                work_items=work_items,
                commits=commits,
                wiki_id=wiki_id
            )
            
            if result.success:
                self.logger.info(
                    f"Wiki documentation created: "
                    f"Main page: {result.main_page_url}, "
                    f"Commits page: {result.commits_page_url}"
                )
            else:
                self.logger.error(
                    f"Failed to create wiki documentation: {result.error_message}"
                )
            
            return result
            
        except Exception as e:
            self.logger.exception(f"Error creating wiki documentation: {str(e)}")
            return WikiDocumentationResult(
                success=False,
                main_page_url=None,
                commits_page_url=None,
                error_message=str(e)
            )
        finally:
            # Clean up clients
            await ado_client.close()
            await wiki_client.close()
    
    async def get_release_history(
        self,
        organization: str,
        project: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[dict]:
        """
        Retrieve release history from Azure Wiki.
        
        Fetches release pages from wiki and returns release information.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            limit: Maximum number of releases to return (default: 50)
            offset: Number of releases to skip (default: 0)
            
        Returns:
            List of release information dictionaries
            
        Raises:
            ReleaseCredentialsError: If credentials not found
            
        Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6
        """
        self.logger.info(
            f"Fetching release history for {organization}/{project} "
            f"(limit: {limit}, offset: {offset})"
        )
        
        # Get Azure clients
        _, wiki_client = self._get_azure_clients(organization)
        
        try:
            releases = await wiki_client.get_release_list(
                organization, project, limit, offset
            )
            
            self.logger.info(f"Retrieved {len(releases)} releases")
            
            return releases
            
        except AzureWikiError as e:
            self.logger.error(f"Error fetching release history: {str(e)}")
            return []
        finally:
            # Clean up client
            await wiki_client.close()
    
    async def get_release_details(
        self,
        organization: str,
        project: str,
        release_name: str
    ) -> Optional[dict]:
        """
        Get detailed information about a specific release from Azure Wiki.
        
        Parses wiki page content to extract release information.
        
        Args:
            organization: Azure DevOps organization name
            project: Project name
            release_name: Name of the release
            
        Returns:
            Dictionary with release details or None if not found
            
        Raises:
            ReleaseCredentialsError: If credentials not found
            
        Requirements: 11.1, 11.2, 11.3, 11.4, 11.6
        """
        self.logger.info(
            f"Fetching release details for '{release_name}' "
            f"in {organization}/{project}"
        )
        
        # Get Azure clients
        _, wiki_client = self._get_azure_clients(organization)
        
        try:
            release_details = await wiki_client.get_release_details(
                organization, project, release_name
            )
            
            if release_details:
                self.logger.info(f"Retrieved details for release '{release_name}'")
            else:
                self.logger.warning(f"Release '{release_name}' not found")
            
            return release_details
            
        except AzureWikiError as e:
            self.logger.error(f"Error fetching release details: {str(e)}")
            return None
        finally:
            # Clean up client
            await wiki_client.close()
