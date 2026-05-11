"""
Unit tests for ConflictResolver

Tests the conflict resolver service including:
- Conflict detection
- Conflict marker parsing
- Content extraction ("ours" and "theirs")
- Detailed conflict information

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

import os
import tempfile
import shutil
from pathlib import Path

import pytest
from git import Repo

from app.services.conflict_resolver import (
    ConflictResolver,
    ConflictResolverError,
    ConflictNotFoundError,
    ConflictParseError,
    ConflictMarker,
    ConflictDetails,
    MergeConflict
)


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
def conflict_resolver(temp_git_repo):
    """Create ConflictResolver instance with temporary repository"""
    return ConflictResolver(temp_git_repo)


class TestConflictResolverInitialization:
    """Test ConflictResolver initialization"""
    
    def test_init_with_valid_repo(self, temp_git_repo):
        """Test initialization with valid repository path"""
        resolver = ConflictResolver(temp_git_repo)
        assert resolver.repo_path == Path(temp_git_repo)
        assert resolver.repo is not None
    
    def test_init_with_nonexistent_path(self):
        """Test initialization with non-existent path raises error"""
        with pytest.raises(ConflictResolverError) as exc_info:
            ConflictResolver("/nonexistent/path")
        assert "does not exist" in str(exc_info.value)
    
    def test_init_with_non_git_directory(self):
        """Test initialization with non-Git directory raises error"""
        temp_dir = tempfile.mkdtemp()
        try:
            with pytest.raises(ConflictResolverError) as exc_info:
                ConflictResolver(temp_dir)
            assert "Invalid Git repository" in str(exc_info.value)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestConflictDetection:
    """Test conflict detection functionality"""
    
    def test_detect_conflicts_no_conflicts(self, conflict_resolver):
        """Test detecting conflicts when there are none"""
        conflicts = conflict_resolver.detect_conflicts()
        assert conflicts == []
    
    def test_detect_conflicts_with_merge_conflict(self, conflict_resolver, temp_git_repo):
        """Test detecting conflicts during merge"""
        repo = conflict_resolver.repo
        
        # Create a file on main branch
        test_file = Path(temp_git_repo) / "conflict.txt"
        test_file.write_text("Original content\n")
        repo.index.add(["conflict.txt"])
        repo.index.commit("Add conflict.txt")
        
        # Create feature branch and modify the file
        feature_branch = repo.create_head("feature/test")
        feature_branch.checkout()
        
        test_file.write_text("Feature content\n")
        repo.index.add(["conflict.txt"])
        repo.index.commit("Modify in feature")
        
        # Go back to main and modify the same file differently
        repo.heads.master.checkout()
        test_file.write_text("Main content\n")
        repo.index.add(["conflict.txt"])
        repo.index.commit("Modify in main")
        
        # Try to merge feature branch (will create conflict)
        try:
            repo.git.merge("feature/test")
        except Exception:
            # Merge will fail due to conflict
            pass
        
        # Detect conflicts
        conflicts = conflict_resolver.detect_conflicts()
        
        # Verify conflict was detected
        assert len(conflicts) > 0
        conflict = conflicts[0]
        assert isinstance(conflict, MergeConflict)
        assert conflict.file_path == "conflict.txt"
        assert conflict.conflict_type in ["content", "delete", "add"]
        assert conflict.our_commit is not None
        assert conflict.their_commit is not None
    
    def test_detect_conflicts_with_cherry_pick_conflict(self, conflict_resolver, temp_git_repo):
        """Test detecting conflicts during cherry-pick"""
        repo = conflict_resolver.repo
        
        # Create a file on main branch
        test_file = Path(temp_git_repo) / "cherry.txt"
        test_file.write_text("Original content\n")
        repo.index.add(["cherry.txt"])
        repo.index.commit("Add cherry.txt")
        
        # Create feature branch and modify the file
        feature_branch = repo.create_head("feature/cherry")
        feature_branch.checkout()
        
        test_file.write_text("Feature content\n")
        repo.index.add(["cherry.txt"])
        feature_commit = repo.index.commit("Modify in feature")
        
        # Go back to main and modify the same file differently
        repo.heads.master.checkout()
        test_file.write_text("Main content\n")
        repo.index.add(["cherry.txt"])
        repo.index.commit("Modify in main")
        
        # Try to cherry-pick feature commit (will create conflict)
        try:
            repo.git.cherry_pick(feature_commit.hexsha)
        except Exception:
            # Cherry-pick will fail due to conflict
            pass
        
        # Detect conflicts
        conflicts = conflict_resolver.detect_conflicts()
        
        # Verify conflict was detected
        assert len(conflicts) > 0
        conflict = conflicts[0]
        assert conflict.file_path == "cherry.txt"
        assert conflict.conflict_type in ["content", "delete", "add"]


class TestConflictMarkerParsing:
    """Test conflict marker parsing"""
    
    def test_parse_simple_conflict_markers(self, conflict_resolver):
        """Test parsing simple conflict markers"""
        lines = [
            "line 1\n",
            "line 2\n",
            "<<<<<<< HEAD\n",
            "ours content\n",
            "=======\n",
            "theirs content\n",
            ">>>>>>> feature\n",
            "line 3\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        
        assert len(markers) == 1
        marker = markers[0]
        assert marker.start_line == 2
        assert marker.end_line == 6
        assert marker.ours_start == 3
        assert marker.ours_end == 4
        assert marker.theirs_start == 5
        assert marker.theirs_end == 6
        assert marker.base_start is None
        assert marker.base_end is None
    
    def test_parse_diff3_style_conflict_markers(self, conflict_resolver):
        """Test parsing diff3 style conflict markers with base"""
        lines = [
            "line 1\n",
            "<<<<<<< HEAD\n",
            "ours content\n",
            "||||||| base\n",
            "base content\n",
            "=======\n",
            "theirs content\n",
            ">>>>>>> feature\n",
            "line 2\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        
        assert len(markers) == 1
        marker = markers[0]
        assert marker.start_line == 1
        assert marker.end_line == 7
        assert marker.ours_start == 2
        assert marker.ours_end == 3
        assert marker.base_start == 4
        assert marker.base_end == 5
        assert marker.theirs_start == 6
        assert marker.theirs_end == 7
    
    def test_parse_multiple_conflict_markers(self, conflict_resolver):
        """Test parsing multiple conflict sections"""
        lines = [
            "line 1\n",
            "<<<<<<< HEAD\n",
            "ours 1\n",
            "=======\n",
            "theirs 1\n",
            ">>>>>>> feature\n",
            "line 2\n",
            "<<<<<<< HEAD\n",
            "ours 2\n",
            "=======\n",
            "theirs 2\n",
            ">>>>>>> feature\n",
            "line 3\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        
        assert len(markers) == 2
        
        # First conflict
        marker1 = markers[0]
        assert marker1.start_line == 1
        assert marker1.end_line == 5
        assert marker1.ours_start == 2
        assert marker1.ours_end == 3
        
        # Second conflict
        marker2 = markers[1]
        assert marker2.start_line == 7
        assert marker2.end_line == 11
        assert marker2.ours_start == 8
        assert marker2.ours_end == 9
    
    def test_parse_conflict_markers_multiline_content(self, conflict_resolver):
        """Test parsing conflict markers with multi-line content"""
        lines = [
            "<<<<<<< HEAD\n",
            "ours line 1\n",
            "ours line 2\n",
            "ours line 3\n",
            "=======\n",
            "theirs line 1\n",
            "theirs line 2\n",
            ">>>>>>> feature\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        
        assert len(markers) == 1
        marker = markers[0]
        assert marker.ours_start == 1
        assert marker.ours_end == 4
        assert marker.theirs_start == 5
        assert marker.theirs_end == 7
    
    def test_parse_conflict_markers_empty_sections(self, conflict_resolver):
        """Test parsing conflict markers with empty sections"""
        lines = [
            "<<<<<<< HEAD\n",
            "=======\n",
            "theirs content\n",
            ">>>>>>> feature\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        
        assert len(markers) == 1
        marker = markers[0]
        assert marker.ours_start == 1
        assert marker.ours_end == 1  # Empty ours section
        assert marker.theirs_start == 2
        assert marker.theirs_end == 3
    
    def test_parse_malformed_conflict_no_separator(self, conflict_resolver):
        """Test parsing malformed conflict without separator"""
        lines = [
            "<<<<<<< HEAD\n",
            "ours content\n",
            ">>>>>>> feature\n",
        ]
        
        with pytest.raises(ConflictParseError) as exc_info:
            conflict_resolver._parse_conflict_markers(lines)
        assert "separator not found" in str(exc_info.value)
    
    def test_parse_malformed_conflict_no_end_marker(self, conflict_resolver):
        """Test parsing malformed conflict without end marker"""
        lines = [
            "<<<<<<< HEAD\n",
            "ours content\n",
            "=======\n",
            "theirs content\n",
        ]
        
        with pytest.raises(ConflictParseError) as exc_info:
            conflict_resolver._parse_conflict_markers(lines)
        assert "end marker not found" in str(exc_info.value)
    
    def test_parse_no_conflict_markers(self, conflict_resolver):
        """Test parsing file with no conflict markers"""
        lines = [
            "line 1\n",
            "line 2\n",
            "line 3\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        assert markers == []


class TestContentExtraction:
    """Test extracting 'ours' and 'theirs' content"""
    
    def test_extract_simple_conflict_content(self, conflict_resolver):
        """Test extracting content from simple conflict"""
        lines = [
            "line 1\n",
            "line 2\n",
            "<<<<<<< HEAD\n",
            "ours content\n",
            "=======\n",
            "theirs content\n",
            ">>>>>>> feature\n",
            "line 3\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        ours, theirs, base = conflict_resolver._extract_conflict_content(lines, markers)
        
        assert ours == "line 1\nline 2\nours content\nline 3\n"
        assert theirs == "line 1\nline 2\ntheirs content\nline 3\n"
        assert base is None or base == "line 1\nline 2\nline 3\n"
    
    def test_extract_diff3_conflict_content(self, conflict_resolver):
        """Test extracting content from diff3 style conflict"""
        lines = [
            "line 1\n",
            "<<<<<<< HEAD\n",
            "ours content\n",
            "||||||| base\n",
            "base content\n",
            "=======\n",
            "theirs content\n",
            ">>>>>>> feature\n",
            "line 2\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        ours, theirs, base = conflict_resolver._extract_conflict_content(lines, markers)
        
        assert ours == "line 1\nours content\nline 2\n"
        assert theirs == "line 1\ntheirs content\nline 2\n"
        assert base == "line 1\nbase content\nline 2\n"
    
    def test_extract_multiple_conflicts_content(self, conflict_resolver):
        """Test extracting content from multiple conflicts"""
        lines = [
            "line 1\n",
            "<<<<<<< HEAD\n",
            "ours 1\n",
            "=======\n",
            "theirs 1\n",
            ">>>>>>> feature\n",
            "line 2\n",
            "<<<<<<< HEAD\n",
            "ours 2\n",
            "=======\n",
            "theirs 2\n",
            ">>>>>>> feature\n",
            "line 3\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        ours, theirs, base = conflict_resolver._extract_conflict_content(lines, markers)
        
        assert ours == "line 1\nours 1\nline 2\nours 2\nline 3\n"
        assert theirs == "line 1\ntheirs 1\nline 2\ntheirs 2\nline 3\n"
    
    def test_extract_conflict_content_multiline(self, conflict_resolver):
        """Test extracting multi-line conflict content"""
        lines = [
            "<<<<<<< HEAD\n",
            "ours line 1\n",
            "ours line 2\n",
            "=======\n",
            "theirs line 1\n",
            "theirs line 2\n",
            "theirs line 3\n",
            ">>>>>>> feature\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        ours, theirs, base = conflict_resolver._extract_conflict_content(lines, markers)
        
        assert ours == "ours line 1\nours line 2\n"
        assert theirs == "theirs line 1\ntheirs line 2\ntheirs line 3\n"
    
    def test_extract_conflict_content_empty_sections(self, conflict_resolver):
        """Test extracting content with empty sections"""
        lines = [
            "line 1\n",
            "<<<<<<< HEAD\n",
            "=======\n",
            "theirs content\n",
            ">>>>>>> feature\n",
            "line 2\n",
        ]
        
        markers = conflict_resolver._parse_conflict_markers(lines)
        ours, theirs, base = conflict_resolver._extract_conflict_content(lines, markers)
        
        assert ours == "line 1\nline 2\n"
        assert theirs == "line 1\ntheirs content\nline 2\n"


class TestGetConflictDetails:
    """Test getting detailed conflict information"""
    
    def test_get_conflict_details_with_conflict(self, conflict_resolver, temp_git_repo):
        """Test getting conflict details for a file with conflicts"""
        # Create a file with conflict markers
        conflict_file = Path(temp_git_repo) / "conflict.txt"
        conflict_content = """line 1
line 2
<<<<<<< HEAD
ours content
=======
theirs content
>>>>>>> feature
line 3
"""
        conflict_file.write_text(conflict_content)
        
        # Get conflict details
        details = conflict_resolver.get_conflict_details("conflict.txt")
        
        assert isinstance(details, ConflictDetails)
        assert details.file_path == "conflict.txt"
        assert "ours content" in details.ours_content
        assert "theirs content" in details.theirs_content
        assert len(details.conflict_markers) == 1
        assert details.full_content == conflict_content
    
    def test_get_conflict_details_no_conflict(self, conflict_resolver, temp_git_repo):
        """Test getting conflict details for file without conflicts"""
        # Create a file without conflict markers
        normal_file = Path(temp_git_repo) / "normal.txt"
        normal_file.write_text("line 1\nline 2\nline 3\n")
        
        # Should raise ConflictNotFoundError
        with pytest.raises(ConflictNotFoundError) as exc_info:
            conflict_resolver.get_conflict_details("normal.txt")
        assert "No conflict markers found" in str(exc_info.value)
    
    def test_get_conflict_details_file_not_found(self, conflict_resolver):
        """Test getting conflict details for non-existent file"""
        with pytest.raises(ConflictNotFoundError) as exc_info:
            conflict_resolver.get_conflict_details("nonexistent.txt")
        assert "File not found" in str(exc_info.value)
    
    def test_get_conflict_details_diff3_style(self, conflict_resolver, temp_git_repo):
        """Test getting conflict details for diff3 style conflict"""
        conflict_file = Path(temp_git_repo) / "diff3.txt"
        conflict_content = """line 1
<<<<<<< HEAD
ours content
||||||| base
base content
=======
theirs content
>>>>>>> feature
line 2
"""
        conflict_file.write_text(conflict_content)
        
        details = conflict_resolver.get_conflict_details("diff3.txt")
        
        assert "ours content" in details.ours_content
        assert "theirs content" in details.theirs_content
        assert details.base_content is not None
        assert "base content" in details.base_content
    
    def test_get_conflict_details_multiple_conflicts(self, conflict_resolver, temp_git_repo):
        """Test getting conflict details for file with multiple conflicts"""
        conflict_file = Path(temp_git_repo) / "multiple.txt"
        conflict_content = """line 1
<<<<<<< HEAD
ours 1
=======
theirs 1
>>>>>>> feature
line 2
<<<<<<< HEAD
ours 2
=======
theirs 2
>>>>>>> feature
line 3
"""
        conflict_file.write_text(conflict_content)
        
        details = conflict_resolver.get_conflict_details("multiple.txt")
        
        assert len(details.conflict_markers) == 2
        assert "ours 1" in details.ours_content
        assert "ours 2" in details.ours_content
        assert "theirs 1" in details.theirs_content
        assert "theirs 2" in details.theirs_content


class TestDataModels:
    """Test data model conversions"""
    
    def test_conflict_marker_to_dict(self):
        """Test ConflictMarker to_dict conversion"""
        marker = ConflictMarker(
            start_line=1,
            end_line=5,
            ours_start=2,
            ours_end=3,
            theirs_start=4,
            theirs_end=5,
            base_start=None,
            base_end=None
        )
        
        data = marker.to_dict()
        
        assert data["start_line"] == 1
        assert data["end_line"] == 5
        assert data["ours_start"] == 2
        assert data["ours_end"] == 3
        assert data["theirs_start"] == 4
        assert data["theirs_end"] == 5
        assert data["base_start"] is None
        assert data["base_end"] is None
    
    def test_conflict_details_to_dict(self):
        """Test ConflictDetails to_dict conversion"""
        marker = ConflictMarker(1, 5, 2, 3, 4, 5)
        details = ConflictDetails(
            file_path="test.txt",
            ours_content="ours",
            theirs_content="theirs",
            base_content="base",
            conflict_markers=[marker],
            full_content="full"
        )
        
        data = details.to_dict()
        
        assert data["file_path"] == "test.txt"
        assert data["ours_content"] == "ours"
        assert data["theirs_content"] == "theirs"
        assert data["base_content"] == "base"
        assert len(data["conflict_markers"]) == 1
        assert data["full_content"] == "full"
    
    def test_merge_conflict_to_dict(self):
        """Test MergeConflict to_dict conversion"""
        conflict = MergeConflict(
            file_path="test.txt",
            conflict_type="content",
            our_commit="abc123",
            their_commit="def456"
        )
        
        data = conflict.to_dict()
        
        assert data["file_path"] == "test.txt"
        assert data["conflict_type"] == "content"
        assert data["our_commit"] == "abc123"
        assert data["their_commit"] == "def456"


class TestConflictResolution:
    """Test conflict resolution methods"""
    
    def test_resolve_conflict_ours(self, conflict_resolver, temp_git_repo):
        """Test resolving conflict by accepting 'ours' version"""
        # Create a file with conflict markers
        conflict_file = Path(temp_git_repo) / "resolve_ours.txt"
        conflict_content = """line 1
<<<<<<< HEAD
ours content
=======
theirs content
>>>>>>> feature
line 2
"""
        conflict_file.write_text(conflict_content)
        
        # Resolve conflict with "ours"
        result = conflict_resolver.resolve_conflict("resolve_ours.txt", "ours")
        
        assert result["success"] is True
        assert result["file_path"] == "resolve_ours.txt"
        assert result["resolution_type"] == "ours"
        assert result["error_message"] is None
        
        # Verify file content
        resolved_content = conflict_file.read_text()
        assert "ours content" in resolved_content
        assert "theirs content" not in resolved_content
        assert "<<<<<<< HEAD" not in resolved_content
        assert "=======" not in resolved_content
        assert ">>>>>>>" not in resolved_content
    
    def test_resolve_conflict_theirs(self, conflict_resolver, temp_git_repo):
        """Test resolving conflict by accepting 'theirs' version"""
        conflict_file = Path(temp_git_repo) / "resolve_theirs.txt"
        conflict_content = """line 1
<<<<<<< HEAD
ours content
=======
theirs content
>>>>>>> feature
line 2
"""
        conflict_file.write_text(conflict_content)
        
        # Resolve conflict with "theirs"
        result = conflict_resolver.resolve_conflict("resolve_theirs.txt", "theirs")
        
        assert result["success"] is True
        assert result["resolution_type"] == "theirs"
        
        # Verify file content
        resolved_content = conflict_file.read_text()
        assert "theirs content" in resolved_content
        assert "ours content" not in resolved_content
        assert "<<<<<<< HEAD" not in resolved_content
    
    def test_resolve_conflict_manual(self, conflict_resolver, temp_git_repo):
        """Test resolving conflict with manual content"""
        conflict_file = Path(temp_git_repo) / "resolve_manual.txt"
        conflict_content = """line 1
<<<<<<< HEAD
ours content
=======
theirs content
>>>>>>> feature
line 2
"""
        conflict_file.write_text(conflict_content)
        
        # Resolve conflict with manual content
        manual_content = "line 1\nmanual resolution\nline 2\n"
        result = conflict_resolver.resolve_conflict(
            "resolve_manual.txt",
            "manual",
            content=manual_content
        )
        
        assert result["success"] is True
        assert result["resolution_type"] == "manual"
        
        # Verify file content
        resolved_content = conflict_file.read_text()
        assert resolved_content == manual_content
        assert "ours content" not in resolved_content
        assert "theirs content" not in resolved_content
    
    def test_resolve_conflict_invalid_type(self, conflict_resolver, temp_git_repo):
        """Test resolving conflict with invalid resolution type"""
        conflict_file = Path(temp_git_repo) / "invalid.txt"
        conflict_content = """<<<<<<< HEAD
ours
=======
theirs
>>>>>>> feature
"""
        conflict_file.write_text(conflict_content)
        
        with pytest.raises(ConflictResolverError) as exc_info:
            conflict_resolver.resolve_conflict("invalid.txt", "invalid_type")
        assert "Invalid resolution type" in str(exc_info.value)
    
    def test_resolve_conflict_manual_without_content(self, conflict_resolver, temp_git_repo):
        """Test resolving conflict manually without providing content"""
        conflict_file = Path(temp_git_repo) / "no_content.txt"
        conflict_content = """<<<<<<< HEAD
ours
=======
theirs
>>>>>>> feature
"""
        conflict_file.write_text(conflict_content)
        
        with pytest.raises(ConflictResolverError) as exc_info:
            conflict_resolver.resolve_conflict("no_content.txt", "manual")
        assert "Content is required for manual resolution" in str(exc_info.value)
    
    def test_resolve_conflict_file_not_found(self, conflict_resolver):
        """Test resolving conflict for non-existent file"""
        with pytest.raises(ConflictNotFoundError) as exc_info:
            conflict_resolver.resolve_conflict("nonexistent.txt", "ours")
        assert "File not found" in str(exc_info.value)
    
    def test_resolve_conflict_no_conflict_markers(self, conflict_resolver, temp_git_repo):
        """Test resolving file without conflict markers"""
        normal_file = Path(temp_git_repo) / "normal.txt"
        normal_file.write_text("line 1\nline 2\n")
        
        with pytest.raises(ConflictNotFoundError) as exc_info:
            conflict_resolver.resolve_conflict("normal.txt", "ours")
        assert "No conflict markers found" in str(exc_info.value)


class TestMarkResolved:
    """Test marking files as resolved"""
    
    def test_mark_resolved_success(self, conflict_resolver, temp_git_repo):
        """Test marking a file as resolved"""
        # Create a file with resolved content
        resolved_file = Path(temp_git_repo) / "resolved.txt"
        resolved_file.write_text("resolved content\n")
        
        # Mark as resolved
        result = conflict_resolver.mark_resolved("resolved.txt")
        
        assert result is True
        
        # Verify file is in index
        repo = conflict_resolver.repo
        assert "resolved.txt" in [item[0] for item in repo.index.entries.keys()]
    
    def test_mark_resolved_file_not_found(self, conflict_resolver):
        """Test marking non-existent file as resolved"""
        with pytest.raises(ConflictResolverError) as exc_info:
            conflict_resolver.mark_resolved("nonexistent.txt")
        assert "File not found" in str(exc_info.value)
    
    def test_mark_resolved_after_resolution(self, conflict_resolver, temp_git_repo):
        """Test marking file as resolved after resolving conflict"""
        # Create conflict file
        conflict_file = Path(temp_git_repo) / "conflict.txt"
        conflict_content = """<<<<<<< HEAD
ours
=======
theirs
>>>>>>> feature
"""
        conflict_file.write_text(conflict_content)
        
        # Resolve conflict
        conflict_resolver.resolve_conflict("conflict.txt", "ours")
        
        # Mark as resolved
        result = conflict_resolver.mark_resolved("conflict.txt")
        
        assert result is True


class TestAbortMerge:
    """Test aborting merge operations"""
    
    def test_abort_merge_no_active_merge(self, conflict_resolver):
        """Test aborting when there's no active merge"""
        result = conflict_resolver.abort_merge()
        assert result is True
    
    def test_abort_merge_with_active_merge(self, conflict_resolver, temp_git_repo):
        """Test aborting an active merge operation"""
        repo = conflict_resolver.repo
        
        # Create a file on main branch
        test_file = Path(temp_git_repo) / "merge_test.txt"
        test_file.write_text("Original content\n")
        repo.index.add(["merge_test.txt"])
        repo.index.commit("Add merge_test.txt")
        
        # Create feature branch and modify the file
        feature_branch = repo.create_head("feature/abort")
        feature_branch.checkout()
        
        test_file.write_text("Feature content\n")
        repo.index.add(["merge_test.txt"])
        repo.index.commit("Modify in feature")
        
        # Go back to main and modify the same file differently
        repo.heads.master.checkout()
        test_file.write_text("Main content\n")
        repo.index.add(["merge_test.txt"])
        repo.index.commit("Modify in main")
        
        # Try to merge feature branch (will create conflict)
        try:
            repo.git.merge("feature/abort")
        except Exception:
            # Merge will fail due to conflict
            pass
        
        # Verify MERGE_HEAD exists
        merge_head_file = Path(temp_git_repo) / ".git" / "MERGE_HEAD"
        assert merge_head_file.exists()
        
        # Abort merge
        result = conflict_resolver.abort_merge()
        
        assert result is True
        
        # Verify MERGE_HEAD is removed
        assert not merge_head_file.exists()
        
        # Verify file is back to original state
        content = test_file.read_text()
        assert content == "Main content\n"
    
    def test_abort_merge_with_cherry_pick(self, conflict_resolver, temp_git_repo):
        """Test aborting an active cherry-pick operation"""
        repo = conflict_resolver.repo
        
        # Create a file on main branch
        test_file = Path(temp_git_repo) / "cherry_abort.txt"
        test_file.write_text("Original content\n")
        repo.index.add(["cherry_abort.txt"])
        repo.index.commit("Add cherry_abort.txt")
        
        # Create feature branch and modify the file
        feature_branch = repo.create_head("feature/cherry_abort")
        feature_branch.checkout()
        
        test_file.write_text("Feature content\n")
        repo.index.add(["cherry_abort.txt"])
        feature_commit = repo.index.commit("Modify in feature")
        
        # Go back to main and modify the same file differently
        repo.heads.master.checkout()
        test_file.write_text("Main content\n")
        repo.index.add(["cherry_abort.txt"])
        repo.index.commit("Modify in main")
        
        # Try to cherry-pick feature commit (will create conflict)
        try:
            repo.git.cherry_pick(feature_commit.hexsha)
        except Exception:
            # Cherry-pick will fail due to conflict
            pass
        
        # Verify CHERRY_PICK_HEAD exists
        cherry_pick_head_file = Path(temp_git_repo) / ".git" / "CHERRY_PICK_HEAD"
        assert cherry_pick_head_file.exists()
        
        # Abort cherry-pick
        result = conflict_resolver.abort_merge()
        
        assert result is True
        
        # Verify CHERRY_PICK_HEAD is removed
        assert not cherry_pick_head_file.exists()
        
        # Verify file is back to original state
        content = test_file.read_text()
        assert content == "Main content\n"


class TestEdgeCases:
    """Test edge cases and error handling"""
    
    def test_conflict_with_special_characters(self, conflict_resolver, temp_git_repo):
        """Test handling conflicts with special characters"""
        conflict_file = Path(temp_git_repo) / "special.txt"
        conflict_content = """<<<<<<< HEAD
ours: special chars !@#$%^&*()
=======
theirs: special chars <>&"'
>>>>>>> feature
"""
        conflict_file.write_text(conflict_content)
        
        details = conflict_resolver.get_conflict_details("special.txt")
        
        assert "!@#$%^&*()" in details.ours_content
        assert "<>&\"'" in details.theirs_content
    
    def test_conflict_with_unicode(self, conflict_resolver, temp_git_repo):
        """Test handling conflicts with unicode characters"""
        conflict_file = Path(temp_git_repo) / "unicode.txt"
        conflict_content = """<<<<<<< HEAD
ours: café ☕ 日本語
=======
theirs: naïve 🚀 中文
>>>>>>> feature
"""
        conflict_file.write_text(conflict_content, encoding='utf-8')
        
        details = conflict_resolver.get_conflict_details("unicode.txt")
        
        assert "café" in details.ours_content
        assert "naïve" in details.theirs_content
    
    def test_conflict_with_empty_file(self, conflict_resolver, temp_git_repo):
        """Test handling empty file"""
        empty_file = Path(temp_git_repo) / "empty.txt"
        empty_file.write_text("")
        
        with pytest.raises(ConflictNotFoundError):
            conflict_resolver.get_conflict_details("empty.txt")
    
    def test_determine_conflict_type_content(self, conflict_resolver):
        """Test determining content conflict type"""
        stages = {1: "base", 2: "ours", 3: "theirs"}
        conflict_type = conflict_resolver._determine_conflict_type(stages)
        assert conflict_type == "content"
    
    def test_determine_conflict_type_delete(self, conflict_resolver):
        """Test determining delete conflict type"""
        # They deleted
        stages = {1: "base", 2: "ours"}
        conflict_type = conflict_resolver._determine_conflict_type(stages)
        assert conflict_type == "delete"
        
        # We deleted
        stages = {1: "base", 3: "theirs"}
        conflict_type = conflict_resolver._determine_conflict_type(stages)
        assert conflict_type == "delete"
    
    def test_determine_conflict_type_add(self, conflict_resolver):
        """Test determining add conflict type"""
        stages = {2: "ours", 3: "theirs"}
        conflict_type = conflict_resolver._determine_conflict_type(stages)
        assert conflict_type == "add"
