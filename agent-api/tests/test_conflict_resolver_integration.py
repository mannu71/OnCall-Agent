"""
Integration tests for ConflictResolver with GitOperationsService

Tests the integration between ConflictResolver and GitOperationsService
to ensure conflict detection and resolution work together correctly.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

import tempfile
import shutil
from pathlib import Path

import pytest
from git import Repo

from app.services.git_operations_service import GitOperationsService
from app.services.conflict_resolver import ConflictResolver


@pytest.fixture
def temp_git_repo():
    """Create a temporary Git repository for testing"""
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Initialize Git repository
        repo = Repo.init(temp_dir)
        
        # Configure Git user (required for commits)
        with repo.config_writer() as config:
            config.set_value("user", "name", "Test User")
            config.set_value("user", "email", "test@example.com")
        
        # Create initial commit
        test_file = Path(temp_dir) / "README.md"
        test_file.write_text("# Test Repository\n")
        repo.index.add(["README.md"])
        repo.index.commit("Initial commit")
        
        yield temp_dir
    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def git_service(temp_git_repo):
    """Create GitOperationsService instance"""
    return GitOperationsService(temp_git_repo)


@pytest.fixture
def conflict_resolver(temp_git_repo):
    """Create ConflictResolver instance"""
    return ConflictResolver(temp_git_repo)


class TestConflictResolverIntegration:
    """Test ConflictResolver integration with GitOperationsService"""
    
    def test_detect_conflicts_from_cherry_pick(
        self,
        git_service,
        conflict_resolver,
        temp_git_repo
    ):
        """Test detecting conflicts created by GitOperationsService cherry-pick"""
        repo = git_service.repo
        current_branch = git_service.get_current_branch()
        
        # Create a file on main branch
        test_file = Path(temp_git_repo) / "conflict.txt"
        test_file.write_text("Original content\n")
        repo.index.add(["conflict.txt"])
        repo.index.commit("Add conflict.txt")
        
        # Create feature branch and modify the file
        feature_branch = "feature/test"
        git_service.create_branch(feature_branch, current_branch)
        repo.git.checkout(feature_branch)
        
        test_file.write_text("Feature content\n")
        repo.index.add(["conflict.txt"])
        feature_commit = repo.index.commit("Modify in feature")
        
        # Go back to main and modify the same file differently
        repo.git.checkout(current_branch)
        test_file.write_text("Main content\n")
        repo.index.add(["conflict.txt"])
        repo.index.commit("Modify in main")
        
        # Create release branch
        release_branch = "release/test"
        git_service.create_branch(release_branch, current_branch)
        
        # Apply commits (will create conflict)
        result = git_service.apply_commits(release_branch, [feature_commit.hexsha])
        
        # Verify GitOperationsService detected conflict
        assert result["success"] is False
        assert len(result["conflicts"]) > 0
        
        # Use ConflictResolver to get detailed conflict information
        conflicts = conflict_resolver.detect_conflicts()
        
        # Verify ConflictResolver also detects the conflict
        assert len(conflicts) > 0
        conflict = conflicts[0]
        assert conflict.file_path == "conflict.txt"
        assert conflict.conflict_type in ["content", "delete", "add"]
        
        # Get detailed conflict information
        details = conflict_resolver.get_conflict_details("conflict.txt")
        
        # Verify we can extract "ours" and "theirs" content
        assert "Main content" in details.ours_content
        assert "Feature content" in details.theirs_content
        assert len(details.conflict_markers) > 0
    
    def test_conflict_details_after_git_operations(
        self,
        git_service,
        conflict_resolver,
        temp_git_repo
    ):
        """Test getting conflict details after GitOperationsService creates conflict"""
        repo = git_service.repo
        current_branch = git_service.get_current_branch()
        
        # Create a file with multiple lines
        test_file = Path(temp_git_repo) / "multi.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")
        repo.index.add(["multi.txt"])
        repo.index.commit("Add multi.txt")
        
        # Create feature branch and modify middle lines
        feature_branch = "feature/multi"
        git_service.create_branch(feature_branch, current_branch)
        repo.git.checkout(feature_branch)
        
        test_file.write_text("line 1\nfeature line 2\nfeature line 3\nline 4\nline 5\n")
        repo.index.add(["multi.txt"])
        feature_commit = repo.index.commit("Modify middle lines in feature")
        
        # Go back to main and modify same lines differently
        repo.git.checkout(current_branch)
        test_file.write_text("line 1\nmain line 2\nmain line 3\nline 4\nline 5\n")
        repo.index.add(["multi.txt"])
        repo.index.commit("Modify middle lines in main")
        
        # Create release branch and apply commits
        release_branch = "release/multi"
        git_service.create_branch(release_branch, current_branch)
        result = git_service.apply_commits(release_branch, [feature_commit.hexsha])
        
        # Verify conflict occurred
        assert result["success"] is False
        
        # Get detailed conflict information
        details = conflict_resolver.get_conflict_details("multi.txt")
        
        # Verify conflict details
        assert "line 1" in details.ours_content
        assert "line 1" in details.theirs_content
        assert "main line 2" in details.ours_content or "main line 3" in details.ours_content
        assert "feature line 2" in details.theirs_content or "feature line 3" in details.theirs_content
        assert "line 5" in details.ours_content
        assert "line 5" in details.theirs_content
    
    def test_multiple_conflicts_integration(
        self,
        git_service,
        conflict_resolver,
        temp_git_repo
    ):
        """Test handling multiple conflicts from GitOperationsService"""
        repo = git_service.repo
        current_branch = git_service.get_current_branch()
        
        # Create two files on main branch
        file1 = Path(temp_git_repo) / "file1.txt"
        file2 = Path(temp_git_repo) / "file2.txt"
        file1.write_text("file1 original\n")
        file2.write_text("file2 original\n")
        repo.index.add(["file1.txt", "file2.txt"])
        repo.index.commit("Add files")
        
        # Create feature branch and modify both files
        feature_branch = "feature/multi-conflict"
        git_service.create_branch(feature_branch, current_branch)
        repo.git.checkout(feature_branch)
        
        file1.write_text("file1 feature\n")
        file2.write_text("file2 feature\n")
        repo.index.add(["file1.txt", "file2.txt"])
        feature_commit = repo.index.commit("Modify both files in feature")
        
        # Go back to main and modify both files differently
        repo.git.checkout(current_branch)
        file1.write_text("file1 main\n")
        file2.write_text("file2 main\n")
        repo.index.add(["file1.txt", "file2.txt"])
        repo.index.commit("Modify both files in main")
        
        # Create release branch and apply commits
        release_branch = "release/multi-conflict"
        git_service.create_branch(release_branch, current_branch)
        result = git_service.apply_commits(release_branch, [feature_commit.hexsha])
        
        # Verify conflicts occurred
        assert result["success"] is False
        assert len(result["conflicts"]) >= 1  # At least one conflict
        
        # Detect all conflicts
        conflicts = conflict_resolver.detect_conflicts()
        
        # Should detect conflicts in both files
        assert len(conflicts) >= 1
        conflict_files = [c.file_path for c in conflicts]
        
        # Get details for each conflicting file
        for conflict in conflicts:
            details = conflict_resolver.get_conflict_details(conflict.file_path)
            
            # Verify we can extract content for each file
            assert details.ours_content is not None
            assert details.theirs_content is not None
            assert len(details.conflict_markers) > 0
    
    def test_no_conflicts_integration(
        self,
        git_service,
        conflict_resolver,
        temp_git_repo
    ):
        """Test that no conflicts are detected when commits apply cleanly"""
        repo = git_service.repo
        current_branch = git_service.get_current_branch()
        
        # Create feature branch with new file
        feature_branch = "feature/clean"
        git_service.create_branch(feature_branch, current_branch)
        repo.git.checkout(feature_branch)
        
        new_file = Path(temp_git_repo) / "new.txt"
        new_file.write_text("New file content\n")
        repo.index.add(["new.txt"])
        feature_commit = repo.index.commit("Add new file")
        
        # Go back to main (no changes)
        repo.git.checkout(current_branch)
        
        # Create release branch and apply commits
        release_branch = "release/clean"
        git_service.create_branch(release_branch, current_branch)
        result = git_service.apply_commits(release_branch, [feature_commit.hexsha])
        
        # Verify no conflicts
        assert result["success"] is True
        assert len(result["conflicts"]) == 0
        
        # ConflictResolver should also detect no conflicts
        conflicts = conflict_resolver.detect_conflicts()
        assert len(conflicts) == 0
