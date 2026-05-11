import React, { useState, useCallback, useEffect } from 'react';
import PropTypes from 'prop-types';
import {
  AlertTriangle,
  FileCode,
  CheckCircle2,
  XCircle,
  ChevronRight,
  Loader2
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import ConflictDiffViewer from './ConflictDiffViewer';
import agentApiClient from '@/services/agentApiClient';

/**
 * ConflictResolutionInterface Component
 * 
 * Interactive UI for resolving merge conflicts during release creation
 * 
 * Features:
 * - Display list of conflicting files
 * - Show conflict status for each file
 * - File selection to view details
 * - Abort and Continue buttons
 */

const ConflictResolutionInterface = ({ conflicts, releaseId, onResolve, onAbort }) => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [resolvedFiles, setResolvedFiles] = useState(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [conflictDetails, setConflictDetails] = useState(null);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [resolutions, setResolutions] = useState(new Map()); // Map<filePath, {type: 'ours'|'theirs'|'manual', content: string}>
  const [manualEditMode, setManualEditMode] = useState(false);
  const [editedContent, setEditedContent] = useState('');

  // Fetch conflict details when a file is selected
  useEffect(() => {
    const fetchConflictDetails = async () => {
      if (!selectedFile) {
        setConflictDetails(null);
        return;
      }

      setLoadingDetails(true);
      setError(null);

      try {
        // TODO: Replace with actual API call to fetch conflict details
        // For now, using mock data structure
        // const response = await axios.get(`/api/releases/${releaseId}/conflicts/${encodeURIComponent(selectedFile.file_path)}`);
        
        // Mock conflict details - in real implementation, this would come from the API
        const mockDetails = {
          file_path: selectedFile.file_path,
          conflict_type: selectedFile.conflict_type,
          our_commit: selectedFile.our_commit,
          their_commit: selectedFile.their_commit,
          our_commit_message: 'Current branch changes',
          their_commit_message: 'Incoming changes from cherry-pick',
          ours_content: generateMockContent(selectedFile.file_path, 'ours'),
          theirs_content: generateMockContent(selectedFile.file_path, 'theirs')
        };

        setConflictDetails(mockDetails);
      } catch (err) {
        console.error('Error fetching conflict details:', err);
        setError('Failed to load conflict details. Please try again.');
      } finally {
        setLoadingDetails(false);
      }
    };

    fetchConflictDetails();
  }, [selectedFile, releaseId]);

  // Generate mock content for demonstration
  // In real implementation, this would come from the Git conflict resolver API
  const generateMockContent = (filePath, version) => {
    const fileName = filePath.split('/').pop();
    const isOurs = version === 'ours';
    
    return `// ${fileName}
// ${isOurs ? 'Current Branch Version' : 'Incoming Changes Version'}

function exampleFunction() {
  ${isOurs ? '// Current implementation' : '// New implementation'}
  const value = ${isOurs ? '42' : '100'};
  
<<<<<<< HEAD
  // This is our version
  console.log('Current branch: ' + value);
  return value * 2;
=======
  // This is their version
  console.log('Incoming changes: ' + value);
  return value * 3;
>>>>>>> ${version === 'theirs' ? 'abc123' : 'HEAD'}
  
  // More code here...
}

export default exampleFunction;`;
  };

  // Select a file to view details
  const handleFileSelect = useCallback((conflict) => {
    setSelectedFile(conflict);
    setError(null);
  }, []);

  // Handle "Accept Ours" resolution
  const handleAcceptOurs = useCallback(() => {
    if (!selectedFile || !conflictDetails) return;
    
    const filePath = selectedFile.file_path;
    setResolutions(prev => {
      const newMap = new Map(prev);
      newMap.set(filePath, {
        type: 'ours',
        content: conflictDetails.ours_content
      });
      return newMap;
    });
    
    // Mark as resolved
    setResolvedFiles(prev => {
      const newSet = new Set(prev);
      newSet.add(filePath);
      return newSet;
    });
    
    setManualEditMode(false);
    setError(null);
  }, [selectedFile, conflictDetails]);

  // Handle "Accept Theirs" resolution
  const handleAcceptTheirs = useCallback(() => {
    if (!selectedFile || !conflictDetails) return;
    
    const filePath = selectedFile.file_path;
    setResolutions(prev => {
      const newMap = new Map(prev);
      newMap.set(filePath, {
        type: 'theirs',
        content: conflictDetails.theirs_content
      });
      return newMap;
    });
    
    // Mark as resolved
    setResolvedFiles(prev => {
      const newSet = new Set(prev);
      newSet.add(filePath);
      return newSet;
    });
    
    setManualEditMode(false);
    setError(null);
  }, [selectedFile, conflictDetails]);

  // Handle manual edit mode toggle
  const handleEnableManualEdit = useCallback(() => {
    if (!selectedFile || !conflictDetails) return;
    
    setManualEditMode(true);
    // Initialize with theirs content or existing resolution
    const existingResolution = resolutions.get(selectedFile.file_path);
    setEditedContent(existingResolution?.content || conflictDetails.theirs_content || '');
  }, [selectedFile, conflictDetails, resolutions]);

  // Handle manual edit save
  const handleSaveManualEdit = useCallback(() => {
    if (!selectedFile) return;
    
    const filePath = selectedFile.file_path;
    setResolutions(prev => {
      const newMap = new Map(prev);
      newMap.set(filePath, {
        type: 'manual',
        content: editedContent
      });
      return newMap;
    });
    
    // Mark as resolved
    setResolvedFiles(prev => {
      const newSet = new Set(prev);
      newSet.add(filePath);
      return newSet;
    });
    
    setManualEditMode(false);
    setError(null);
  }, [selectedFile, editedContent]);

  // Handle manual edit cancel
  const handleCancelManualEdit = useCallback(() => {
    setManualEditMode(false);
    setEditedContent('');
  }, []);

  // Mark a file as resolved (placeholder for future implementation)
  const handleMarkResolved = useCallback((filePath) => {
    setResolvedFiles(prev => {
      const newSet = new Set(prev);
      newSet.add(filePath);
      return newSet;
    });
    setError(null);
  }, []);

  // Handle continue (all conflicts must be resolved)
  const handleContinue = useCallback(async () => {
    // Validate all conflicts are resolved
    const unresolvedFiles = conflicts.filter(c => !resolvedFiles.has(c.file_path));
    
    if (unresolvedFiles.length > 0) {
      setError(`Please resolve all conflicts before continuing. ${unresolvedFiles.length} file(s) remaining.`);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // Submit each resolution to the API
      const resolutionPromises = Array.from(resolutions.entries()).map(([filePath, resolution]) => {
        return agentApiClient.resolveConflict(releaseId, {
          file_path: filePath,
          resolution_type: resolution.type,
          content: resolution.type === 'manual' ? resolution.content : undefined
        });
      });

      // Wait for all resolutions to complete
      await Promise.all(resolutionPromises);

      // Call the onResolve callback with resolution data
      await onResolve({
        releaseId,
        resolvedFiles: Array.from(resolvedFiles),
        resolutions: Array.from(resolutions.entries()).map(([filePath, resolution]) => ({
          file_path: filePath,
          resolution_type: resolution.type,
          content: resolution.type === 'manual' ? resolution.content : undefined
        }))
      });
    } catch (err) {
      console.error('Error submitting conflict resolutions:', err);
      const errorMessage = err.response?.data?.message || err.message || 'Failed to submit conflict resolutions';
      setError(`Failed to submit resolutions: ${errorMessage}`);
    } finally {
      setLoading(false);
    }
  }, [conflicts, resolvedFiles, resolutions, releaseId, onResolve]);

  // Handle abort
  const handleAbort = useCallback(async () => {
    if (!globalThis.confirm('Are you sure you want to abort? The release will not be created.')) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      if (releaseId) {
        await agentApiClient.abortRelease(releaseId);
      }
    } catch (err) {
      console.error('Error aborting release:', err);
    } finally {
      setLoading(false);
      onAbort();
    }
  }, [onAbort, releaseId]);

  // Calculate resolution progress
  const totalFiles = conflicts.length;
  const resolvedCount = resolvedFiles.size;
  const progressPercent = totalFiles > 0 ? Math.round((resolvedCount / totalFiles) * 100) : 0;

  return (
    <Card className="max-w-6xl mx-auto">
      <CardHeader className="border-b border-slate-100">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-xl font-bold text-slate-900 flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-yellow-600" />
              Merge Conflicts Detected
            </CardTitle>
            <CardDescription className="mt-1">
              Resolve conflicts to continue with release creation
            </CardDescription>
          </div>
          <Badge variant={resolvedCount === totalFiles ? 'success' : 'warning'} className="text-sm">
            {resolvedCount} / {totalFiles} Resolved
          </Badge>
        </div>

        {/* Progress Bar */}
        <div className="mt-4">
          <div className="flex items-center justify-between text-sm text-slate-600 mb-2">
            <span>Resolution Progress</span>
            <span>{progressPercent}%</span>
          </div>
          <div className="w-full bg-slate-200 rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all duration-300 ${
                resolvedCount === totalFiles ? 'bg-green-600' : 'bg-yellow-600'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
      </CardHeader>

      <CardContent className="p-6">
        {/* Error Display */}
        {error && (
          <Alert className="mb-6 border-red-200 bg-red-50">
            <AlertTriangle className="h-4 w-4 text-red-600" />
            <AlertDescription className="text-red-800">
              {error}
            </AlertDescription>
          </Alert>
        )}

        {/* Info Alert */}
        <Alert className="mb-6 border-blue-200 bg-blue-50">
          <AlertDescription className="text-blue-800">
            <p className="font-semibold mb-2">What are merge conflicts?</p>
            <p className="text-sm">
              Merge conflicts occur when the same lines of code have been modified in different commits.
              You need to review each conflicting file and decide how to resolve the conflicts before continuing.
            </p>
          </AlertDescription>
        </Alert>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left Panel: File List */}
          <div>
            <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
              <FileCode className="w-4 h-4" />
              Conflicting Files ({totalFiles})
            </h3>
            
            <div className="space-y-2 max-h-[500px] overflow-y-auto">
              {conflicts.map((conflict) => {
                const isResolved = resolvedFiles.has(conflict.file_path);
                const isSelected = selectedFile?.file_path === conflict.file_path;
                
                return (
                  <button
                    key={conflict.file_path}
                    onClick={() => handleFileSelect(conflict)}
                    className={`
                      w-full text-left p-4 border rounded-lg transition-all
                      ${isSelected ? 'border-red-500 bg-red-50' : 'border-slate-200 hover:border-slate-300'}
                      ${isResolved ? 'bg-green-50 border-green-300' : ''}
                    `}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          {isResolved ? (
                            <CheckCircle2 className="w-4 h-4 text-green-600 flex-shrink-0" />
                          ) : (
                            <XCircle className="w-4 h-4 text-yellow-600 flex-shrink-0" />
                          )}
                          <code className="text-sm font-mono text-slate-900 truncate">
                            {conflict.file_path}
                          </code>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-slate-500">
                          <Badge variant="outline" className="text-xs">
                            {conflict.conflict_type || 'content'}
                          </Badge>
                          <span>•</span>
                          <span className="truncate">
                            {conflict.our_commit?.substring(0, 7)} ↔ {conflict.their_commit?.substring(0, 7)}
                          </span>
                        </div>
                      </div>
                      <ChevronRight className={`w-4 h-4 flex-shrink-0 ${isSelected ? 'text-red-600' : 'text-slate-400'}`} />
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right Panel: File Details */}
          <div>
            <h3 className="text-sm font-semibold text-slate-700 mb-3">
              Conflict Details
            </h3>
            
            {selectedFile ? (
              <div className="space-y-4">
                {loadingDetails ? (
                  <div className="flex items-center justify-center h-[400px] border border-slate-200 rounded-lg">
                    <div className="text-center">
                      <Loader2 className="w-8 h-8 mx-auto mb-3 text-slate-400 animate-spin" />
                      <p className="text-sm text-slate-500">Loading conflict details...</p>
                    </div>
                  </div>
                ) : conflictDetails ? (
                  <>
                    {/* Conflict Diff Viewer */}
                    <ConflictDiffViewer
                      conflict={conflictDetails}
                      oursContent={conflictDetails.ours_content}
                      theirsContent={conflictDetails.theirs_content}
                      manualEditMode={manualEditMode}
                      editedContent={editedContent}
                      onContentChange={(newContent) => {
                        setEditedContent(newContent);
                      }}
                    />

                    {/* Resolution Status */}
                    <Card className="border-slate-200">
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm font-semibold text-slate-900">
                          Resolution Controls
                        </CardTitle>
                      </CardHeader>
                      <CardContent>
                        {resolvedFiles.has(selectedFile.file_path) ? (
                          <div className="space-y-4">
                            <div className="flex items-center gap-2 text-green-700">
                              <CheckCircle2 className="w-4 h-4" />
                              <span className="text-sm font-semibold">This file has been resolved</span>
                            </div>
                            
                            {/* Show resolution type */}
                            {resolutions.has(selectedFile.file_path) && (
                              <div className="text-xs text-slate-600 bg-slate-50 p-3 rounded border border-slate-200">
                                <span className="font-semibold">Resolution type: </span>
                                <Badge variant="outline" className="ml-2">
                                  {resolutions.get(selectedFile.file_path).type === 'ours' && 'Accept Ours'}
                                  {resolutions.get(selectedFile.file_path).type === 'theirs' && 'Accept Theirs'}
                                  {resolutions.get(selectedFile.file_path).type === 'manual' && 'Manual Edit'}
                                </Badge>
                              </div>
                            )}
                            
                            <Button
                              onClick={() => {
                                setResolvedFiles(prev => {
                                  const newSet = new Set(prev);
                                  newSet.delete(selectedFile.file_path);
                                  return newSet;
                                });
                                setResolutions(prev => {
                                  const newMap = new Map(prev);
                                  newMap.delete(selectedFile.file_path);
                                  return newMap;
                                });
                                setManualEditMode(false);
                              }}
                              variant="outline"
                              size="sm"
                            >
                              <XCircle className="w-4 h-4 mr-2" />
                              Change Resolution
                            </Button>
                          </div>
                        ) : (
                          <div className="space-y-4">
                            <div className="flex items-center gap-2 text-yellow-700 mb-4">
                              <AlertTriangle className="w-4 h-4" />
                              <span className="text-sm font-semibold">Choose a resolution method</span>
                            </div>
                            
                            {!manualEditMode ? (
                              <>
                                {/* Quick Resolution Buttons */}
                                <div className="space-y-2">
                                  <Button
                                    onClick={handleAcceptOurs}
                                    className="w-full bg-blue-600 hover:bg-blue-700 text-white"
                                    size="sm"
                                  >
                                    Accept Ours (Current Branch)
                                  </Button>
                                  
                                  <Button
                                    onClick={handleAcceptTheirs}
                                    className="w-full bg-green-600 hover:bg-green-700 text-white"
                                    size="sm"
                                  >
                                    Accept Theirs (Incoming Changes)
                                  </Button>
                                  
                                  <Button
                                    onClick={handleEnableManualEdit}
                                    variant="outline"
                                    className="w-full"
                                    size="sm"
                                  >
                                    Manual Edit
                                  </Button>
                                </div>
                                
                                <div className="text-xs text-slate-500 bg-slate-50 p-3 rounded border border-slate-200">
                                  <p className="font-semibold mb-1">Resolution Options:</p>
                                  <ul className="space-y-1 ml-4 list-disc">
                                    <li><strong>Accept Ours:</strong> Keep current branch version</li>
                                    <li><strong>Accept Theirs:</strong> Use incoming changes</li>
                                    <li><strong>Manual Edit:</strong> Combine or modify both versions</li>
                                  </ul>
                                </div>
                              </>
                            ) : (
                              <>
                                {/* Manual Edit Mode */}
                                <Alert className="border-blue-200 bg-blue-50">
                                  <AlertDescription className="text-blue-800 text-xs">
                                    <p className="font-semibold mb-1">Manual Edit Mode</p>
                                    <p>Edit the content in the diff viewer above. When finished, click "Save Manual Edit" to apply your changes.</p>
                                  </AlertDescription>
                                </Alert>
                                
                                <div className="flex gap-2">
                                  <Button
                                    onClick={handleSaveManualEdit}
                                    className="flex-1 bg-green-600 hover:bg-green-700 text-white"
                                    size="sm"
                                  >
                                    <CheckCircle2 className="w-4 h-4 mr-2" />
                                    Save Manual Edit
                                  </Button>
                                  
                                  <Button
                                    onClick={handleCancelManualEdit}
                                    variant="outline"
                                    size="sm"
                                  >
                                    Cancel
                                  </Button>
                                </div>
                              </>
                            )}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  </>
                ) : (
                  <Alert className="border-red-200 bg-red-50">
                    <AlertTriangle className="h-4 w-4 text-red-600" />
                    <AlertDescription className="text-red-800">
                      Failed to load conflict details. Please try selecting the file again.
                    </AlertDescription>
                  </Alert>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center h-[400px] border-2 border-dashed border-slate-200 rounded-lg">
                <div className="text-center text-slate-500">
                  <FileCode className="w-12 h-12 mx-auto mb-3 text-slate-400" />
                  <p className="text-sm">Select a file to view conflict details</p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex justify-between mt-8 pt-6 border-t border-slate-100">
          <Button
            variant="outline"
            onClick={handleAbort}
            disabled={loading}
            className="text-red-600 border-red-300 hover:bg-red-50"
          >
            <XCircle className="w-4 h-4 mr-2" />
            Abort Release
          </Button>

          <Button
            onClick={handleContinue}
            disabled={loading || resolvedCount < totalFiles}
            className="bg-red-600 hover:bg-red-700 text-white"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Processing...
              </>
            ) : (
              <>
                <CheckCircle2 className="w-4 h-4 mr-2" />
                Continue with Release
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};

ConflictResolutionInterface.propTypes = {
  conflicts: PropTypes.arrayOf(
    PropTypes.shape({
      file_path: PropTypes.string.isRequired,
      conflict_type: PropTypes.string,
      our_commit: PropTypes.string,
      their_commit: PropTypes.string
    })
  ).isRequired,
  releaseId: PropTypes.string.isRequired,
  onResolve: PropTypes.func.isRequired,
  onAbort: PropTypes.func.isRequired
};

export default ConflictResolutionInterface;
