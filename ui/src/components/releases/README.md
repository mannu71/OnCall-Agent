# Azure Release Management Components

This directory contains React components for the Azure Release Management feature.

## Components

### CreateReleaseWizard

A multi-step wizard component for creating Azure DevOps releases.

**Location**: `CreateReleaseWizard.jsx`

**Props**:
- `onComplete: (release) => void` - Callback when release is successfully created
- `onCancel: () => void` - Callback when user cancels the wizard

**Steps**:

1. **Organization & Project Selection**
   - User enters Azure DevOps organization name
   - User enters project name
   - Validates both fields are provided

2. **Work Item Selection**
   - User chooses search method: by tags or by PBI numbers
   - User enters comma-separated tags or PBI numbers
   - Fetches work items from Azure DevOps API

3. **Work Item Review**
   - Displays fetched work items in a table
   - Shows validation warnings for non-closed PBIs
   - Allows user to select/deselect work items
   - Provides "Remove Non-Closed PBIs" button

4. **Commit Selection**
   - Fetches commits for each selected work item
   - Groups commits by PBI
   - Allows individual commit selection
   - Provides "Select All" and "Deselect All" buttons

5. **Branch Configuration**
   - User enters release name
   - User enters branch name (validated against Git conventions)
   - User can specify base branch (defaults to "main")

6. **Release Creation**
   - Displays summary of release configuration
   - Shows loading indicator during creation
   - Calls API to create release
   - Handles success, errors, and conflicts

**API Integration**:

The wizard integrates with the following API endpoints (via `agentApiClient`):

- `POST /api/releases/work-items/search` - Search for work items
- `GET /api/releases/work-items/{id}/commits` - Get commits for work item
- `POST /api/releases` - Create release

**State Management**:

The wizard maintains the following state:
- `currentStep`: Current wizard step (1-6)
- `loading`: Loading state for async operations
- `error`: Error message to display
- `formData`: All form data including:
  - organization, project
  - search criteria (tags or PBI numbers)
  - work items and selected work item IDs
  - commits and selected commit IDs
  - release name, branch name, base branch
- `validationWarnings`: Warnings for non-closed PBIs

**Usage Example**:

```jsx
import CreateReleaseWizard from '@/components/releases/CreateReleaseWizard';

function MyComponent() {
  const handleComplete = (release) => {
    console.log('Release created:', release);
    // Navigate back to list or show success message
  };

  const handleCancel = () => {
    // Navigate back to list
  };

  return (
    <CreateReleaseWizard
      onComplete={handleComplete}
      onCancel={handleCancel}
    />
  );
}
```

**Features**:

- ✅ Multi-step progress indicator
- ✅ Form validation at each step
- ✅ Loading states for async operations
- ✅ Error handling and display
- ✅ Validation warnings for non-closed PBIs
- ✅ Commit grouping by PBI
- ✅ Branch name validation
- ✅ Responsive design
- ⏳ Conflict resolution (Task 15)

**Dependencies**:

- React hooks (useState, useCallback, useMemo)
- Lucide React icons
- shadcn/ui components (Button, Card, Input, Label, Badge, Alert, Checkbox)
- agentApiClient for API calls

**Notes**:

- The wizard validates each step before allowing progression
- All commits are auto-selected by default in Step 4
- Branch name must follow Git naming conventions (alphanumeric, /, _, -)
- Conflict resolution will be implemented in Task 15
- The wizard can be cancelled at any step with confirmation

---

### ConflictResolutionInterface

An interactive UI component for resolving merge conflicts during release creation.

**Location**: `ConflictResolutionInterface.jsx`

**Props**:
- `conflicts: Array<Conflict>` - Array of conflict objects
- `releaseId: string` - ID of the release being created
- `onResolve: (data) => void` - Callback when all conflicts are resolved and user continues
- `onAbort: () => void` - Callback when user aborts the release

**Conflict Object Structure**:
```typescript
{
  file_path: string;        // Path to the conflicting file
  conflict_type: string;    // Type of conflict (e.g., "content", "delete", "rename")
  our_commit: string;       // Commit hash from our branch
  their_commit: string;     // Commit hash from their branch
}
```

**Features**:

- ✅ Display list of conflicting files with status indicators
- ✅ Show conflict status for each file (resolved/unresolved)
- ✅ File selection to view conflict details
- ✅ Progress bar showing resolution progress
- ✅ Mark files as resolved/unmark
- ✅ Abort and Continue buttons
- ✅ Validation that all conflicts are resolved before continuing
- ⏳ Side-by-side diff viewer (Task 15.2)
- ⏳ Resolution controls (Accept Ours/Theirs, Manual Edit) (Task 15.3)

**State Management**:

The component maintains:
- `selectedFile`: Currently selected file for viewing details
- `resolvedFiles`: Set of file paths that have been marked as resolved
- `loading`: Loading state for continue operation
- `error`: Error message to display

**Usage Example**:

```jsx
import ConflictResolutionInterface from '@/components/releases/ConflictResolutionInterface';

function MyComponent() {
  const conflicts = [
    {
      file_path: 'src/components/App.jsx',
      conflict_type: 'content',
      our_commit: 'abc123def456',
      their_commit: 'xyz789uvw012'
    }
  ];

  const handleResolve = async (data) => {
    console.log('Resolved files:', data.resolvedFiles);
    // Continue with release creation
  };

  const handleAbort = () => {
    // Cancel release creation
  };

  return (
    <ConflictResolutionInterface
      conflicts={conflicts}
      releaseId="release-123"
      onResolve={handleResolve}
      onAbort={handleAbort}
    />
  );
}
```

**UI Layout**:

The component uses a two-panel layout:

**Left Panel - File List**:
- Lists all conflicting files
- Shows resolution status with icons (✓ resolved, ✗ unresolved)
- Displays conflict type badge
- Shows abbreviated commit hashes
- Highlights selected file
- Scrollable for many files

**Right Panel - File Details**:
- Shows selected file path
- Displays conflict type and resolution status
- Shows commit information (our commit, their commit)
- Placeholder for future diff viewer (Task 15.2)
- Mark as Resolved / Unmark button

**Progress Tracking**:
- Progress bar at the top showing X / Y resolved
- Percentage indicator
- Color changes from yellow to green when all resolved

**Validation**:
- Continue button is disabled until all conflicts are resolved
- Shows error message if user tries to continue with unresolved conflicts
- Confirmation dialog when aborting

**Dependencies**:

- React hooks (useState, useCallback)
- Lucide React icons
- shadcn/ui components (Button, Card, Badge, Alert)

**Notes**:

- Task 15.1 implements the basic UI structure and file list
- Task 15.2 will add the diff viewer with side-by-side comparison
- Task 15.3 will add resolution controls (Accept Ours/Theirs, Manual Edit)
- Task 15.4 will implement actual conflict resolution API integration
- Currently, files can be marked as resolved manually for testing purposes
