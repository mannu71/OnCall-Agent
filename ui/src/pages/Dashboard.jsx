import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Box, Typography, Grid, Card, CardContent, Alert, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Chip, IconButton, Dialog, DialogTitle, DialogContent, DialogActions, Button, TablePagination } from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';
import { useScheduler } from '../context/SchedulerContext';
import ExecutionMonitor from '../components/monitoring/ExecutionMonitor';
import agentApiClient from '../services/agentApiClient';

export default function Dashboard() {
  const { schedules, formatTime } = useScheduler();
  const { runningWorkflows, count: inProgressCount } = useWorkflowStatus();

  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);

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

  useEffect(() => {
    loadExecutions();
    const interval = setInterval(loadExecutions, 30000);
    return () => clearInterval(interval);
  }, [loadExecutions]);

  const stats = useMemo(() => {
    const totalSchedules = schedules.length;
    const enabledSchedules = schedules.filter(s => s.enabled).length;
    const failedLast24h = executions.filter(e =>
      e.status === 'failed' &&
      (new Date() - new Date(e.start_time)) < 24 * 60 * 60 * 1000
    ).length;

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

  return (
    <Box sx={{ p: 4 }}>
      <Box sx={{ mb: 4 }}>
        <Typography variant="h4" sx={{ fontWeight: 700, mb: 1 }}>Dashboard</Typography>
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

      <Grid container spacing={3} sx={{ mb: 4 }}>
        {stats.map((stat, i) => (
          <Grid item xs={12} sm={6} md={3} key={i}>
            <Card variant="outlined" sx={{ borderRadius: 2 }}>
              <CardContent sx={{ textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }}>{stat.title}</Typography>
                <Typography variant="h4" sx={{ color: stat.color, fontWeight: 700, mt: 1 }}>{stat.value}</Typography>
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

      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>Recent Activity</Typography>
        <Button size="small" onClick={loadExecutions}>Refresh</Button>
      </Box>

      <TableContainer component={Paper} variant="outlined" sx={{ borderRadius: 2 }}>
        <Table>
          <TableHead sx={{ bgcolor: 'action.hover' }}>
            <TableRow>
              <TableCell sx={{ fontWeight: 600 }}>Workflow</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Started</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Duration</TableCell>
              <TableCell sx={{ fontWeight: 600 }}>Status</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600 }}>Details</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {loading && executions.length === 0 ? (
              <TableRow><TableCell colSpan={5} align="center">Loading history...</TableCell></TableRow>
            ) : executions.length === 0 ? (
              <TableRow><TableCell colSpan={5} align="center">No recent activity</TableCell></TableRow>
            ) : (
              paginatedRuns.map((run) => (
                <TableRow key={run.execution_id} hover>
                  <TableCell sx={{ textTransform: 'capitalize', fontWeight: 500 }}>{run.workflow_name.replace(/-/g, ' ')}</TableCell>
                  <TableCell>{new Date(run.start_time).toLocaleString()}</TableCell>
                  <TableCell>{run.duration_seconds ? `${run.duration_seconds.toFixed(1)}s` : '-'}</TableCell>
                  <TableCell>
                    <Chip
                      label={run.status}
                      size="small"
                      color={run.status === 'success' ? 'success' : run.status === 'failed' ? 'error' : 'warning'}
                      sx={{ fontWeight: 600, textTransform: 'capitalize' }}
                    />
                  </TableCell>
                  <TableCell align="right">
                    <IconButton size="small" color="primary" onClick={() => setSelectedRun(run)}>
                      <VisibilityIcon />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
        <TablePagination
          component="div"
          count={executions.length}
          rowsPerPage={rowsPerPage}
          page={page}
          onPageChange={(e, p) => setPage(p)}
          onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }}
        />
      </TableContainer>

      <Dialog open={Boolean(selectedRun)} onClose={() => setSelectedRun(null)} maxWidth="md" fullWidth>
        <DialogTitle>Execution Details: {selectedRun?.workflow_name}</DialogTitle>
        <DialogContent dividers>
          {selectedRun && (
            <Box>
              <Typography variant="body2" color="text.secondary" gutterBottom>Execution ID: {selectedRun.execution_id}</Typography>
              <Box sx={{ mt: 2 }}>
                {selectedRun.task_results?.map((task, i) => (
                  <Box key={i} sx={{ mb: 2, p: 2, bgcolor: 'action.hover', borderRadius: 1 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>{task.task_name}</Typography>
                    <Typography variant="body2" color={task.status === 'success' ? 'success.main' : 'error.main'}>
                      Status: {task.status} | Duration: {task.duration_seconds?.toFixed(1)}s
                    </Typography>
                    {task.error && <Typography variant="caption" color="error">{task.error}</Typography>}
                  </Box>
                ))}
              </Box>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedRun(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
