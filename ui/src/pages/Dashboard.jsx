import React, { useState, useEffect } from 'react';
import { Box, Typography, Grid, Card, CardContent, Alert, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Chip, IconButton, Dialog, DialogTitle, DialogContent, DialogActions, Button, TablePagination } from '@mui/material';
import VisibilityIcon from '@mui/icons-material/Visibility';
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';

// Simple cron next run calculator for common patterns
// Note: Cron expressions are stored in UTC, this converts to local time for display
const getNextRunFromCron = (cronExpression) => {
  try {
    const parts = cronExpression.trim().split(/\s+/);
    if (parts.length !== 5) return null;
    
    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
    const now = new Date();
    
    // Handle simple daily cron: "M H * * *"
    if (dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
      const targetHourUTC = parseInt(hour);
      const targetMinuteUTC = parseInt(minute);
      
      // Create a date in UTC and let JavaScript convert to local
      let next = new Date();
      next.setUTCHours(targetHourUTC, targetMinuteUTC, 0, 0);
      
      // If the time has passed today (in UTC), move to tomorrow
      if (next <= now) {
        next.setUTCDate(next.getUTCDate() + 1);
      }
      return next;
    }
    
    // For more complex crons, return null (could be extended)
    return null;
  } catch {
    return null;
  }
};

const formatNextRun = (date) => {
  if (!date) return 'Unknown';
  
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  
  const timeStr = date.toLocaleTimeString('en-US', { 
    hour: 'numeric', 
    minute: '2-digit',
    hour12: true 
  });
  
  // Check if it's today
  if (date.toDateString() === now.toDateString()) {
    return `Today at ${timeStr}`;
  }
  
  // Check if it's tomorrow
  if (date.toDateString() === tomorrow.toDateString()) {
    return `Tomorrow at ${timeStr}`;
  }
  
  // Otherwise show date
  const dateStr = date.toLocaleDateString('en-US', { 
    weekday: 'long',
    month: 'short', 
    day: 'numeric' 
  });
  return `${dateStr} at ${timeStr}`;
};

// Helper function to parse result if it's a JSON string
const parseResultIfNeeded = (result) => {
  if (typeof result === 'string') {
    try {
      return JSON.parse(result);
    } catch (e) {
      return result;
    }
  }
  return result;
};

export default function Dashboard() {
  const [workflowRuns, setWorkflowRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [openDialog, setOpenDialog] = useState(false);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(5);
  const [nextScheduleRun, setNextScheduleRun] = useState(null);
  const { runningWorkflows, count: workflowsInProgressCount } = useWorkflowStatus();
  const [stats, setStats] = useState([
    { title: "Profiles Updated", value: "-", color: "primary.main" },
    { title: "Failed Schedules", value: "-", color: "error.main" },
    { title: "Pending Alerts", value: "-", color: "warning.main" },
    { title: "Uptime", value: "-", color: "success.main" }
  ]);

  useEffect(() => {
    const loadData = async () => {
      try {
        console.log('Dashboard: checking electronAPI...', !!window.electronAPI);
        if (window.electronAPI?.loadWorkflowRuns) {
          // Load workflow runs
          const runs = await window.electronAPI.loadWorkflowRuns();
          setWorkflowRuns(runs);

          // Load latest workflow result for stats
          let profilesUpdatedValue = "0";
          let failedSchedulesValue = "0";
          let kycAlertsValue = "0";
          let publishedEmailsValue = "0 / 0";
          let metricsRefreshValue = "No";
          let schedulesRanValue = "0";
          let searchResultsValue = "0";
          let profilesLockedValue = "0";

          if (window.electronAPI?.loadLatestWorkflowResult) {
            const latestResult = await window.electronAPI.loadLatestWorkflowResult();
            
            if (latestResult && latestResult.results) {
              // Extract stats from the latest workflow result
              // Support both old format (metadata.label) and new format (label at top level)
              const profilesUpdated = latestResult.results.find(r => 
                r.label === "Profiles Updated" || r.metadata?.label === "Profiles Updated"
              );
              const failedSchedules = latestResult.results.find(r => 
                r.label === "Failed Schedules" || r.metadata?.label === "Failed Schedules"
              );
              const profileKycAlerts = latestResult.results.find(r => 
                r.label === "Profile KYC Alerts Count" || r.metadata?.label === "Profile KYC Alerts Count"
              );
              const emailNotifications = latestResult.results.find(r => 
                r.label === "customer email notifications" || r.metadata?.label === "customer email notifications"
              );
              const metricsRefresh = latestResult.results.find(r => 
                r.label === "Monitoring Metrics Refreshes (Today)" || r.metadata?.label === "Monitoring Metrics Refreshes (Today)"
              );
              const schedulesRan = latestResult.results.find(r => 
                r.label === "Schedules Ran Count" || r.metadata?.label === "Schedules Ran Count"
              );
              const searchResults = latestResult.results.find(r => 
                r.label === "Search Results Count" || r.metadata?.label === "Search Results Count"
              );
              const profilesLocked = latestResult.results.find(r => 
                r.label === "profiles locked in last 3 days" || r.metadata?.label === "profiles locked in last 3 days"
              );
              
              // Parse results if they're JSON strings
              const parsedProfilesUpdated = profilesUpdated ? parseResultIfNeeded(profilesUpdated.result) : null;
              const parsedFailedSchedules = failedSchedules ? parseResultIfNeeded(failedSchedules.result) : null;
              const parsedKycAlerts = profileKycAlerts ? parseResultIfNeeded(profileKycAlerts.result) : null;
              const parsedSchedulesRan = schedulesRan ? parseResultIfNeeded(schedulesRan.result) : null;
              const parsedSearchResults = searchResults ? parseResultIfNeeded(searchResults.result) : null;
              const parsedProfilesLocked = profilesLocked ? parseResultIfNeeded(profilesLocked.result) : null;
              const parsedEmailNotifications = emailNotifications ? parseResultIfNeeded(emailNotifications.result) : null;
              const parsedMetricsRefresh = metricsRefresh ? parseResultIfNeeded(metricsRefresh.result) : null;
              
              profilesUpdatedValue = parsedProfilesUpdated?.[0]?.total_count || "0";
              failedSchedulesValue = parsedFailedSchedules?.[0]?.count || "0";
              kycAlertsValue = parsedKycAlerts?.[0]?.value || "0";
              schedulesRanValue = parsedSchedulesRan?.[0]?.count || "0";
              searchResultsValue = parsedSearchResults?.[0]?.searchresultscount || "0";
              profilesLockedValue = parsedProfilesLocked?.[0]?.["profiles locked in last 3 days"] || "0";
              
              if (parsedEmailNotifications?.[0]) {
                const published = parsedEmailNotifications[0].published_count || "0";
                const total = parsedEmailNotifications[0].total_count || "0";
                publishedEmailsValue = `${published} / ${total}`;
              }

              if (parsedMetricsRefresh?.[0]) {
                metricsRefreshValue = parsedMetricsRefresh[0].is_completed ? "Yes" : "No";
              }

              console.log('Dashboard: extracted stats -', {
                profilesUpdated: profilesUpdatedValue,
                failedSchedules: failedSchedulesValue,
                kycAlerts: kycAlertsValue,
                publishedEmails: publishedEmailsValue,
                metricsRefresh: metricsRefreshValue,
                schedulesRan: schedulesRanValue,
                searchResults: searchResultsValue,
                profilesLocked: profilesLockedValue
              });
            }
          }

          // Update stats with all values
          setStats([
            { 
              title: "Profiles Updated", 
              value: profilesUpdatedValue, 
              color: "primary.main" 
            },
            { 
              title: "Profiles Locked (3 days)", 
              value: profilesLockedValue, 
              color: parseInt(profilesLockedValue) > 0 ? "warning.main" : "success.main" 
            },
            { 
              title: "Published Emails", 
              value: publishedEmailsValue, 
              color: "info.main" 
            },
            { 
              title: "Metrics Refreshed", 
              value: metricsRefreshValue, 
              color: metricsRefreshValue === "Yes" ? "success.main" : "error.main" 
            },
            { 
              title: "KYC Alerts", 
              value: kycAlertsValue, 
              color: "warning.main" 
            },
            { 
              title: "Failed Schedules", 
              value: failedSchedulesValue, 
              color: parseInt(failedSchedulesValue) > 0 ? "error.main" : "success.main" 
            },
            { 
              title: "Schedules Ran", 
              value: schedulesRanValue, 
              color: "primary.main" 
            }
          ]);

          // Load schedules and calculate next run time
          if (window.electronAPI?.loadSchedules) {
            const schedules = await window.electronAPI.loadSchedules();
            const enabledSchedules = schedules.filter(s => s.enabled && s.schedule);
            
            // Find the earliest next run among all enabled schedules
            let earliestNextRun = null;
            for (const schedule of enabledSchedules) {
              const nextRun = getNextRunFromCron(schedule.schedule);
              if (nextRun && (!earliestNextRun || nextRun < earliestNextRun)) {
                earliestNextRun = nextRun;
              }
            }
            setNextScheduleRun(earliestNextRun);
          }
        } else {
          console.log('Dashboard: electronAPI not available, not in Electron environment');
        }
      } catch (error) {
        console.error('Error loading workflow runs:', error);
      } finally {
        setLoading(false);
      }
    };
    
    loadData();
    
    // Auto-reload every 3 seconds for responsive in-progress detection
    const interval = setInterval(loadData, 3000);
    
    return () => clearInterval(interval);
  }, []);

  const handleViewDetails = async (run) => {
    try {
      // Load full workflow data from file
      if (window.electronAPI?.loadWorkflowResult && run.filename) {
        const fullData = await window.electronAPI.loadWorkflowResult(run.filename);
        setSelectedRun({ ...run, fullData });
      } else {
        setSelectedRun(run);
      }
      setOpenDialog(true);
    } catch (error) {
      console.error('Error loading workflow details:', error);
      setSelectedRun(run);
      setOpenDialog(true);
    }
  };

  const handleCloseDialog = () => {
    setOpenDialog(false);
    setSelectedRun(null);
  };

  const handleChangePage = (event, newPage) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event) => {
    setRowsPerPage(parseInt(event.target.value, 10));
    setPage(0);
  };

  const handleClearTableData = async () => {
    try {
      if (window.electronAPI?.clearWorkflowOutputs) {
        const result = await window.electronAPI.clearWorkflowOutputs();
        if (result.success) {
          console.log(`Cleared ${result.deletedCount} output files`);
        } else {
          console.error('Error clearing outputs:', result.error);
        }
      }
      setWorkflowRuns([]);
      setPage(0);
    } catch (error) {
      console.error('Error clearing workflow outputs:', error);
    }
  };

  // Get paginated data
  const paginatedRuns = workflowRuns.slice(
    page * rowsPerPage,
    page * rowsPerPage + rowsPerPage
  );

  return (
    <Box
      sx={{
        minHeight: "100vh",
        backgroundColor: "background.default",
      }}
    >
      <Box>
        {/* Header */}
        <Box sx={{ mb: 4 }}>
          <Typography variant="h4" sx={{ fontWeight: 600, mb: 1 }}>
            Dashboard
          </Typography>
          <Typography variant="body1" color="text.secondary">
            Monitor your on-call activities and statistics
          </Typography>
        </Box>

      {/* Banner OUTSIDE the grid */}
      {workflowsInProgressCount > 0 ? (
        <Alert severity="warning" sx={{ mb: 3 }}>
          <strong>Workflow running:</strong> {runningWorkflows?.join(', ') || 'Processing...'}
        </Alert>
      ) : (
        <Alert severity="info" sx={{ mb: 3 }}>
          You are currently on-call. Next scheduled run: {formatNextRun(nextScheduleRun)}
        </Alert>
      )}

      {/* MAIN GRID */}
      <Grid container spacing={2}>

        {/* --- Stats Cards --- */}
        {stats.map((stat, index) => (
          <Grid item xs={6} sm={4} md={3} lg={3} key={index}>
            <Card sx={{ height: 120, display: "flex" }}>
              <CardContent sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', textAlign: 'center', p: 2 }}>
                <Typography variant="body2" color="text.secondary" gutterBottom sx={{ fontSize: '0.85rem', fontWeight: 500 }}>
                  {stat.title}
                </Typography>
                <Typography variant="h4" sx={{ color: stat.color, fontWeight: 600 }}>
                  {stat.value}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        ))}

      </Grid>

      {/* Schedules Table */}
      <Box sx={{ mt: 4 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            Recent Workflow Runs
          </Typography>
          {workflowRuns.length > 0 && (
            <Button
              variant="outlined"
              color="error"
              size="small"
              startIcon={<DeleteSweepIcon />}
              onClick={handleClearTableData}
            >
              Clear
            </Button>
          )}
        </Box>
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>Workflow Name</TableCell>
                <TableCell>Last Run</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Results</TableCell>
                <TableCell align="center">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5} align="center">Loading workflow runs...</TableCell>
                </TableRow>
              ) : workflowRuns.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} align="center">No workflow runs found</TableCell>
                </TableRow>
              ) : (
                paginatedRuns.map((run, index) => (
                  <TableRow key={index} hover>
                    <TableCell sx={{ textTransform: 'capitalize' }}>
                      {run.workflow.replace(/-/g, ' ')}
                    </TableCell>
                    <TableCell>
                      {new Date(run.timestamp).toLocaleString()}
                    </TableCell>
                    <TableCell>
                      <Chip 
                        label={run.success ? 'Success' : 'Failed'} 
                        color={run.success ? 'success' : 'error'}
                        size="small"
                      />
                    </TableCell>
                    <TableCell>{run.resultCount} steps</TableCell>
                    <TableCell align="center">
                      <IconButton 
                        size="small" 
                        color="primary"
                        onClick={() => handleViewDetails(run)}
                      >
                        <VisibilityIcon />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          rowsPerPageOptions={[5, 10, 25, 50]}
          component="div"
          count={workflowRuns.length}
          rowsPerPage={rowsPerPage}
          page={page}
          onPageChange={handleChangePage}
          onRowsPerPageChange={handleChangeRowsPerPage}
        />
      </Box>

      {/* Details Dialog */}
      <Dialog 
        open={openDialog} 
        onClose={handleCloseDialog}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Workflow Run Details
          {selectedRun && (
            <Typography variant="body2" color="text.secondary">
              {selectedRun.workflow} - {new Date(selectedRun.timestamp).toLocaleString()}
            </Typography>
          )}
        </DialogTitle>
        <DialogContent dividers>
          {selectedRun && (
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Status: <Chip 
                  label={selectedRun.success ? 'Success' : 'Failed'} 
                  color={selectedRun.success ? 'success' : 'error'}
                  size="small"
                />
              </Typography>
              <Typography variant="subtitle2" gutterBottom>
                Duration: {selectedRun.duration}
              </Typography>
              
              {selectedRun.fullData?.results && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 600, mb: 2 }}>
                    Query Results:
                  </Typography>
                  <TableContainer component={Paper} variant="outlined">
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell sx={{ fontWeight: 600 }}>Label</TableCell>
                          <TableCell sx={{ fontWeight: 600 }}>Result</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {selectedRun.fullData.results.map((result, index) => {
                          // Parse result if it's a JSON string
                          const parsedResult = parseResultIfNeeded(result.result);
                          const displayLabel = result.label || result.metadata?.label || result.step;
                          
                          return (
                          <TableRow key={index} hover>
                            <TableCell sx={{ verticalAlign: 'top', width: '40%' }}>
                              {displayLabel}
                            </TableCell>
                            <TableCell>
                              {result.success ? (
                                parsedResult && Array.isArray(parsedResult) && parsedResult.length > 0 ? (
                                  <Box>
                                    {parsedResult.map((row, rowIndex) => {
                                      const isLastRow = rowIndex >= parsedResult.length - 1;
                                      const marginBottom = isLastRow ? 0 : 1;
                                      const rowKey = `row-${rowIndex}-${JSON.stringify(row).substring(0, 20)}`;
                                      
                                      return (
                                      <Box key={rowKey} sx={{ mb: marginBottom }}>
                                        {Object.entries(row).map(([key, value]) => {
                                          let displayValue;
                                          if (typeof value === 'boolean') {
                                            displayValue = value ? 'Yes' : 'No';
                                          } else {
                                            displayValue = String(value);
                                          }
                                          
                                          // If there's only one key-value pair, show just the value
                                          if (Object.keys(row).length === 1) {
                                            return (
                                              <Typography key={key} variant="body2">
                                                {displayValue}
                                              </Typography>
                                            );
                                          }
                                          // Otherwise show key: value format
                                          return (
                                            <Typography key={key} variant="body2">
                                              <strong>{key}:</strong> {displayValue}
                                            </Typography>
                                          );
                                        })}
                                      </Box>
                                      );
                                    })}
                                  </Box>
                                ) : (
                                  <Typography variant="body2" color="text.secondary">No data</Typography>
                                )
                              ) : (
                                <Typography variant="body2" color="error">
                                  {result.error}
                                </Typography>
                              )}
                            </TableCell>
                          </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </TableContainer>
                </Box>
              )}
              

            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDialog}>Close</Button>
        </DialogActions>
      </Dialog>

      </Box>
    </Box>
  );
}
