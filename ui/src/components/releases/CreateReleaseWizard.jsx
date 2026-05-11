import React, { useState, useCallback, useMemo } from 'react';
import PropTypes from 'prop-types';
import {
  Building2,
  Tags,
  Hash,
  FileText,
  GitCommit,
  GitBranch,
  CheckCircle2,
  AlertTriangle,
  ChevronRight,
  ChevronLeft,
  X,
  Loader2
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import agentApiClient from '@/services/agentApiClient';
import ConflictResolutionInterface from './ConflictResolutionInterface';

/**
 * CreateReleaseWizard Component
 * 
 * Multi-step wizard for creating Azure DevOps releases
 * 
 * Steps:
 * 1. Organization and Project Selection
 * 2. Work Item Selection (tags or PBI numbers)
 * 3. Work Item Review (with validation warnings)
 * 4. Commit Selection (grouped by PBI)
 * 5. Branch Configuration (release name and branch name)
 * 6. Release Creation (with progress indicator)
 */

const STEPS = [
  { id: 1, title: 'Organization & Project', icon: Building2 },
  { id: 2, title: 'Work Item Selection', icon: Tags },
  { id: 3, title: 'Work Item Review', icon: FileText },
  { id: 4, title: 'Commit Selection', icon: GitCommit },
  { id: 5, title: 'Branch Configuration', icon: GitBranch },
  { id: 6, title: 'Create Release', icon: CheckCircle2 }
];

const CreateReleaseWizard = ({ onComplete, onCancel }) => {
  // Wizard state
  const [currentStep, setCurrentStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Form data state
  const [formData, setFormData] = useState({
    organization: '',
    project: '',
    searchType: 'tags', // 'tags' or 'pbi_numbers'
    tags: '',
    pbiNumbers: '',
    workItems: [],
    selectedWorkItemIds: [],
    commits: {},
    selectedCommitIds: [],
    releaseName: '',
    branchName: '',
    baseBranch: 'main',
    wikiId: ''
  });

  // Validation warnings
  const [validationWarnings, setValidationWarnings] = useState([]);

  // Release result after successful creation
  const [releaseResult, setReleaseResult] = useState(null);

  // Conflict state when merge conflicts are detected (releaseId + conflicts array)
  const [conflictState, setConflictState] = useState(null);

  // Update form field
  const updateField = useCallback((field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }));
    setError(null);
  }, []);

  // Grouped commits for Step 4 — grouped by repository (must be at component level — Rules of Hooks)
  const groupedCommits = useMemo(() => {
    const groups = {};
    formData.selectedWorkItemIds.forEach(workItemId => {
      const commits = formData.commits[workItemId] || [];
      commits.forEach(commit => {
        const key = commit.repository_name || commit.repository_id || 'unknown';
        if (!groups[key]) {
          groups[key] = { repositoryName: key, commits: [] };
        }
        // Avoid duplicates (same commit linked to multiple work items)
        if (!groups[key].commits.find(c => c.commit_id === commit.commit_id)) {
          groups[key].commits.push(commit);
        }
      });
    });
    // Sort commits within each repo by date descending
    Object.values(groups).forEach(g => {
      g.commits.sort((a, b) => new Date(b.commit_date) - new Date(a.commit_date));
    });
    return groups;
  }, [formData.selectedWorkItemIds, formData.commits]);

  const allCommitIds = useMemo(() => {
    return Object.values(groupedCommits).flatMap(g => g.commits.map(c => c.commit_id));
  }, [groupedCommits]);

  // Step 1: Organization and Project Selection
  const renderStep1 = () => (
    <div className="space-y-6">
      <div>
        <Label htmlFor="organization" className="text-sm font-medium mb-2 block">
          Azure DevOps Organization <span className="text-red-500">*</span>
        </Label>
        <Input
          id="organization"
          placeholder="e.g., myorganization"
          value={formData.organization}
          onChange={(e) => updateField('organization', e.target.value)}
          className="w-full"
        />
        <p className="text-xs text-slate-500 mt-1">
          The organization name from your Azure DevOps URL
        </p>
      </div>

      <div>
        <Label htmlFor="project" className="text-sm font-medium mb-2 block">
          Project Name <span className="text-red-500">*</span>
        </Label>
        <Input
          id="project"
          placeholder="e.g., MyProject"
          value={formData.project}
          onChange={(e) => updateField('project', e.target.value)}
          className="w-full"
        />
        <p className="text-xs text-slate-500 mt-1">
          The project name within your organization
        </p>
      </div>

      <div>
        <Label htmlFor="wikiId" className="text-sm font-medium mb-2 block">
          Wiki Parent Page ID <span className="text-red-500">*</span>
        </Label>
        <Input
          id="wikiId"
          placeholder="e.g., 31155 or Compliance.wiki"
          value={formData.wikiId}
          onChange={(e) => updateField('wikiId', e.target.value)}
          className="w-full"
        />
        <p className="text-xs text-slate-500 mt-1">
          Enter a page ID (number from the wiki URL) to create release pages as sub-pages under it, or a wiki name like <code>Compliance.wiki</code> to create at the root.
        </p>
      </div>
    </div>
  );

  // Step 2: Work Item Selection
  const renderStep2 = () => (
    <div className="space-y-6">
      <div>
        <Label className="text-sm font-medium mb-3 block">
          Search Method <span className="text-red-500">*</span>
        </Label>
        <div className="flex gap-4">
          <label className="flex items-center space-x-2 cursor-pointer">
            <input
              type="radio"
              name="searchType"
              value="tags"
              checked={formData.searchType === 'tags'}
              onChange={(e) => updateField('searchType', e.target.value)}
              className="w-4 h-4 text-red-600"
            />
            <span className="text-sm">Search by Tags</span>
          </label>
          <label className="flex items-center space-x-2 cursor-pointer">
            <input
              type="radio"
              name="searchType"
              value="pbi_numbers"
              checked={formData.searchType === 'pbi_numbers'}
              onChange={(e) => updateField('searchType', e.target.value)}
              className="w-4 h-4 text-red-600"
            />
            <span className="text-sm">Search by PBI Numbers</span>
          </label>
        </div>
      </div>

      {formData.searchType === 'tags' ? (
        <div>
          <Label htmlFor="tags" className="text-sm font-medium mb-2 block">
            Tags <span className="text-red-500">*</span>
          </Label>
          <Input
            id="tags"
            placeholder="e.g., release-1.0, feature-auth"
            value={formData.tags}
            onChange={(e) => updateField('tags', e.target.value)}
            className="w-full"
          />
          <p className="text-xs text-slate-500 mt-1">
            Enter tags separated by commas. Work items matching any tag will be included.
          </p>
        </div>
      ) : (
        <div>
          <Label htmlFor="pbiNumbers" className="text-sm font-medium mb-2 block">
            PBI Numbers <span className="text-red-500">*</span>
          </Label>
          <Input
            id="pbiNumbers"
            placeholder="e.g., 12345, 12346, 12347"
            value={formData.pbiNumbers}
            onChange={(e) => updateField('pbiNumbers', e.target.value)}
            className="w-full"
          />
          <p className="text-xs text-slate-500 mt-1">
            Enter PBI numbers separated by commas
          </p>
        </div>
      )}
    </div>
  );

  // Step 3: Work Item Review
  const renderStep3 = () => (
    <div className="space-y-4">
      {validationWarnings.length > 0 && (
        <Alert className="border-yellow-200 bg-yellow-50">
          <AlertTriangle className="h-4 w-4 text-yellow-600" />
          <AlertDescription className="text-yellow-800">
            <p className="font-semibold mb-2">Validation Warnings:</p>
            <ul className="list-disc list-inside space-y-1 text-sm">
              {validationWarnings.map((warning, idx) => (
                <li key={idx}>
                  PBI #{warning.work_item_id}: {warning.message} (Current state: {warning.current_state})
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <div className="border rounded-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase">
                <input
                  type="checkbox"
                  checked={formData.selectedWorkItemIds.length === formData.workItems.length}
                  onChange={(e) => {
                    if (e.target.checked) {
                      updateField('selectedWorkItemIds', formData.workItems.map(wi => wi.id));
                    } else {
                      updateField('selectedWorkItemIds', []);
                    }
                  }}
                  className="w-4 h-4"
                />
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase">ID</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase">Title</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase">State</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-slate-600 uppercase">Tags</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {formData.workItems.map((workItem) => {
              const isNonClosed = workItem.state !== 'Closed';
              return (
                <tr
                  key={workItem.id}
                  className={`hover:bg-slate-50 ${isNonClosed ? 'bg-yellow-50' : ''}`}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={formData.selectedWorkItemIds.includes(workItem.id)}
                      onChange={(e) => {
                        if (e.target.checked) {
                          updateField('selectedWorkItemIds', [...formData.selectedWorkItemIds, workItem.id]);
                        } else {
                          updateField('selectedWorkItemIds', formData.selectedWorkItemIds.filter(id => id !== workItem.id));
                        }
                      }}
                      className="w-4 h-4"
                    />
                  </td>
                  <td className="px-4 py-3 font-mono text-sm">#{workItem.id}</td>
                  <td className="px-4 py-3 text-sm">{workItem.title}</td>
                  <td className="px-4 py-3">
                    <Badge variant={isNonClosed ? 'warning' : 'success'} className="text-xs">
                      {workItem.state}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {workItem.tags?.map((tag, idx) => (
                        <Badge key={idx} variant="outline" className="text-xs">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between items-center text-sm text-slate-600">
        <span>{formData.selectedWorkItemIds.length} of {formData.workItems.length} work items selected</span>
        {validationWarnings.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              const nonClosedIds = validationWarnings.map(w => w.work_item_id);
              updateField('selectedWorkItemIds', formData.selectedWorkItemIds.filter(id => !nonClosedIds.includes(id)));
            }}
          >
            Remove Non-Closed PBIs
          </Button>
        )}
      </div>
    </div>
  );

  // Step 4: Commit Selection (grouped by repository)
  const renderStep4 = () => {
    return (
      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <p className="text-sm text-slate-600">
            {formData.selectedCommitIds.length} of {allCommitIds.length} commits selected
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => updateField('selectedCommitIds', allCommitIds)}
            >
              Select All
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => updateField('selectedCommitIds', [])}
            >
              Deselect All
            </Button>
          </div>
        </div>

        {Object.keys(groupedCommits).length === 0 ? (
          <p className="text-sm text-slate-500 italic text-center py-8">No commits found for the selected work items</p>
        ) : (
          <div className="space-y-6">
            {Object.entries(groupedCommits).map(([repoName, { commits }]) => {
              const repoCommitIds = commits.map(c => c.commit_id);
              const allRepoSelected = repoCommitIds.every(id => formData.selectedCommitIds.includes(id));
              const someRepoSelected = repoCommitIds.some(id => formData.selectedCommitIds.includes(id));

              return (
                <Card key={repoName}>
                  <CardHeader className="pb-3">
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        checked={allRepoSelected}
                        ref={el => { if (el) el.indeterminate = someRepoSelected && !allRepoSelected; }}
                        onChange={(e) => {
                          if (e.target.checked) {
                            const next = new Set(formData.selectedCommitIds);
                            repoCommitIds.forEach(id => next.add(id));
                            updateField('selectedCommitIds', [...next]);
                          } else {
                            updateField('selectedCommitIds', formData.selectedCommitIds.filter(id => !repoCommitIds.includes(id)));
                          }
                        }}
                        className="w-4 h-4"
                        aria-label={`Select all commits in repository ${repoName}`}
                      />
                      <div>
                        <CardTitle className="text-base flex items-center gap-2">
                          <GitCommit className="w-4 h-4" />
                          {repoName}
                        </CardTitle>
                        <CardDescription>{commits.length} commit{commits.length === 1 ? '' : 's'}</CardDescription>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2">
                      {commits.map((commit) => (
                        <label
                          key={commit.commit_id}
                          className="flex items-start gap-3 p-3 border rounded-lg hover:bg-slate-50 cursor-pointer"
                          aria-label={commit.message}
                        >
                          <input
                            type="checkbox"
                            checked={formData.selectedCommitIds.includes(commit.commit_id)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                updateField('selectedCommitIds', [...formData.selectedCommitIds, commit.commit_id]);
                              } else {
                                updateField('selectedCommitIds', formData.selectedCommitIds.filter(id => id !== commit.commit_id));
                              }
                            }}
                            className="w-4 h-4 mt-1"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                              <code className="text-xs font-mono text-slate-600 bg-slate-100 px-2 py-0.5 rounded">
                                {commit.commit_id.substring(0, 8)}
                              </code>
                              <span className="text-xs text-slate-500">
                                {commit.author} • {new Date(commit.commit_date).toLocaleDateString()}
                              </span>
                              {commit.work_item_ids?.length > 0 && (
                                <span className="text-xs text-slate-400">
                                  PBI #{commit.work_item_ids.join(', #')}
                                </span>
                              )}
                            </div>
                            <p className="text-sm text-slate-700 truncate">{commit.message}</p>
                          </div>
                        </label>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  // Step 5: Branch Configuration
  const renderStep5 = () => (
    <div className="space-y-6">
      <div>
        <Label htmlFor="releaseName" className="text-sm font-medium mb-2 block">
          Release Name <span className="text-red-500">*</span>
        </Label>
        <Input
          id="releaseName"
          placeholder="e.g., Release-1.0.0"
          value={formData.releaseName}
          onChange={(e) => updateField('releaseName', e.target.value)}
          className="w-full"
        />
        <p className="text-xs text-slate-500 mt-1">
          A descriptive name for this release
        </p>
      </div>

      <div>
        <Label htmlFor="branchName" className="text-sm font-medium mb-2 block">
          Branch Name <span className="text-red-500">*</span>
        </Label>
        <Input
          id="branchName"
          placeholder="e.g., release/1.0.0"
          value={formData.branchName}
          onChange={(e) => updateField('branchName', e.target.value)}
          className="w-full"
        />
        <p className="text-xs text-slate-500 mt-1">
          The Git branch name (must follow Git naming conventions)
        </p>
      </div>

      <div>
        <Label htmlFor="baseBranch" className="text-sm font-medium mb-2 block">
          Base Branch
        </Label>
        <Input
          id="baseBranch"
          placeholder="main"
          value={formData.baseBranch}
          onChange={(e) => updateField('baseBranch', e.target.value)}
          className="w-full"
        />
        <p className="text-xs text-slate-500 mt-1">
          The branch to create the release from (default: main)
        </p>
      </div>
    </div>
  );

  // Step 6: Release Creation
  const renderStep6 = () => {
    // Show success state with wiki URLs
    if (releaseResult?.success) {
      return (
        <div className="space-y-6">
          <Alert className="border-green-200 bg-green-50">
            <CheckCircle2 className="h-4 w-4 text-green-600" />
            <AlertDescription className="text-green-800">
              <p className="font-semibold mb-2">Release created successfully!</p>
              <div className="text-sm space-y-1">
                <p><strong>Release Name:</strong> {formData.releaseName}</p>
                <p><strong>Branch:</strong> <code className="bg-green-100 px-1 rounded">{releaseResult.branch_name || formData.branchName}</code></p>
                <p><strong>Organization:</strong> {formData.organization}</p>
                <p><strong>Project:</strong> {formData.project}</p>
              </div>
            </AlertDescription>
          </Alert>

          {releaseResult.wiki_urls && Object.keys(releaseResult.wiki_urls).length > 0 && (
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-slate-700">Wiki Documentation</h3>
              <div className="flex flex-col gap-2">
                {releaseResult.wiki_urls.main_page_url && (
                  <a
                    href={releaseResult.wiki_urls.main_page_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center px-4 py-2 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 transition-colors text-sm font-medium"
                  >
                    Release Wiki Page →
                  </a>
                )}
                {releaseResult.wiki_urls.commits_page_url && (
                  <a
                    href={releaseResult.wiki_urls.commits_page_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center px-4 py-2 bg-purple-50 text-purple-700 rounded-lg hover:bg-purple-100 transition-colors text-sm font-medium"
                  >
                    Commits &amp; Approvals →
                  </a>
                )}
              </div>
            </div>
          )}

          <div className="flex justify-end pt-4">
            <Button
              onClick={() => onComplete(releaseResult)}
              className="bg-green-600 hover:bg-green-700 text-white"
            >
              <CheckCircle2 className="w-4 h-4 mr-2" />
              Done
            </Button>
          </div>
        </div>
      );
    }

    // Show loading spinner while creating
    if (loading) {
      return (
        <div className="space-y-6">
          <Alert className="border-blue-200 bg-blue-50">
            <CheckCircle2 className="h-4 w-4 text-blue-600" />
            <AlertDescription className="text-blue-800">
              <p className="font-semibold mb-2">Creating release...</p>
              <div className="text-sm space-y-1">
                <p><strong>Organization:</strong> {formData.organization}</p>
                <p><strong>Project:</strong> {formData.project}</p>
                <p><strong>Release Name:</strong> {formData.releaseName}</p>
                <p><strong>Branch Name:</strong> {formData.branchName}</p>
                <p><strong>Work Items:</strong> {formData.selectedWorkItemIds.length}</p>
                <p><strong>Commits:</strong> {formData.selectedCommitIds.length}</p>
              </div>
            </AlertDescription>
          </Alert>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-8 h-8 animate-spin text-red-600" />
            <span className="ml-3 text-slate-600">Creating release...</span>
          </div>
        </div>
      );
    }

    // Default: show summary before creation starts
    return (
      <Alert className="border-blue-200 bg-blue-50">
        <CheckCircle2 className="h-4 w-4 text-blue-600" />
        <AlertDescription className="text-blue-800">
          <p className="font-semibold mb-2">Ready to create release</p>
          <div className="text-sm space-y-1">
            <p><strong>Organization:</strong> {formData.organization}</p>
            <p><strong>Project:</strong> {formData.project}</p>
            <p><strong>Wiki ID:</strong> {formData.wikiId}</p>
            <p><strong>Release Name:</strong> {formData.releaseName}</p>
            <p><strong>Branch Name:</strong> {formData.branchName}</p>
            <p><strong>Work Items:</strong> {formData.selectedWorkItemIds.length}</p>
            <p><strong>Commits:</strong> {formData.selectedCommitIds.length}</p>
          </div>
        </AlertDescription>
      </Alert>
    );
  };

  // Render current step content
  const renderStepContent = () => {
    switch (currentStep) {
      case 1:
        return renderStep1();
      case 2:
        return renderStep2();
      case 3:
        return renderStep3();
      case 4:
        return renderStep4();
      case 5:
        return renderStep5();
      case 6:
        return renderStep6();
      default:
        return null;
    }
  };

  // Validate current step
  const validateStep = useCallback(() => {
    switch (currentStep) {
      case 1:
        if (!formData.organization.trim() || !formData.project.trim()) {
          setError('Organization and Project are required');
          return false;
        }
        if (!formData.wikiId.trim()) {
          setError('Wiki ID is required');
          return false;
        }
        break;
      case 2:
        if (formData.searchType === 'tags' && !formData.tags.trim()) {
          setError('Please enter at least one tag');
          return false;
        }
        if (formData.searchType === 'pbi_numbers' && !formData.pbiNumbers.trim()) {
          setError('Please enter at least one PBI number');
          return false;
        }
        break;
      case 3:
        if (formData.selectedWorkItemIds.length === 0) {
          setError('Please select at least one work item');
          return false;
        }
        break;
      case 4:
        if (formData.selectedCommitIds.length === 0) {
          setError('Please select at least one commit');
          return false;
        }
        break;
      case 5:
        if (!formData.releaseName.trim()) {
          setError('Release name is required');
          return false;
        }
        if (!formData.branchName.trim()) {
          setError('Branch name is required');
          return false;
        }
        // Basic branch name validation
        if (!/^[a-zA-Z0-9/_-]+$/.test(formData.branchName)) {
          setError('Branch name contains invalid characters');
          return false;
        }
        break;
    }
    return true;
  }, [currentStep, formData]);

  // Handle next step
  const handleNext = useCallback(async () => {
    if (!validateStep()) {
      return;
    }

    setError(null);
    setLoading(true);

    try {
      // Step 2 -> 3: Fetch work items
      if (currentStep === 2) {
        const criteria = formData.searchType === 'tags' 
          ? { tags: formData.tags.split(',').map(t => t.trim()).filter(Boolean) }
          : { pbi_numbers: formData.pbiNumbers.split(',').map(n => parseInt(n.trim())).filter(n => !isNaN(n)) };
        
        const response = await agentApiClient.searchWorkItems(
          formData.organization,
          formData.project,
          criteria
        );
        
        updateField('workItems', response.work_items || []);
        updateField('selectedWorkItemIds', (response.work_items || []).map(wi => wi.id));
        setValidationWarnings(response.validation_warnings || []);
      }

      // Step 3 -> 4: Fetch commits for selected work items
      if (currentStep === 3) {
        const commitsMap = {};
        
        // Fetch commits for each selected work item
        await Promise.all(
          formData.selectedWorkItemIds.map(async (workItemId) => {
            try {
              const response = await agentApiClient.getCommitsForWorkItem(
                formData.organization,
                formData.project,
                workItemId
              );
              commitsMap[workItemId] = response.commits || [];
            } catch (err) {
              console.error(`Error fetching commits for work item ${workItemId}:`, err);
              commitsMap[workItemId] = [];
            }
          })
        );
        
        updateField('commits', commitsMap);
        
        // Auto-select all commits
        const allCommitIds = Object.values(commitsMap).flatMap(commits => commits.map(c => c.commit_id));
        updateField('selectedCommitIds', allCommitIds);
      }

      // Step 5 -> 6: Create release
      if (currentStep === 5) {
        // Move to step 6 first to show loading state
        setCurrentStep(6);
        
        const response = await agentApiClient.createRelease({
          name: formData.releaseName,
          organization: formData.organization,
          project: formData.project,
          work_item_ids: formData.selectedWorkItemIds,
          commit_ids: formData.selectedCommitIds,
          base_branch: formData.baseBranch,
          wiki_id: formData.wikiId.trim()
        });
        
        setLoading(false);
        
        if (response.success) {
          setReleaseResult(response);
        } else if (response.conflicts && response.conflicts.length > 0) {
          setConflictState({
            releaseId: response.release_id,
            conflicts: response.conflicts
          });
        } else {
          setError(response.message || 'Failed to create release');
        }
        return;
      }

      // Move to next step
      setCurrentStep(prev => prev + 1);
    } catch (err) {
      console.error('Error in wizard step:', err);
      setError(err.response?.data?.message || err.message || 'An error occurred');
    } finally {
      if (currentStep !== 5) {
        setLoading(false);
      }
    }
  }, [currentStep, formData, validateStep, onComplete, updateField]);

  // Handle previous step
  const handlePrevious = useCallback(() => {
    setError(null);
    setCurrentStep(prev => Math.max(1, prev - 1));
  }, []);

  // Handle cancel
  const handleCancel = useCallback(() => {
    if (window.confirm('Are you sure you want to cancel? All progress will be lost.')) {
      onCancel();
    }
  }, [onCancel]);

  // If conflict resolution is active, show it fullscreen
  if (conflictState) {
    return (
      <ConflictResolutionInterface
        conflicts={conflictState.conflicts}
        releaseId={conflictState.releaseId}
        onResolve={() => {
          setConflictState(null);
          setReleaseResult({ success: true, branch_name: formData.branchName, wiki_urls: null });
          setCurrentStep(6);
        }}
        onAbort={() => {
          setConflictState(null);
          onCancel();
        }}
      />
    );
  }

  return (
    <Card className="max-w-4xl mx-auto">
      <CardHeader className="border-b border-slate-100">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-xl font-bold text-slate-900">
              Create New Release
            </CardTitle>
            <CardDescription className="mt-1">
              Step {currentStep} of {STEPS.length}: {STEPS[currentStep - 1].title}
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCancel}
            className="text-slate-500 hover:text-slate-700"
          >
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Progress Indicator */}
        <div className="mt-6">
          <div className="flex items-center justify-between">
            {STEPS.map((step, index) => {
              const Icon = step.icon;
              const isActive = step.id === currentStep;
              const isCompleted = step.id < currentStep;
              
              return (
                <React.Fragment key={step.id}>
                  <div className="flex flex-col items-center">
                    <div
                      className={`
                        w-10 h-10 rounded-full flex items-center justify-center
                        ${isActive ? 'bg-red-600 text-white' : ''}
                        ${isCompleted ? 'bg-green-600 text-white' : ''}
                        ${!isActive && !isCompleted ? 'bg-slate-200 text-slate-500' : ''}
                      `}
                    >
                      <Icon className="w-5 h-5" />
                    </div>
                    <span className={`
                      text-xs mt-2 text-center max-w-[80px]
                      ${isActive ? 'font-semibold text-slate-900' : 'text-slate-500'}
                    `}>
                      {step.title}
                    </span>
                  </div>
                  {index < STEPS.length - 1 && (
                    <div className={`
                      flex-1 h-0.5 mx-2 mb-6
                      ${step.id < currentStep ? 'bg-green-600' : 'bg-slate-200'}
                    `} />
                  )}
                </React.Fragment>
              );
            })}
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

        {/* Step Content */}
        <div className="min-h-[400px]">
          {renderStepContent()}
        </div>

        {/* Navigation Buttons */}
        <div className="flex justify-between mt-8 pt-6 border-t border-slate-100">
          <Button
            variant="outline"
            onClick={handlePrevious}
            disabled={currentStep === 1 || loading || releaseResult?.success}
          >
            <ChevronLeft className="w-4 h-4 mr-2" />
            Previous
          </Button>

          <div className="flex gap-2">
            {!releaseResult?.success && (
              <Button
                variant="outline"
                onClick={handleCancel}
                disabled={loading}
              >
                Cancel
              </Button>
            )}
            {currentStep < STEPS.length && !releaseResult?.success && (
              <Button
                onClick={handleNext}
                disabled={loading}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Loading...
                  </>
                ) : (
                  <>
                    {currentStep === 5 ? 'Create Release' : 'Next'}
                    <ChevronRight className="w-4 h-4 ml-2" />
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

CreateReleaseWizard.propTypes = {
  onComplete: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired
};

export default CreateReleaseWizard;
