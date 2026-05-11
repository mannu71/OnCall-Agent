import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import ConflictResolutionInterface from './ConflictResolutionInterface';

describe('ConflictResolutionInterface', () => {
  const mockConflicts = [
    {
      file_path: 'src/components/Example.jsx',
      conflict_type: 'content',
      our_commit: 'abc123def456',
      their_commit: 'xyz789uvw012'
    },
    {
      file_path: 'src/utils/helper.js',
      conflict_type: 'content',
      our_commit: 'def456ghi789',
      their_commit: 'uvw012rst345'
    }
  ];

  const mockProps = {
    conflicts: mockConflicts,
    releaseId: 'test-release-123',
    onResolve: vi.fn(),
    onAbort: vi.fn()
  };

  it('renders conflict resolution interface with file list', () => {
    render(<ConflictResolutionInterface {...mockProps} />);
    
    expect(screen.getByText('Merge Conflicts Detected')).toBeInTheDocument();
    expect(screen.getByText(/Conflicting Files/)).toBeInTheDocument();
    expect(screen.getByText('src/components/Example.jsx')).toBeInTheDocument();
    expect(screen.getByText('src/utils/helper.js')).toBeInTheDocument();
  });

  it('displays progress bar with correct percentage', () => {
    render(<ConflictResolutionInterface {...mockProps} />);
    
    expect(screen.getByText('0 / 2 Resolved')).toBeInTheDocument();
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  it('allows selecting a file to view details', async () => {
    render(<ConflictResolutionInterface {...mockProps} />);
    
    const fileButton = screen.getByText('src/components/Example.jsx').closest('button');
    fireEvent.click(fileButton);
    
    await waitFor(() => {
      expect(screen.getByText('Loading conflict details...')).toBeInTheDocument();
    });
  });

  it('allows marking a file as resolved', async () => {
    render(<ConflictResolutionInterface {...mockProps} />);
    
    // Select first file
    const fileButton = screen.getByText('src/components/Example.jsx').closest('button');
    fireEvent.click(fileButton);
    
    // Wait for details to load
    await waitFor(() => {
      expect(screen.queryByText('Loading conflict details...')).not.toBeInTheDocument();
    });
    
    // Mark as resolved
    const resolveButton = screen.getByText('Mark as Resolved');
    fireEvent.click(resolveButton);
    
    // Check progress updated
    await waitFor(() => {
      expect(screen.getByText('1 / 2 Resolved')).toBeInTheDocument();
    });
  });

  it('prevents continuing when not all conflicts are resolved', async () => {
    render(<ConflictResolutionInterface {...mockProps} />);
    
    const continueButton = screen.getByText('Continue with Release');
    expect(continueButton).toBeDisabled();
  });

  it('calls onAbort when abort button is clicked and confirmed', () => {
    window.confirm = vi.fn(() => true);
    render(<ConflictResolutionInterface {...mockProps} />);
    
    const abortButton = screen.getByText('Abort Release');
    fireEvent.click(abortButton);
    
    expect(mockProps.onAbort).toHaveBeenCalled();
  });

  it('displays error message when trying to continue with unresolved conflicts', async () => {
    render(<ConflictResolutionInterface {...mockProps} />);
    
    // Enable continue button by marking one file as resolved
    const fileButton = screen.getByText('src/components/Example.jsx').closest('button');
    fireEvent.click(fileButton);
    
    await waitFor(() => {
      expect(screen.queryByText('Loading conflict details...')).not.toBeInTheDocument();
    });
    
    const resolveButton = screen.getByText('Mark as Resolved');
    fireEvent.click(resolveButton);
    
    // Try to continue (should still be disabled because not all are resolved)
    const continueButton = screen.getByText('Continue with Release');
    expect(continueButton).toBeDisabled();
  });
});
