"""
Azure DevOps Data Models

This module defines Pydantic models for Azure DevOps entities including
work items, commits, and connection results.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 5.1, 5.2, 10.1
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class WorkItem(BaseModel):
    """
    Model for Azure DevOps work item.
    
    Represents a work item (PBI, Bug, Task, etc.) from Azure DevOps.
    
    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
    """
    id: int = Field(..., description="Work item ID")
    title: str = Field(..., description="Work item title")
    state: str = Field(..., description="Work item state (New, Active, Resolved, Closed, etc.)")
    work_item_type: str = Field(..., description="Work item type (PBI, Bug, Task, etc.)")
    tags: List[str] = Field(default_factory=list, description="List of tags")
    assigned_to: Optional[str] = Field(None, description="Assigned user display name")
    created_date: datetime = Field(..., description="Creation date")


class Commit(BaseModel):
    """
    Model for Git commit.
    
    Represents a Git commit with associated work items and changed files.
    
    Requirements: 5.1, 5.2
    """
    commit_id: str = Field(..., description="Commit SHA hash")
    author: str = Field(..., description="Commit author name")
    author_email: str = Field(..., description="Commit author email")
    commit_date: datetime = Field(..., description="Commit date")
    message: str = Field(..., description="Commit message")
    work_item_ids: List[int] = Field(default_factory=list, description="Associated work item IDs")
    changed_files: List[str] = Field(default_factory=list, description="List of changed file paths")
    repository_id: str = Field(default="", description="Repository ID the commit belongs to")
    repository_name: str = Field(default="", description="Repository name the commit belongs to")


class ConnectionResult(BaseModel):
    """
    Result of connection validation.
    
    Indicates whether connection to Azure DevOps was successful.
    
    Requirements: 10.1
    """
    success: bool = Field(..., description="Whether connection was successful")
    message: str = Field(..., description="Status message")
    organization: str = Field(..., description="Azure DevOps organization name")
    project: str = Field(..., description="Project name")


class WikiInfo(BaseModel):
    """
    Model for Azure Wiki information.
    
    Represents a wiki in Azure DevOps.
    
    Requirements: 13.8
    """
    id: str = Field(..., description="Wiki ID")
    name: str = Field(..., description="Wiki name")
    project_id: str = Field(..., description="Project ID")
    repository_id: str = Field(..., description="Repository ID")


class WikiPageResult(BaseModel):
    """
    Result of wiki page creation or update.
    
    Requirements: 13.1, 13.5, 13.6
    """
    success: bool = Field(..., description="Whether operation was successful")
    page_id: Optional[str] = Field(None, description="Wiki page ID")

    @field_validator("page_id", mode="before")
    @classmethod
    def coerce_page_id(cls, v):
        return str(v) if v is not None else None
    page_path: Optional[str] = Field(None, description="Wiki page path")
    url: Optional[str] = Field(None, description="URL to wiki page")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class WikiDocumentationResult(BaseModel):
    """
    Result of release documentation creation in wiki.
    
    Requirements: 13.1, 13.7
    """
    success: bool = Field(..., description="Whether operation was successful")
    main_page_url: Optional[str] = Field(None, description="URL to main release page")
    commits_page_url: Optional[str] = Field(None, description="URL to commits page")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class Release(BaseModel):
    """
    Model for a release.
    
    Represents a release with associated work items and commits.
    
    Requirements: 7.3, 11.1, 11.2
    """
    id: str = Field(..., description="Release UUID")
    name: str = Field(..., description="Release name")
    branch_name: str = Field(..., description="Git branch name")
    organization: str = Field(..., description="Azure DevOps organization")
    project: str = Field(..., description="Azure DevOps project")
    created_at: datetime = Field(..., description="Creation timestamp")
    created_by: str = Field(..., description="User who created the release")
    work_item_ids: List[int] = Field(default_factory=list, description="Work item IDs in release")
    commit_ids: List[str] = Field(default_factory=list, description="Commit IDs in release")
    status: str = Field(..., description="Release status (created, failed, conflict_resolved)")
    wiki_main_page_url: Optional[str] = Field(None, description="URL to main wiki page")
    wiki_commits_page_url: Optional[str] = Field(None, description="URL to commits wiki page")


class ReleaseResult(BaseModel):
    """
    Result of release creation.
    
    Requirements: 7.3, 7.4, 8.2, 12.1, 12.2
    """
    success: bool = Field(..., description="Whether release was created successfully")
    release_id: Optional[str] = Field(None, description="Release UUID")
    branch_name: Optional[str] = Field(None, description="Created branch name")
    conflicts: Optional[List[dict]] = Field(None, description="List of merge conflicts if any")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    wiki_urls: Optional[dict] = Field(None, description="URLs to created wiki pages")


class ValidationResult(BaseModel):
    """
    Result of work item validation.
    
    Requirements: 4.1, 4.2, 4.3
    """
    valid: bool = Field(..., description="Whether all work items are valid")
    warnings: List['ValidationWarning'] = Field(default_factory=list, description="Validation warnings")


class ValidationWarning(BaseModel):
    """
    Warning for a work item validation issue.
    
    Requirements: 4.1, 4.2, 4.3
    """
    work_item_id: int = Field(..., description="Work item ID")
    work_item_title: str = Field(..., description="Work item title")
    current_state: str = Field(..., description="Current work item state")
    message: str = Field(..., description="Warning message")
