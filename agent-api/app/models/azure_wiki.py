"""
Azure Wiki Data Models

This module defines Pydantic models for Azure Wiki entities including
wiki information, wiki pages, and documentation results.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 11.1, 11.2, 11.3, 11.4
"""

from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


class WikiInfo(BaseModel):
    """
    Model for Azure Wiki information.
    
    Represents basic information about a project wiki.
    
    Requirements: 13.8
    """
    id: str = Field(..., description="Wiki ID")
    name: str = Field(..., description="Wiki name")
    project_id: str = Field(..., description="Project ID")
    repository_id: str = Field(..., description="Repository ID")
    url: Optional[str] = Field(None, description="Wiki URL")


class WikiPageResult(BaseModel):
    """
    Result of wiki page creation or update.
    
    Indicates whether wiki page operation was successful.
    
    Requirements: 13.1, 13.5, 13.6
    """
    success: bool = Field(..., description="Whether operation was successful")
    page_id: Optional[str] = Field(None, description="Page ID")
    page_path: Optional[str] = Field(None, description="Page path")
    url: Optional[str] = Field(None, description="Page URL")
    version: Optional[str] = Field(None, description="Page version (ETag)")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class WikiDocumentationResult(BaseModel):
    """
    Result of release documentation creation.
    
    Contains URLs to created wiki pages for release documentation.
    
    Requirements: 13.1, 13.7
    """
    success: bool = Field(..., description="Whether documentation creation was successful")
    main_page_url: Optional[str] = Field(None, description="URL to main release page")
    commits_page_url: Optional[str] = Field(None, description="URL to commits page")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class ReleaseInfo(BaseModel):
    """
    Model for release information extracted from wiki.
    
    Represents a release entry in the release history.
    
    Requirements: 11.1, 11.2, 11.3
    """
    name: str = Field(..., description="Release name")
    branch_name: str = Field(..., description="Release branch name")
    organization: str = Field(..., description="Azure DevOps organization")
    project: str = Field(..., description="Azure DevOps project")
    created_date: datetime = Field(..., description="Release creation date")
    created_by: str = Field(..., description="Release creator")
    status: str = Field(..., description="Release status")
    pbi_count: int = Field(0, description="Number of PBIs in release")
    wiki_url: Optional[str] = Field(None, description="URL to wiki page")


class ReleaseDetails(BaseModel):
    """
    Detailed information about a release.
    
    Includes work items and commits from wiki page content.
    
    Requirements: 11.3, 11.4
    """
    name: str = Field(..., description="Release name")
    branch_name: str = Field(..., description="Release branch name")
    organization: str = Field(..., description="Azure DevOps organization")
    project: str = Field(..., description="Azure DevOps project")
    created_date: datetime = Field(..., description="Release creation date")
    created_by: str = Field(..., description="Release creator")
    status: str = Field(..., description="Release status")
    work_items: List[Dict[str, Any]] = Field(default_factory=list, description="Work items in release")
    commits: List[Dict[str, Any]] = Field(default_factory=list, description="Commits in release")
    main_page_url: Optional[str] = Field(None, description="URL to main wiki page")
    commits_page_url: Optional[str] = Field(None, description="URL to commits wiki page")
