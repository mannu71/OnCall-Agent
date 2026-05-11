import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  GitBranch,
  Plus,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Calendar,
  User,
  FileText,
  ExternalLink,
  ArrowLeft
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import CreateReleaseWizard from '@/components/releases/CreateReleaseWizard';
import agentApiClient from '@/services/agentApiClient';

// API client for releases
const releasesApi = {
  async getReleases(limit = 20, offset = 0, organization = null, project = null) {
    return agentApiClient.getReleases(limit, offset, organization, project);
  },
  
  async getReleaseDetails(organization, project, releaseName) {
    return agentApiClient.getReleaseDetails(organization, project, releaseName);
  }
};

/**
 * ReleaseList Sub-component
 * Displays release history table with pagination
 */
const ReleaseList = ({ onCreateRelease, onViewDetails }) => {
  const [releases, setReleases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [rowsPerPage] = useState(20);
  const [totalCount, setTotalCount] = useState(0);

  const loadReleases = useCallback(async () => {
    setLoading(true);
    try {
      const response = await releasesApi.getReleases(rowsPerPage, page * rowsPerPage);
      setReleases(response.releases);
      setTotalCount(response.total_count);
    } catch (error) {
      console.error('Error loading releases:', error);
      setReleases([]);
      setTotalCount(0);
    } finally {
      setLoading(false);
    }
  }, [page, rowsPerPage]);

  useEffect(() => {
    loadReleases();
  }, [loadReleases]);

  const totalPages = Math.ceil(totalCount / rowsPerPage);

  const formatDate = (dateString) => {
    if (!dateString) return '—';
    try {
      const date = new Date(dateString);
      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch {
      return dateString;
    }
  };

  return (
    <Card>
      <CardHeader className="px-4 md:px-6 py-4 md:py-5 border-b border-slate-100 flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base md:text-lg font-bold text-slate-900">Release History</CardTitle>
        <div className="flex items-center space-x-2 md:space-x-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={loadReleases}
            className="text-red-600 hover:bg-red-50 hover:text-red-700 text-xs md:text-sm"
          >
            <RefreshCw className="w-3 h-3 md:w-4 md:h-4 md:mr-2" />
            <span className="hidden md:inline">Refresh</span>
          </Button>
          <Button
            variant="default"
            size="sm"
            onClick={onCreateRelease}
            className="bg-red-600 hover:bg-red-700 text-white text-xs md:text-sm"
          >
            <Plus className="w-3 h-3 md:w-4 md:h-4 md:mr-2" />
            <span className="hidden md:inline">Create New Release</span>
            <span className="md:hidden">Create</span>
          </Button>
        </div>
      </CardHeader>

      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50/50">
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4">
                  Release Name
                </TableHead>
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 hidden sm:table-cell">
                  Branch
                </TableHead>
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 hidden md:table-cell">
                  Organization
                </TableHead>
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 hidden lg:table-cell">
                  Project
                </TableHead>
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4">
                  Created
                </TableHead>
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 hidden sm:table-cell">
                  PBIs
                </TableHead>
                <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 text-right">
                  Actions
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody className="text-xs md:text-sm">
              {loading ? (
                <TableRow>
                  <TableCell colSpan={7} className="px-3 md:px-6 py-6 md:py-8 text-center text-slate-500">
                    Loading releases...
                  </TableCell>
                </TableRow>
              ) : releases.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="px-3 md:px-6 py-6 md:py-8 text-center">
                    <div className="flex flex-col items-center justify-center space-y-3">
                      <GitBranch className="w-12 h-12 text-slate-300" />
                      <div>
                        <p className="text-slate-500 font-medium">No releases yet</p>
                        <p className="text-slate-400 text-sm mt-1">Create your first release to get started</p>
                      </div>
                      <Button
                        variant="default"
                        size="sm"
                        onClick={onCreateRelease}
                        className="bg-red-600 hover:bg-red-700 text-white mt-2"
                      >
                        <Plus className="w-4 h-4 mr-2" />
                        Create New Release
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                releases.map((release) => (
                  <TableRow key={release.id} className="hover:bg-slate-50 cursor-pointer">
                    <TableCell 
                      className="px-3 md:px-6 py-3 md:py-4 font-semibold text-slate-900"
                      onClick={() => onViewDetails(release)}
                    >
                      {release.name}
                    </TableCell>
                    <TableCell className="px-3 md:px-6 py-3 md:py-4 text-slate-600 font-mono text-xs hidden sm:table-cell">
                      {release.branch_name}
                    </TableCell>
                    <TableCell className="px-3 md:px-6 py-3 md:py-4 text-slate-600 hidden md:table-cell">
                      {release.organization}
                    </TableCell>
                    <TableCell className="px-3 md:px-6 py-3 md:py-4 text-slate-600 hidden lg:table-cell">
                      {release.project}
                    </TableCell>
                    <TableCell className="px-3 md:px-6 py-3 md:py-4 text-slate-500">
                      {formatDate(release.created_at)}
                    </TableCell>
                    <TableCell className="px-3 md:px-6 py-3 md:py-4 hidden sm:table-cell">
                      <Badge variant="secondary" className="text-xs">
                        {release.work_item_ids?.length || 0} PBIs
                      </Badge>
                    </TableCell>
                    <TableCell className="px-3 md:px-6 py-3 md:py-4 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onViewDetails(release)}
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                      >
                        View
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {/* Pagination Footer */}
        {!loading && releases.length > 0 && (
          <div className="px-3 md:px-6 py-3 md:py-4 bg-slate-50 border-t border-slate-100 flex items-center justify-between">
            <p className="text-[10px] md:text-xs text-slate-500 italic">
              Showing {totalCount === 0 ? 0 : page * rowsPerPage + 1}-{Math.min((page + 1) * rowsPerPage, totalCount)} of {totalCount} releases
            </p>
            <div className="flex items-center space-x-1 md:space-x-2">
              <span className="text-[10px] md:text-xs text-slate-500 mr-2">
                Page {totalCount === 0 ? 0 : page + 1} of {totalPages || 1}
              </span>
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1 text-slate-400 hover:text-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronLeft className="w-4 h-4 md:w-5 md:h-5" />
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1 || totalCount === 0}
                className="p-1 text-slate-400 hover:text-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronRight className="w-4 h-4 md:w-5 md:h-5" />
              </button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

/**
 * ReleaseDetails Sub-component
 * Displays detailed information about a specific release
 */
const ReleaseDetails = ({ release, onBack }) => {
  const [details, setDetails] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadDetails = async () => {
      setLoading(true);
      try {
        const data = await releasesApi.getReleaseDetails(
          release.organization,
          release.project,
          release.name
        );
        setDetails(data || release);
      } catch (error) {
        console.error('Error loading release details:', error);
        setDetails(release);
      } finally {
        setLoading(false);
      }
    };

    loadDetails();
  }, [release]);

  const formatDate = (dateString) => {
    if (!dateString) return '—';
    try {
      const date = new Date(dateString);
      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      });
    } catch {
      return dateString;
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-slate-500">
          Loading release details...
        </CardContent>
      </Card>
    );
  }

  const releaseData = details || release;

  return (
    <div className="space-y-6">
      {/* Header with Back Button */}
      <div className="flex items-center space-x-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="text-slate-600 hover:text-slate-900"
        >
          <ArrowLeft className="w-4 h-4 mr-2" />
          Back to Releases
        </Button>
      </div>

      {/* Release Metadata Card */}
      <Card>
        <CardHeader className="border-b border-slate-100">
          <div className="flex items-start justify-between">
            <div>
              <CardTitle className="text-2xl font-bold text-slate-900 mb-2">
                {releaseData.name}
              </CardTitle>
              <div className="flex items-center space-x-4 text-sm text-slate-500">
                <div className="flex items-center">
                  <GitBranch className="w-4 h-4 mr-1" />
                  <span className="font-mono">{releaseData.branch_name}</span>
                </div>
                <div className="flex items-center">
                  <Calendar className="w-4 h-4 mr-1" />
                  <span>{formatDate(releaseData.created_at)}</span>
                </div>
                {releaseData.created_by && (
                  <div className="flex items-center">
                    <User className="w-4 h-4 mr-1" />
                    <span>{releaseData.created_by}</span>
                  </div>
                )}
              </div>
            </div>
            <Badge 
              variant={releaseData.status === 'created' ? 'success' : 'secondary'}
              className="text-sm"
            >
              {releaseData.status || 'created'}
            </Badge>
          </div>
        </CardHeader>

        <CardContent className="p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-2">
                Organization
              </h3>
              <p className="text-slate-900">{releaseData.organization}</p>
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-2">
                Project
              </h3>
              <p className="text-slate-900">{releaseData.project}</p>
            </div>
          </div>

          {/* Wiki Links */}
          {(releaseData.wiki_main_page_url || releaseData.wiki_commits_page_url) && (
            <div className="mt-6 pt-6 border-t border-slate-100">
              <h3 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">
                Documentation
              </h3>
              <div className="flex flex-wrap gap-3">
                {releaseData.wiki_main_page_url && (
                  <a
                    href={releaseData.wiki_main_page_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center px-4 py-2 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 transition-colors text-sm font-medium"
                  >
                    <FileText className="w-4 h-4 mr-2" />
                    Release Wiki Page
                    <ExternalLink className="w-3 h-3 ml-2" />
                  </a>
                )}
                {releaseData.wiki_commits_page_url && (
                  <a
                    href={releaseData.wiki_commits_page_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center px-4 py-2 bg-purple-50 text-purple-700 rounded-lg hover:bg-purple-100 transition-colors text-sm font-medium"
                  >
                    <FileText className="w-4 h-4 mr-2" />
                    Commits & Approvals
                    <ExternalLink className="w-3 h-3 ml-2" />
                  </a>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Work Items Card */}
      <Card>
        <CardHeader className="border-b border-slate-100">
          <CardTitle className="text-lg font-bold text-slate-900">
            Work Items ({releaseData.work_item_ids?.length || 0})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          {releaseData.work_item_ids && releaseData.work_item_ids.length > 0 ? (
            <div className="space-y-2">
              {releaseData.work_item_ids.map((id) => (
                <div
                  key={id}
                  className="flex items-center justify-between p-3 bg-slate-50 rounded-lg hover:bg-slate-100 transition-colors"
                >
                  <div className="flex items-center space-x-3">
                    <Badge variant="outline" className="font-mono">
                      #{id}
                    </Badge>
                    <span className="text-slate-700">Work Item {id}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-slate-500 text-center py-4">No work items associated with this release</p>
          )}
        </CardContent>
      </Card>

      {/* Commits Card */}
      <Card>
        <CardHeader className="border-b border-slate-100">
          <CardTitle className="text-lg font-bold text-slate-900">
            Commits ({releaseData.commit_ids?.length || 0})
          </CardTitle>
        </CardHeader>
        <CardContent className="p-6">
          {releaseData.commit_ids && releaseData.commit_ids.length > 0 ? (
            <div className="space-y-2">
              {releaseData.commit_ids.map((commitId) => (
                <div
                  key={commitId}
                  className="flex items-center p-3 bg-slate-50 rounded-lg hover:bg-slate-100 transition-colors font-mono text-sm"
                >
                  <code className="text-slate-700">{commitId}</code>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-slate-500 text-center py-4">No commits associated with this release</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

/**
 * Main Releases Page Component
 * Manages view state and orchestrates sub-components
 */
const ReleasesPage = () => {
  const [view, setView] = useState('list'); // 'list', 'create', 'details'
  const [selectedRelease, setSelectedRelease] = useState(null);

  const handleCreateRelease = () => {
    setView('create');
  };

  const handleReleaseComplete = (release) => {
    console.log('Release created:', release);
    setView('list');
    // Optionally refresh the release list here
  };

  const handleViewDetails = (release) => {
    setSelectedRelease(release);
    setView('details');
  };

  const handleBackToList = () => {
    setSelectedRelease(null);
    setView('list');
  };

  return (
    <div className="p-4 md:p-8 min-h-screen bg-background">
      <div className="max-w-7xl mx-auto">
        {/* Page Header */}
        <div className="mb-6 md:mb-8">
          <h2 className="text-2xl md:text-3xl font-bold text-slate-900 tracking-tight">
            Release Management
          </h2>
          <p className="text-slate-500 mt-1 text-sm md:text-base">
            Create and manage Azure DevOps release branches
          </p>
        </div>

        {/* View Rendering */}
        {view === 'list' && (
          <ReleaseList
            onCreateRelease={handleCreateRelease}
            onViewDetails={handleViewDetails}
          />
        )}

        {view === 'details' && selectedRelease && (
          <ReleaseDetails
            release={selectedRelease}
            onBack={handleBackToList}
          />
        )}

        {view === 'create' && (
          <CreateReleaseWizard
            onComplete={handleReleaseComplete}
            onCancel={handleBackToList}
          />
        )}
      </div>
    </div>
  );
};

export default ReleasesPage;
