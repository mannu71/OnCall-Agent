import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  LayoutDashboard,
  Calendar,
  AlertTriangle,
  Zap,
  RefreshCw,
  Trash2,
  Eye,
  ChevronLeft,
  ChevronRight
} from 'lucide-react';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';
import { useScheduler } from '../context/SchedulerContext';
import ExecutionMonitor from '../components/monitoring/ExecutionMonitor';
import agentApiClient from '../services/agentApiClient';
import { parseDate, formatDate } from '../utils/workflowUtils';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

export default function Dashboard() {
  const { schedules, formatTime } = useScheduler();
  const { runningWorkflows, count: inProgressCount } = useWorkflowStatus();

  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [page, setPage] = useState(0);
  const [rowsPerPage] = useState(5);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);

  const loadExecutions = useCallback(async () => {
    try {
      const history = await agentApiClient.listAllExecutions(100);
      setExecutions(history);
    } catch (error) {
      console.error('Error loading executions:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleClearAll = useCallback(async () => {
    setClearing(true);
    try {
      await agentApiClient.deleteAllExecutions();
      setExecutions([]);
      setPage(0);
      setConfirmClear(false);
    } catch (error) {
      console.error('Error clearing executions:', error);
      alert('Failed to clear executions. Please try again.');
    } finally {
      setClearing(false);
    }
  }, []);

  useEffect(() => {
    loadExecutions();
    const interval = setInterval(loadExecutions, 30000);
    return () => clearInterval(interval);
  }, [loadExecutions]);

  const stats = useMemo(() => {
    const totalSchedules = schedules.length;
    const enabledSchedules = schedules.filter(s => s.enabled).length;
    const disabledSchedules = totalSchedules - enabledSchedules;

    // Calculate failures in last 24h (any non-success status)
    const failedLast24h = executions.filter(e => {
      if (e.status === 'success') return false;
      const date = parseDate(e.start_time);
      if (!date) return false;
      return (Date.now() - date.getTime()) < 24 * 60 * 60 * 1000;
    }).length;

    // Calculate total executions in last 24h
    const executionsLast24h = executions.filter(e => {
      const date = parseDate(e.start_time);
      if (!date) return false;
      return (Date.now() - date.getTime()) < 24 * 60 * 60 * 1000;
    }).length;

    // Calculate success rate
    const successRate = executionsLast24h > 0
      ? Math.round(((executionsLast24h - failedLast24h) / executionsLast24h) * 100)
      : 100;

    // Calculate workflows from yesterday
    const yesterday = Date.now() - 24 * 60 * 60 * 1000;
    const twoDaysAgo = Date.now() - 48 * 60 * 60 * 1000;

    const executionsYesterday = executions.filter(e => {
      const date = parseDate(e.start_time);
      if (!date) return false;
      const time = date.getTime();
      return time >= twoDaysAgo && time < yesterday;
    }).length;

    const workflowChange = executionsLast24h - executionsYesterday;
    const workflowChangeText = workflowChange > 0
      ? `+${workflowChange} since yesterday`
      : workflowChange < 0
        ? `${workflowChange} since yesterday`
        : 'No change';

    return [
      {
        title: "Active Workflows",
        value: totalSchedules,
        icon: LayoutDashboard,
        bgColor: "bg-red-50",
        iconColor: "text-red-600",
        change: disabledSchedules > 0 ? `${disabledSchedules} disabled` : "All enabled"
      },
      {
        title: "Enabled Schedules",
        value: enabledSchedules,
        icon: Calendar,
        bgColor: "bg-emerald-50",
        iconColor: "text-emerald-600",
        change: `${Math.round((enabledSchedules / Math.max(totalSchedules, 1)) * 100)}% of total`
      },
      {
        title: "Failures (24h)",
        value: failedLast24h,
        icon: AlertTriangle,
        bgColor: "bg-rose-50",
        iconColor: "text-rose-600",
        change: `${successRate}% success rate`
      },
      {
        title: "Running Now",
        value: inProgressCount,
        icon: Zap,
        bgColor: "bg-amber-50",
        iconColor: "text-amber-600",
        change: executionsLast24h > 0 ? `${executionsLast24h} runs today` : "No runs today"
      }
    ];
  }, [schedules, executions, inProgressCount]);

  const nextRunInfo = useMemo(() => {
    const enabled = schedules.filter(s => s.enabled && s.startTime);
    if (enabled.length === 0) return 'Not scheduled';

    const sorted = [...enabled].sort((a, b) => a.startTime.localeCompare(b.startTime));
    const earliest = sorted[0];

    return `Next: ${formatTime(earliest.startTime)} (${earliest.name})`;
  }, [schedules, formatTime]);

  const paginatedRuns = useMemo(() =>
    executions.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage),
    [executions, page, rowsPerPage]);

  const totalPages = Math.ceil(executions.length / rowsPerPage);

  return (
    <div className="p-4 md:p-8 min-h-screen">
      {/* Title & Status Section */}
      <div className="mb-6 md:mb-8 flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl md:text-3xl font-bold text-slate-900 tracking-tight">System Dashboard</h2>
          <p className="text-slate-500 mt-1 text-sm md:text-base">Real-time monitoring and node activity tracking</p>
        </div>

        {/* Status Banner */}
        {inProgressCount > 0 ? (
          <div className="bg-amber-50 border border-amber-100 rounded-lg px-3 md:px-4 py-2 md:py-2.5 flex items-center space-x-2 md:space-x-3 shadow-sm">
            <div className="relative flex h-2.5 w-2.5 flex-shrink-0">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-amber-600"></span>
            </div>
            <p className="text-xs md:text-sm font-medium text-amber-900">
              <strong>Workflow running:</strong> <span className="text-amber-600 ml-1">{runningWorkflows?.join(', ')}</span>
            </p>
          </div>
        ) : (
          <div className="bg-red-50 border border-red-100 rounded-lg px-3 md:px-4 py-2 md:py-2.5 flex items-center space-x-2 md:space-x-3 shadow-sm">
            <div className="relative flex h-2.5 w-2.5 flex-shrink-0">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-600"></span>
            </div>
            <p className="text-xs md:text-sm font-medium text-red-900">
              All systems operational. <span className="text-red-600 ml-1 hidden sm:inline">{nextRunInfo}</span>
            </p>
          </div>
        )}
      </div>

      {/* Summary Cards Grid */}
      <section className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4 lg:gap-6 mb-6 md:mb-10">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <Card key={stat.title} className="hover:shadow-md transition-shadow">
              <CardContent className="p-4 md:p-6">
                <div className="flex items-center justify-between mb-3 md:mb-4">
                  <span className="text-[10px] md:text-xs lg:text-sm font-semibold text-slate-500 uppercase tracking-wider leading-tight">{stat.title}</span>
                  <div className={cn("p-1.5 md:p-2 rounded-lg", stat.bgColor, stat.iconColor)}>
                    <Icon className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                </div>
                <div className="flex flex-col space-y-1">
                  <span className="text-2xl md:text-3xl lg:text-4xl font-bold text-slate-900">{stat.value}</span>
                  <span className="text-[10px] md:text-xs text-slate-400 font-medium leading-tight">{stat.change}</span>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </section>

      {/* Live Monitor */}
      {runningWorkflows.length > 0 && (
        <section className="mb-6 md:mb-10">
          <h3 className="text-base md:text-lg font-bold text-slate-900 mb-3 md:mb-4 flex items-center">
            <div className="relative flex h-2.5 w-2.5 mr-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-600"></span>
            </div>
            Live Monitor
          </h3>
          {runningWorkflows.map(name => (
            <ExecutionMonitor key={name} workflowName={name} autoStart={true} />
          ))}
        </section>
      )}

      {/* Recent Activity Table */}
      <Card>
        <CardHeader className="px-4 md:px-6 py-4 md:py-5 border-b border-slate-100 flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base md:text-lg font-bold text-slate-900">Recent Activity</CardTitle>
          <div className="flex items-center space-x-2 md:space-x-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={loadExecutions}
              className="text-red-600 hover:bg-red-50 hover:text-red-700 text-xs md:text-sm"
            >
              <RefreshCw className="w-3 h-3 md:w-4 md:h-4 md:mr-2" />
              <span className="hidden md:inline">Refresh</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmClear(true)}
              disabled={executions.length === 0}
              className="border-slate-200 hover:bg-slate-50 text-xs md:text-sm"
            >
              <Trash2 className="w-3 h-3 md:w-4 md:h-4 md:mr-2" />
              <span className="hidden md:inline">Clear All</span>
            </Button>
          </div>
        </CardHeader>

        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50/50">
                  <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4">Workflow</TableHead>
                  <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 hidden sm:table-cell">Started</TableHead>
                  <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 hidden md:table-cell">Duration</TableHead>
                  <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4">Details</TableHead>
                  <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4">Status</TableHead>
                  <TableHead className="text-slate-500 text-[10px] md:text-xs font-bold uppercase tracking-wider px-3 md:px-6 py-3 md:py-4 text-right">Details</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className="text-xs md:text-sm">
                {loading && executions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="px-3 md:px-6 py-6 md:py-8 text-center text-slate-500">Loading...</TableCell>
                  </TableRow>
                ) : executions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="px-3 md:px-6 py-6 md:py-8 text-center text-slate-500">No recent activity</TableCell>
                  </TableRow>
                ) : (
                  paginatedRuns.map((run) => {
                    const orchestratorKey = Object.keys(run.output || {}).find(k => k.startsWith('orchestrator'));
                    const orchestrator = orchestratorKey ? run.output[orchestratorKey] : null;
                    const queriesExecuted = orchestrator?.queries_executed || run.output?.queries_executed || 0;
                    const isCloudWatch = run.output?.analysis_type;
                    const logGroupsAnalyzed = run.output?.log_groups_analyzed?.length || 0;
                    const anomaliesCount = run.output?.alerts?.length || 0;

                    return (
                      <TableRow key={run.execution_id || run.id}>
                        <TableCell className="px-3 md:px-6 py-3 md:py-4 font-semibold text-slate-900 capitalize">
                          {run.workflow_name.replaceAll('-', ' ')}
                        </TableCell>
                        <TableCell className="px-3 md:px-6 py-3 md:py-4 text-slate-500 hidden sm:table-cell">{formatDate(run.start_time)}</TableCell>
                        <TableCell className="px-3 md:px-6 py-3 md:py-4 text-slate-600 font-mono hidden md:table-cell">
                          {run.duration ? `${run.duration.toFixed(1)}s` : '—'}
                        </TableCell>
                        <TableCell className="px-3 md:px-6 py-3 md:py-4">
                          {isCloudWatch ? (
                            <span className={logGroupsAnalyzed > 0 ? "text-red-600 font-medium" : "text-slate-400"}>
                              {logGroupsAnalyzed > 0 ? `${logGroupsAnalyzed} log group${logGroupsAnalyzed !== 1 ? 's' : ''}` : '—'}
                            </span>
                          ) : (
                            <span className={queriesExecuted > 0 ? "text-red-600 font-medium" : "text-slate-400"}>
                              {queriesExecuted > 0 ? queriesExecuted : '—'}
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="px-3 md:px-6 py-3 md:py-4">
                          <Badge variant={run.status === 'success' ? 'success' : 'destructive'} className="text-[10px] md:text-xs">
                            {run.status === 'success' ? 'Success' : 'Failure'}
                          </Badge>
                        </TableCell>
                        <TableCell className="px-3 md:px-6 py-3 md:py-4 text-right">
                          <button
                            onClick={() => setSelectedRun(run)}
                            className="text-red-500 hover:text-red-700 transition-colors"
                          >
                            <Eye className="w-4 h-4 md:w-5 md:h-5 inline" />
                          </button>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>

          {/* Table Footer with Pagination */}
          <div className="px-3 md:px-6 py-3 md:py-4 bg-slate-50 border-t border-slate-100 flex items-center justify-between">
            <p className="text-[10px] md:text-xs text-slate-500 italic">
              Showing {executions.length === 0 ? 0 : page * rowsPerPage + 1}-{Math.min((page + 1) * rowsPerPage, executions.length)} of {executions.length} records
            </p>
            <div className="flex items-center space-x-1 md:space-x-2">
              <span className="text-[10px] md:text-xs text-slate-500 mr-2">
                Page {executions.length === 0 ? 0 : page + 1} of {totalPages || 1}
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
                disabled={page >= totalPages - 1 || executions.length === 0}
                className="p-1 text-slate-400 hover:text-slate-600 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronRight className="w-4 h-4 md:w-5 md:h-5" />
              </button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Confirmation Dialog for Clear All */}
      <Dialog open={confirmClear} onOpenChange={setConfirmClear}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Clear All Executions?</DialogTitle>
            <DialogDescription>
              This will permanently delete all {executions.length} execution{executions.length === 1 ? '' : 's'} from the history. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setConfirmClear(false)}
              disabled={clearing}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleClearAll}
              disabled={clearing}
            >
              {clearing ? 'Clearing...' : 'Clear All'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Details Dialog */}
      <Dialog open={!!selectedRun} onOpenChange={(open) => !open && setSelectedRun(null)}>
        <DialogContent className="sm:max-w-4xl w-full max-h-[90vh] overflow-y-auto bg-white border border-slate-200 shadow-xl p-0 gap-0">
          {/* Header Section */}
          <div className="bg-white border-b border-slate-200 px-6 py-5">
            <DialogHeader>
              <DialogTitle className="text-xl font-semibold text-slate-900">
                Execution Details: {selectedRun?.workflow_name?.replaceAll('-', ' ')}
              </DialogTitle>
            </DialogHeader>
          </div>

          <div className="p-8 space-y-8 bg-slate-50 overflow-y-auto custom-scrollbar">
            {/* Execution Metrics Grid */}
            {(() => {
              const resultData = selectedRun?.result || {};
              const orchestratorKey = Object.keys(resultData).find(k => k.startsWith('orchestrator'));
              const orchestrator = orchestratorKey ? resultData[orchestratorKey] : null;
              const isCloudWatch = selectedRun?.output?.analysis_type;

              const queriesExecuted = orchestrator?.queries_executed || selectedRun?.output?.queries_executed || 0;
              const failures = orchestrator?.failures || selectedRun?.output?.failures || 0;
              const duration = selectedRun?.duration ? selectedRun.duration.toFixed(2) : 'N/A';
              const logGroupsAnalyzed = selectedRun?.output?.log_groups_analyzed?.length || 0;
              const alertsCount = selectedRun?.output?.alerts?.length || 0;

              if (isCloudWatch) {
                return (
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Execution ID</p>
                      <p className="mt-2 text-4xl font-bold text-slate-900">{selectedRun?.execution_id || selectedRun?.id}</p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Duration</p>
                      <p className="mt-2 text-4xl font-bold text-slate-900">{duration}<span className="text-xl ml-1 text-slate-400">s</span></p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Log Groups</p>
                      <p className="mt-2 text-4xl font-bold text-blue-700">{logGroupsAnalyzed}</p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Alerts</p>
                      <p className="mt-2 text-4xl font-bold text-emerald-600">{alertsCount}</p>
                    </div>
                  </div>
                );
              }

              return (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Execution ID</p>
                    <p className="mt-2 text-4xl font-bold text-slate-900">{selectedRun?.execution_id || selectedRun?.id}</p>
                  </div>
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Duration</p>
                    <p className="mt-2 text-4xl font-bold text-slate-900">{duration}<span className="text-xl ml-1 text-slate-400">s</span></p>
                  </div>
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Queries</p>
                    <p className="mt-2 text-4xl font-bold text-blue-700">{queriesExecuted}</p>
                  </div>
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Failures</p>
                    <p className="mt-2 text-4xl font-bold text-emerald-600">{failures}</p>
                  </div>
                </div>
              );
            })()}

            {/* Results Section */}
            {(() => {
              const resultData = selectedRun?.result || {};
              const orchestratorKey = Object.keys(resultData).find(k => k.startsWith('orchestrator'));
              const orchestrator = orchestratorKey ? resultData[orchestratorKey] : null;
              const isCloudWatch = selectedRun?.output?.analysis_type;

              const results = orchestrator?.results || selectedRun?.output?.results || [];

              if (isCloudWatch) {
                const cwResults = selectedRun?.output?.results || {};
                const cwAlerts = selectedRun?.output?.alerts || [];
                const analysisType = selectedRun.output.analysis_type;
                const logGroupsAnalyzed = selectedRun.output.log_groups_analyzed || [];
                const timeRange = selectedRun.output.time_range || '—';

                return (
                  <div>
                    <div className="flex items-center justify-between mb-6">
                      <h3 className="text-xl font-bold text-slate-900">CloudWatch Analysis</h3>
                      <span className="px-3 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-medium uppercase">{analysisType}</span>
                    </div>
                    <div className="border border-slate-200 rounded-lg overflow-hidden shadow-sm">
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50 border-b border-slate-200">
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Property</TableHead>
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Value</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                            <TableCell className="px-6 py-4 text-slate-600 font-medium">Analysis Type</TableCell>
                            <TableCell className="px-6 py-4">
                              <span className="px-2 py-1 bg-slate-100 rounded text-xs font-medium">{analysisType}</span>
                            </TableCell>
                          </TableRow>
                          <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                            <TableCell className="px-6 py-4 text-slate-600 font-medium">Time Range</TableCell>
                            <TableCell className="px-6 py-4 font-semibold text-slate-900">{timeRange}</TableCell>
                          </TableRow>
                          <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                            <TableCell className="px-6 py-4 text-slate-600 font-medium">Log Groups</TableCell>
                            <TableCell className="px-6 py-4">
                              <div className="flex flex-wrap gap-1">
                                {logGroupsAnalyzed.map((lg, i) => (
                                  <code key={i} className="px-2 py-0.5 bg-slate-100 rounded text-xs font-mono text-slate-800 border border-slate-200">{lg}</code>
                                ))}
                              </div>
                            </TableCell>
                          </TableRow>
                          {cwResults.summary && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Summary</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.summary, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {cwResults.patterns && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Patterns</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.patterns, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {cwResults.anomalies && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Anomalies</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.anomalies, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {cwResults.timeline && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Timeline</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.timeline, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {cwResults.log_groups && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Log Group Details</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.log_groups, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {cwAlerts.length > 0 && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Alerts</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="space-y-2">
                                  {cwAlerts.map((alert, i) => (
                                    <div key={i} className={`px-3 py-2 rounded text-xs font-medium ${alert.severity === 'high' ? 'bg-red-50 border border-red-200 text-red-800' : alert.severity === 'medium' ? 'bg-amber-50 border border-amber-200 text-amber-800' : 'bg-slate-50 border border-slate-200 text-slate-800'}`}>
                                      <span className="font-bold uppercase">{alert.severity}</span>: {alert.message}
                                    </div>
                                  ))}
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                );
              }

              if (Array.isArray(results) && results.length > 0) {
                return (
                  <div>
                    <div className="flex items-center justify-between mb-6">
                      <h3 className="text-xl font-bold text-slate-900">Query Results</h3>
                      <span className="px-3 py-1 bg-slate-100 text-slate-600 rounded-full text-xs font-medium uppercase">Detailed Metrics</span>
                    </div>
                    <div className="border border-slate-200 rounded-lg overflow-hidden shadow-sm">
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50 border-b border-slate-200">
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Label</TableHead>
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Result / Error</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {results.map((result, idx) => {
                            const label = result.label || result.query_id || `Query ${idx + 1}`;
                            const hasError = !result.success || result.error;
                            let resultValue = hasError ? result.error : result.result;

                            // Extract value from array if it's a single-element array
                            if (Array.isArray(resultValue) && resultValue.length === 1) {
                              resultValue = resultValue[0];
                            }

                            // Extract scalar value if result is an object with a single key-value pair
                            let displayValue = resultValue;
                            if (typeof resultValue === 'object' && resultValue !== null && !Array.isArray(resultValue)) {
                              const keys = Object.keys(resultValue);
                              if (keys.length === 1) {
                                displayValue = resultValue[keys[0]];
                              }
                            }

                            const uniqueKey = result.query_id || `${label}-${idx}`;

                            // Check if value looks like a UUID
                            const isUUID = typeof displayValue === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(displayValue);

                            return (
                              <TableRow key={uniqueKey} className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                                <TableCell className="px-6 py-4 text-slate-600 font-medium">{label}</TableCell>
                                <TableCell className="px-6 py-4">
                                  {hasError ? (
                                    <span className="text-red-600 font-semibold">{String(displayValue)}</span>
                                  ) : typeof displayValue === 'boolean' ? (
                                    displayValue ? (
                                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-green-100 text-emerald-600 uppercase">
                                        <span className="w-2 h-2 mr-1.5 rounded-full bg-emerald-600"></span>
                                        TRUE
                                      </span>
                                    ) : (
                                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-600 uppercase">
                                        <span className="w-2 h-2 mr-1.5 rounded-full bg-red-600"></span>
                                        FALSE
                                      </span>
                                    )
                                  ) : isUUID ? (
                                    <code className="px-2 py-1 bg-slate-100 rounded text-xs font-mono text-slate-800 border border-slate-200">
                                      {String(displayValue)}
                                    </code>
                                  ) : typeof displayValue === 'object' && displayValue !== null ? (
                                    <div className="bg-slate-50 rounded p-2 text-xs font-mono overflow-auto max-h-32">
                                      <pre className="whitespace-pre-wrap break-words">{JSON.stringify(displayValue, null, 2)}</pre>
                                    </div>
                                  ) : (
                                    <span className="font-semibold text-slate-900">{String(displayValue)}</span>
                                  )}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                );
              }

              return null;
            })()}

            {/* Error Information */}
            {selectedRun?.error && (
              <div className="bg-white rounded-lg border border-red-200 overflow-hidden">
                <div className="px-6 py-4 border-b border-red-200 bg-red-50 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-red-600" />
                  <h3 className="text-base font-semibold text-red-900">Error Details</h3>
                </div>
                <div className="p-6">
                  <pre className="text-sm font-mono text-red-800 whitespace-pre-wrap break-words">
                    {typeof selectedRun.error === 'string' ? selectedRun.error : JSON.stringify(selectedRun.error, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="bg-white border-t border-slate-200 px-6 py-4 flex justify-end">
            <Button onClick={() => setSelectedRun(null)} variant="outline" className="px-6">CLOSE</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
