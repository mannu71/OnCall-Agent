import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';
import { DiffEditor } from '@monaco-editor/react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { GitCommit, AlertTriangle } from 'lucide-react';

/**
 * ConflictDiffViewer Component
 * 
 * Displays side-by-side diff viewer for merge conflicts using Monaco Editor
 * 
 * Features:
 * - Side-by-side diff with "ours" and "theirs"
 * - Syntax highlighting
 * - Conflict marker highlighting
 * - Commit information display
 * - Manual edit mode for custom resolution
 */

const ConflictDiffViewer = ({ conflict, oursContent, theirsContent, manualEditMode, editedContent, onContentChange }) => {
  const [modifiedContent, setModifiedContent] = useState(editedContent || theirsContent);
  const [conflictMarkers, setConflictMarkers] = useState([]);

  // Update modified content when editedContent or theirsContent changes
  useEffect(() => {
    setModifiedContent(editedContent || theirsContent);
  }, [editedContent, theirsContent]);

  // Parse conflict markers from content
  useEffect(() => {
    const markers = parseConflictMarkers(oursContent);
    setConflictMarkers(markers);
  }, [oursContent]);

  // Parse conflict markers (<<<<<<, =======, >>>>>>>)
  const parseConflictMarkers = (content) => {
    if (!content) return [];
    
    const lines = content.split('\n');
    const markers = [];
    let inConflict = false;
    let conflictStart = -1;
    let separatorLine = -1;

    lines.forEach((line, index) => {
      if (line.startsWith('<<<<<<<')) {
        inConflict = true;
        conflictStart = index;
      } else if (line.startsWith('=======') && inConflict) {
        separatorLine = index;
      } else if (line.startsWith('>>>>>>>') && inConflict) {
        markers.push({
          start: conflictStart,
          separator: separatorLine,
          end: index,
          oursStart: conflictStart + 1,
          oursEnd: separatorLine - 1,
          theirsStart: separatorLine + 1,
          theirsEnd: index - 1
        });
        inConflict = false;
        conflictStart = -1;
        separatorLine = -1;
      }
    });

    return markers;
  };

  // Handle editor content change
  const handleEditorChange = (value) => {
    setModifiedContent(value);
    if (onContentChange) {
      onContentChange(value);
    }
  };

  // Monaco editor options
  const editorOptions = {
    readOnly: !manualEditMode, // Enable editing only in manual edit mode
    minimap: { enabled: true },
    scrollBeyondLastLine: false,
    fontSize: 13,
    lineNumbers: 'on',
    renderSideBySide: true,
    ignoreTrimWhitespace: false,
    renderOverviewRuler: true,
    wordWrap: 'off',
    automaticLayout: true
  };

  return (
    <div className="space-y-4">
      {/* Commit Information */}
      <div className="grid grid-cols-2 gap-4">
        <Card className="border-blue-200 bg-blue-50">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold text-blue-900 flex items-center gap-2">
              <GitCommit className="w-4 h-4" />
              Our Changes (Current Branch)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              <div className="text-xs">
                <span className="font-semibold text-blue-800">Commit:</span>
                <code className="ml-2 text-xs font-mono bg-blue-100 px-2 py-0.5 rounded">
                  {conflict.our_commit?.substring(0, 7) || 'N/A'}
                </code>
              </div>
              {conflict.our_commit_message && (
                <div className="text-xs text-blue-700 mt-2">
                  {conflict.our_commit_message}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="border-green-200 bg-green-50">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold text-green-900 flex items-center gap-2">
              <GitCommit className="w-4 h-4" />
              Their Changes (Incoming)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              <div className="text-xs">
                <span className="font-semibold text-green-800">Commit:</span>
                <code className="ml-2 text-xs font-mono bg-green-100 px-2 py-0.5 rounded">
                  {conflict.their_commit?.substring(0, 7) || 'N/A'}
                </code>
              </div>
              {conflict.their_commit_message && (
                <div className="text-xs text-green-700 mt-2">
                  {conflict.their_commit_message}
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Conflict Markers Alert */}
      {conflictMarkers.length > 0 && (
        <Alert className="border-yellow-200 bg-yellow-50">
          <AlertTriangle className="h-4 w-4 text-yellow-600" />
          <AlertDescription className="text-yellow-800">
            <p className="font-semibold mb-1">
              {conflictMarkers.length} conflict region{conflictMarkers.length > 1 ? 's' : ''} detected
            </p>
            <p className="text-xs">
              Conflict markers are highlighted in the diff viewer. Review the changes and choose the appropriate resolution.
            </p>
          </AlertDescription>
        </Alert>
      )}

      {/* File Path */}
      <div className="flex items-center gap-2 text-sm text-slate-700">
        <span className="font-semibold">File:</span>
        <code className="font-mono bg-slate-100 px-2 py-1 rounded text-xs">
          {conflict.file_path}
        </code>
        <Badge variant="outline" className="text-xs">
          {conflict.conflict_type || 'content'}
        </Badge>
      </div>

      {/* Monaco Diff Editor */}
      <div className="border border-slate-200 rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-4 py-2 border-b border-slate-200">
          <div className="flex items-center justify-between text-xs text-slate-600">
            <span className="font-semibold">
              {manualEditMode ? 'Manual Edit Mode - Edit Right Side' : 'Side-by-Side Comparison'}
            </span>
            <span>
              {manualEditMode ? 'Left: Reference | Right: Editable' : 'Left: Current Branch | Right: Incoming Changes'}
            </span>
          </div>
        </div>
        <div className="bg-white" style={{ height: '500px' }}>
          <DiffEditor
            original={oursContent || '// No content available'}
            modified={modifiedContent || '// No content available'}
            language={getLanguageFromFilePath(conflict.file_path)}
            theme="vs-light"
            options={editorOptions}
            onChange={handleEditorChange}
          />
        </div>
        {manualEditMode && (
          <div className="bg-blue-50 px-4 py-2 border-t border-blue-200">
            <p className="text-xs text-blue-800">
              <strong>Tip:</strong> Edit the right side to create your custom resolution. You can combine parts from both versions or write entirely new code.
            </p>
          </div>
        )}
      </div>

      {/* Conflict Markers Legend */}
      {conflictMarkers.length > 0 && (
        <Card className="border-slate-200 bg-slate-50">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold text-slate-900">
              Conflict Markers Guide
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2 text-xs text-slate-700">
              <div className="flex items-start gap-2">
                <code className="font-mono bg-blue-100 px-2 py-0.5 rounded text-blue-800 whitespace-nowrap">
                  {'<<<<<<< HEAD'}
                </code>
                <span>Marks the start of your current branch changes</span>
              </div>
              <div className="flex items-start gap-2">
                <code className="font-mono bg-slate-200 px-2 py-0.5 rounded whitespace-nowrap">
                  =======
                </code>
                <span>Separates your changes from incoming changes</span>
              </div>
              <div className="flex items-start gap-2">
                <code className="font-mono bg-green-100 px-2 py-0.5 rounded text-green-800 whitespace-nowrap">
                  {'>>>>>>> commit'}
                </code>
                <span>Marks the end of incoming changes</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

// Helper function to determine language from file path
const getLanguageFromFilePath = (filePath) => {
  if (!filePath) return 'plaintext';
  
  const extension = filePath.split('.').pop().toLowerCase();
  
  const languageMap = {
    'js': 'javascript',
    'jsx': 'javascript',
    'ts': 'typescript',
    'tsx': 'typescript',
    'py': 'python',
    'java': 'java',
    'cs': 'csharp',
    'cpp': 'cpp',
    'c': 'c',
    'h': 'cpp',
    'hpp': 'cpp',
    'go': 'go',
    'rs': 'rust',
    'rb': 'ruby',
    'php': 'php',
    'html': 'html',
    'css': 'css',
    'scss': 'scss',
    'json': 'json',
    'xml': 'xml',
    'yaml': 'yaml',
    'yml': 'yaml',
    'md': 'markdown',
    'sql': 'sql',
    'sh': 'shell',
    'bash': 'shell',
    'ps1': 'powershell',
    'dockerfile': 'dockerfile'
  };
  
  return languageMap[extension] || 'plaintext';
};

ConflictDiffViewer.propTypes = {
  conflict: PropTypes.shape({
    file_path: PropTypes.string.isRequired,
    conflict_type: PropTypes.string,
    our_commit: PropTypes.string,
    their_commit: PropTypes.string,
    our_commit_message: PropTypes.string,
    their_commit_message: PropTypes.string
  }).isRequired,
  oursContent: PropTypes.string,
  theirsContent: PropTypes.string,
  manualEditMode: PropTypes.bool,
  editedContent: PropTypes.string,
  onContentChange: PropTypes.func
};

ConflictDiffViewer.defaultProps = {
  oursContent: '',
  theirsContent: '',
  manualEditMode: false,
  editedContent: '',
  onContentChange: null
};

export default ConflictDiffViewer;
