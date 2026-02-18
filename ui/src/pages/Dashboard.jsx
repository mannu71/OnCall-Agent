import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Box, Typography, Grid, Card, CardContent, Alert, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Chip, IconButton, Dialog, DialogTitle, DialogContent, DialogActions, Button, TablePagination } from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';
import { useScheduler } from '../context/SchedulerContext';
import ExecutionMonitor from '../components/monitoring/ExecutionMonitor';
import agentApiClient from '../services/agentApiClient';
import { parseDate, formatDate, formatResult } from '../utils/workflowUtils';

const STATUS_COLOR = { success: 'success', failed: 'error' };

export default function Dashboard() {
  const { schedules, formatTime } = useScheduler();
  const { runningWorkflows, count: inProgressCount } = useWorkflowStatus();

  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
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
    const failedLast24h = executions.filter(e => {
      if (e.status !== 'failed') return false;
      const date = parseDate(e.start_time);
      if (!date) return false;
      return (Date.now() - date.getTime()) < 24 * 60 * 60 * 1000;
    }).length;

    return [
      { title: "Active Workflows", value: totalSchedules, color: "primary.main" },
      { title: "Enabled Schedules", value: enabledSchedules, color: "success.main" },
      { title: "Failures (24h)", value: failedLast24h, color: failedLast24h > 0 ? "error.main" : "success.main" },
      { title: "Running Now", value: inProgressCount, color: inProgressCount > 0 ? "warning.main" : "text.secondary" }
    ];
  }, [schedules, executions, inProgressCount]);

  const nextRunInfo = useMemo(() => {
    const enabled = schedules.filter(s => s.enabled && s.startTime);
    if (enabled.length === 0) return 'Not scheduled';

    // Simplified "Next Run" display: show the earliest today or tomorrow
    const sorted = [...enabled].sort((a, b) => a.startTime.localeCompare(b.startTime));
    const earliest = sorted[0];

    return `Scheduled: ${formatTime(earliest.startTime)} (${earliest.name})`;
  }, [schedules, formatTime]);

  const paginatedRuns = useMemo(() =>
    executions.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage),
    [executions, page, rowsPerPage]);

  const tableContent = useMemo(() => {
    if (loading && executions.length === 0) {
      return <TableRow><TableCell colSpan={6} align="center">Loading...</TableCell></TableRow>;
    }
    if (executions.length === 0) {
      return <TableRow><TableCell colSpan={6} align="center">No recent activity</TableCell></TableRow>;
    }
    return paginatedRuns.map((run) => (
      <TableRow key={run.execution_id} hover>
        <TableCell sx={{ textTransform: 'capitalize', fontWeight: 500, maxWidth: { xs: 120, sm: 200 }, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{run.workflow_name.replaceAll('-', ' ')}</TableCell>
        <TableCell sx={{ whiteSpace: 'nowrap', display: { xs: 'none', sm: 'table-cell' } }}>{formatDate(run.start_time)}</TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>{run.duration ? `${run.duration.toFixed(1)}s` : '-'}</TableCell>
        <TableCell>
          {run.output ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600, color: 'primary.main' }}>
                {run.output.queries_executed || 0}
              </Typography>
              {run.output.failures > 0 && (
                <Chip label={`${run.output.failures} failed`} size="small" color="error" sx={{ height: 20, fontSize: '0.7rem' }} />
              )}
            </Box>
          ) : '-'}
        </TableCell>
        <TableCell>
          <Chip label={run.status} size="small" color={STATUS_COLOR[run.status] || 'warning'} sx={{ fontWeight: 600, textTransform: 'capitalize' }} />
        </TableCell>
        <TableCell align="right">
          <IconButton size="small" color="primary" onClick={() => setSelectedRun(run)}><VisibilityIcon /></IconButton>
        </TableCell>
      </TableRow>
    ));
  }, [loading, executions.length, paginatedRuns]);

  return (
    <Box sx={{ p: { xs: 2, sm: 3, md: 4 }, maxWidth: '100%', boxSizing: 'border-box' }}>
      <Box sx={{ mb: { xs: 2, md: 4 } }}>
        <Typography variant="h4" sx={{ fontWeight: 700, mb: 1, fontSize: { xs: '1.5rem', sm: '2rem', md: '2.125rem' } }}>Dashboard</Typography>
        <Typography variant="body1" color="text.secondary">System monitoring and activity tracking</Typography>
      </Box>

      {inProgressCount > 0 ? (
        <Alert severity="warning" sx={{ mb: 3 }}>
          <strong>Workflow running:</strong> {runningWorkflows?.join(', ')}
        </Alert>
      ) : (
        <Alert severity="info" sx={{ mb: 3 }}>
          System operational. <strong>{nextRunInfo}</strong>
        </Alert>
      )}

      <Grid container spacing={{ xs: 1.5, sm: 2, md: 3 }} sx={{ mb: { xs: 2, md: 4 } }}>
        {stats.map((stat) => (
          <Grid size={{ xs: 6, sm: 6, md: 3 }} key={stat.title}>
            <Card variant="outlined" sx={{ borderRadius: 2 }}>
              <CardContent sx={{ textAlign: 'center', py: { xs: 1.5, sm: 2 }, px: { xs: 1, sm: 2 } }}>
                <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600, fontSize: { xs: '0.75rem', sm: '0.875rem' } }}>{stat.title}</Typography>
                <Typography variant="h4" sx={{ color: stat.color, fontWeight: 700, mt: 1, fontSize: { xs: '1.5rem', sm: '2rem', md: '2.125rem' } }}>{stat.value}</Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}
      </Grid>

      {runningWorkflows.length > 0 && (
        <Box sx={{ mb: 4 }}>
          <Typography variant="h6" sx={{ mb: 2, display: 'flex', alignItems: 'center', fontWeight: 600 }}>
            <Box sx={{ width: 10, height: 10, bgcolor: 'error.main', borderRadius: '50%', mr: 1, animation: 'pulse 1.5s infinite' }} />
            Live Monitor
          </Typography>
          {runningWorkflows.map(name => (
            <ExecutionMonitor key={name} workflowName={name} autoStart={true} />
          ))}
        </Box>
      )}

      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, flexWrap: 'wrap', gap: 1 }}>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>Recent Activity</Typography>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button size="small" onClick={loadExecutions}>Refresh</Button>
          <Button
            size="small"
            color="error"
            variant="outlined"
            startIcon={<DeleteSweepIcon />}
            onClick={() => setConfirmClear(true)}
            disabled={executions.length === 0}
          >
            Clear All
          </Button>
        </Box>
      </Box>

      <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2, overflowX: 'auto', width: '100%' }}>
        <Table sx={{ minWidth: { xs: 500, sm: 600 } }} size="small">
          <TableHead sx={{ bgcolor: 'action.hover' }}>
            <TableRow>
              <TableCell sx={{ fontWeight: 600 }}>Workflow</TableCell>
              <TableCell sx={{ fontWeight: 600, display: { xs: 'none', sm: 'table-cell' } }}>Started</TableCell>
              <TableCell sx={{ fontWeight: 600, display: { xs: 'none', md: 'table-cell' } }}>Duration</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Queries</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600 }}>Details</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {tableContent}
          </TableBody>
        </Table>
        <TablePagination
          component="div"
          count={executions.length}
          rowsPerPage={rowsPerPage}
          page={page}
          onPageChange={(e, p) => setPage(p)}
          onRowsPerPageChange={(e) => { setRowsPerPage(Number.parseInt(e.target.value, 10)); setPage(0); }}
        />
      </TableContainer>

      <Dialog open={Boolean(selectedRun)} onClose={() => setSelectedRun(null)} maxWidth="lg" fullWidth>
        <DialogTitle>Execution Details: {selectedRun?.workflow_name}</DialogTitle>
        <DialogContent dividers>
          {selectedRun && (
            <Box>
              <Box sx={{ mb: 3, p: 2, bgcolor: 'action.hover', borderRadius: 1 }}>
                <Grid container spacing={2} columns={6}>
                  <Grid size={2}>
                    <Typography variant="caption" color="text.secondary">Execution ID</Typography>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.85rem' }}>
                      {selectedRun.execution_id}
                    </Typography>
                  </Grid>
                  <Grid size={1}>
                    <Typography variant="caption" color="text.secondary">Duration</Typography>
                    <Typography variant="body2">{selectedRun.duration?.toFixed(2)}s</Typography>
                  </Grid>
                  <Grid size={1}>
                    <Typography variant="caption" color="text.secondary">Nodes Executed</Typography>
                    <Typography variant="body2">{selectedRun.nodes_executed || 0}</Typography>
                  </Grid>
                  {selectedRun.output && (
                    <>
                      <Grid size={1}>
                        <Typography variant="caption" color="text.secondary">Queries</Typography>
                        <Typography variant="body2" sx={{ fontWeight: 600, color: 'primary.main' }}>
                          {selectedRun.output.queries_executed || 0}
                        </Typography>
                      </Grid>
                      <Grid size={1}>
                        <Typography variant="caption" color="text.secondary">Failures</Typography>
                        <Typography variant="body2" sx={{ fontWeight: 600, color: selectedRun.output.failures > 0 ? 'error.main' : 'success.main' }}>
                          {selectedRun.output.failures || 0}
                        </Typography>
                      </Grid>
                    </>
                  )}
                  {selectedRun.output && (
                    <Grid size={6}>
                      <Typography variant="caption" color="text.secondary">Success Rate</Typography>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {selectedRun.output.queries_executed > 0
                          ? `${(((selectedRun.output.queries_executed - selectedRun.output.failures) / selectedRun.output.queries_executed) * 100).toFixed(1)}%`
                          : 'N/A'}
                      </Typography>
                    </Grid>
                  )}
                </Grid>
              </Box>

              {selectedRun.output?.error && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>Workflow Error</Typography>
                  <Typography variant="caption">{selectedRun.output.error}</Typography>
                </Alert>
              )}

              {selectedRun.output?.results && selectedRun.output.results.length > 0 && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>Query Results</Typography>
                  <TableContainer component={Paper} variant="outlined">
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 600 }}>Label</TableCell>
                          <TableCell sx={{ fontWeight: 600 }}>Result / Error</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {selectedRun.output.results.map((query, idx) => (
                          <TableRow key={query.query_id || idx} sx={{ bgcolor: query.success ? 'inherit' : 'error.lighter' }}>
                            <TableCell sx={{ fontWeight: 500, minWidth: 200 }}>{query.label || query.query_id}</TableCell>
                            <TableCell>
                              {query.success ? (
                                <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'text.primary' }}>
                                  {formatResult(query.result || query.data)}
                                </Typography>
                              ) : (
                                <Typography variant="body2" color="error">{query.error}</Typography>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Box>
              )}


            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedRun(null)}>Close</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={confirmClear} onClose={() => setConfirmClear(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Clear All Executions?</DialogTitle>
        <DialogContent>
          <Typography>
            This will permanently delete all {executions.length} execution{executions.length === 1 ? '' : 's'} from the history. This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmClear(false)} disabled={clearing}>Cancel</Button>
          <Button onClick={handleClearAll} color="error" variant="contained" disabled={clearing}>
            {clearing ? 'Clearing...' : 'Clear All'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
