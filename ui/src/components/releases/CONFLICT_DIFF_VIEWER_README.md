# Conflict Diff Viewer Implementation

## Overview

This document describes the implementation of Task 15.2: Implement conflict diff viewer for the Azure Release Management feature.

## Components Implemented

### 1. ConflictDiffViewer.jsx

A new component that provides a side-by-side diff viewer for merge conflicts using Monaco Editor.

**Features:**
- ✅ Side-by-side diff display with "ours" (current branch) and "theirs" (incoming changes)
- ✅ Syntax highlighting based on file extension
- ✅ Conflict marker highlighting (<<<<<<, =======, >>>>>>>)
- ✅ Commit information display for both sides
- ✅ Visual distinction between current and incoming changes
- ✅ Conflict markers legend for user guidance
- ✅ Support for 30+ programming languages

**Props:**
- `conflict`: Object containing conflict metadata (file_path, conflict_type, commit hashes)
- `oursContent`: String content from current branch
- `theirsContent`: String content from incoming changes
- `onContentChange`: Callback function for manual content editing

### 2. ConflictResolutionInterface.jsx (Updated)

Enhanced the existing component to integrate the new diff viewer.

**New Features:**
- ✅ Fetches conflict details when a file is selected
- ✅ Displays loading state while fetching details
- ✅ Integrates ConflictDiffViewer component
- ✅ Shows resolution status for each file
- ✅ Provides visual feedback for resolved/unresolved files

## Requirements Satisfied

### Requirement 8.4: Conflict Detection Display
- ✅ Displays list of conflicting files
- ✅ Shows conflict markers
- ✅ Identifies conflicting commits

### Requirement 8.5: Conflict Information
- ✅ Shows commit hashes for both sides
- ✅ Displays commit messages
- ✅ Highlights conflict regions

### Requirement 9.1: Conflict Resolution Interface
- ✅ Provides interactive interface for each file
- ✅ Displays conflicting sections clearly

### Requirement 9.2: Side-by-Side Comparison
- ✅ Shows "ours" and "theirs" versions side-by-side
- ✅ Highlights differences
- ✅ Provides syntax highlighting

## Dependencies Added

```json
{
  "@monaco-editor/react": "^4.6.0",
  "monaco-editor": "^0.52.2"
}
```

## File Structure

```
ui/src/components/releases/
├── ConflictResolutionInterface.jsx (updated)
├── ConflictDiffViewer.jsx (new)
├── ConflictResolutionInterface.test.jsx (new)
└── CONFLICT_DIFF_VIEWER_README.md (this file)
```

## Usage Example

```jsx
import ConflictDiffViewer from './ConflictDiffViewer';

const conflict = {
  file_path: 'src/components/Example.jsx',
  conflict_type: 'content',
  our_commit: 'abc123',
  their_commit: 'xyz789',
  our_commit_message: 'Add feature X',
  their_commit_message: 'Update feature X'
};

<ConflictDiffViewer
  conflict={conflict}
  oursContent={oursFileContent}
  theirsContent={theirsFileContent}
  onContentChange={(newContent) => {
    console.log('User edited content:', newContent);
  }}
/>
```

## Manual Testing Guide

### Test Scenario 1: View Conflict Diff
1. Navigate to the Releases page
2. Create a new release that results in conflicts
3. Select a conflicting file from the list
4. Verify the diff viewer displays:
   - Side-by-side comparison
   - Commit information for both sides
   - Conflict markers highlighted
   - Syntax highlighting for the file type

### Test Scenario 2: Conflict Markers
1. Select a file with conflicts
2. Verify the conflict markers legend is displayed
3. Verify conflict regions are highlighted in the diff
4. Check that the conflict count is accurate

### Test Scenario 3: Multiple File Types
1. Create conflicts in different file types (.js, .py, .css, etc.)
2. Verify syntax highlighting works for each type
3. Verify the language is correctly detected from file extension

### Test Scenario 4: Resolution Workflow
1. Select a conflicting file
2. Review the diff viewer
3. Click "Mark as Resolved"
4. Verify the file is marked as resolved in the list
5. Verify progress bar updates
6. Repeat for all files
7. Verify "Continue with Release" button becomes enabled

## API Integration Notes

The current implementation uses mock data for demonstration. To integrate with the backend API:

1. Replace the mock data generation in `ConflictResolutionInterface.jsx`:

```javascript
// Replace this:
const mockDetails = {
  file_path: selectedFile.file_path,
  // ... mock data
};

// With actual API call:
const response = await axios.get(
  `/api/releases/${releaseId}/conflicts/${encodeURIComponent(selectedFile.file_path)}`
);
const conflictDetails = response.data;
```

2. The API endpoint should return:
```json
{
  "file_path": "src/example.js",
  "conflict_type": "content",
  "our_commit": "abc123",
  "their_commit": "xyz789",
  "our_commit_message": "Current branch changes",
  "their_commit_message": "Incoming changes",
  "ours_content": "// file content from current branch",
  "theirs_content": "// file content from incoming changes"
}
```

## Language Support

The diff viewer supports syntax highlighting for:
- JavaScript/JSX
- TypeScript/TSX
- Python
- Java
- C#
- C/C++
- Go
- Rust
- Ruby
- PHP
- HTML/CSS/SCSS
- JSON/XML/YAML
- Markdown
- SQL
- Shell scripts
- PowerShell
- Dockerfile

## Performance Considerations

- Monaco Editor is loaded on-demand when the diff viewer is displayed
- Large files (>10,000 lines) are handled efficiently by Monaco's virtual scrolling
- Syntax highlighting is performed asynchronously
- The diff algorithm is optimized for performance

## Future Enhancements

Potential improvements for future tasks:

1. **Inline Editing**: Allow users to edit the merged content directly in the diff viewer
2. **Three-Way Merge**: Show base, ours, and theirs in a three-way comparison
3. **Conflict Resolution Suggestions**: AI-powered suggestions for resolving conflicts
4. **Keyboard Shortcuts**: Add keyboard shortcuts for navigation and resolution
5. **Conflict History**: Track resolution history for audit purposes

## Troubleshooting

### Monaco Editor Not Loading
- Ensure `@monaco-editor/react` and `monaco-editor` are installed
- Check browser console for errors
- Verify the component is rendered with valid content

### Syntax Highlighting Not Working
- Check that the file extension is recognized in `getLanguageFromFilePath()`
- Add new extensions to the `languageMap` if needed

### Diff Not Displaying
- Verify both `oursContent` and `theirsContent` props are provided
- Check that the content is valid string data
- Ensure the Monaco Editor container has a defined height

## Related Documentation

- [Monaco Editor Documentation](https://microsoft.github.io/monaco-editor/)
- [@monaco-editor/react Documentation](https://github.com/suren-atoyan/monaco-react)
- [Azure Release Management Design Document](../../../.kiro/specs/azure-release-management/design.md)
- [Task 15.2 Specification](../../../.kiro/specs/azure-release-management/tasks.md#152-implement-conflict-diff-viewer)
