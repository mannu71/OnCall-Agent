import React from 'react';
import ConflictResolutionInterface from './ConflictResolutionInterface';

/**
 * Demo component to verify ConflictResolutionInterface works correctly
 * This demonstrates task 15.3 implementation
 */
const ConflictResolutionDemo = () => {
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
    },
    {
      file_path: 'src/services/api.js',
      conflict_type: 'content',
      our_commit: 'ghi789jkl012',
      their_commit: 'rst345vwx678'
    }
  ];

  const handleResolve = async (resolutionData) => {
    console.log('Conflicts resolved:', resolutionData);
    alert('All conflicts resolved successfully! Check console for details.');
  };

  const handleAbort = () => {
    console.log('Release aborted');
    alert('Release creation aborted');
  };

  return (
    <div className="p-8 bg-slate-50 min-h-screen">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900 mb-2">
          Task 15.3: Resolution Controls Demo
        </h1>
        <p className="text-slate-600">
          This demo shows all resolution controls implemented for task 15.3:
        </p>
        <ul className="list-disc list-inside text-sm text-slate-600 mt-2 space-y-1">
          <li>✅ "Accept Ours" button</li>
          <li>✅ "Accept Theirs" button</li>
          <li>✅ Manual edit mode with code editor</li>
          <li>✅ "Mark as Resolved" action (automatic)</li>
          <li>✅ Resolution status tracking for all files</li>
        </ul>
      </div>

      <ConflictResolutionInterface
        conflicts={mockConflicts}
        releaseId="demo-release-123"
        onResolve={handleResolve}
        onAbort={handleAbort}
      />
    </div>
  );
};

export default ConflictResolutionDemo;
