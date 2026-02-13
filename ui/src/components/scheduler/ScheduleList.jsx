import React, { useState, useMemo, useCallback, memo } from 'react';
import {
  Box,
  Typography,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Grid,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  IconButton,
  Menu,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Button,
  CircularProgress,
  Snackbar
} from '@mui/material';
import {
  MoreVert as MoreVertIcon,
  PlayArrow as PlayArrowIcon,
  Edit as EditIcon,
  Delete as DeleteIcon
} from '@mui/icons-material';
import { useScheduler } from '../../context/SchedulerContext';
import { useWorkflowStatus } from '../../context/WorkflowStatusContext';
import EditScheduleDialog from './EditScheduleDialog';
import agentApiClient from '../../services/agentApiClient.js';

const ScheduleList = memo(() => {
  const { schedules, deleteSchedule, updateSchedule, isLoading } = useScheduler();
  const { isWorkflowRunning, markWorkflowPending, clearWorkflowPending } = useWorkflowStatus();
  const [searchTerm, setSearchTerm] = useState('');
  // Removed type filtering
  const [editingSchedule, setEditingSchedule] = useState(null);
  const [anchorEl, setAnchorEl] = useState(null);
  const [selectedSchedule, setSelectedSchedule] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scheduleToDelete, setScheduleToDelete] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  const filteredAndSortedSchedules = useMemo(() => {
    const searchLower = searchTerm.toLowerCase();
    let filtered = schedules.filter(schedule => {
      const title = (schedule.title || schedule.name || '').toLowerCase();
      const workflowName = (schedule.workflow || schedule.name || '').toLowerCase();
      const matchesSearch = title.includes(searchLower) || workflowName.includes(searchLower);
      return matchesSearch;
    });

    // Schedules are displayed in the order they appear
    return filtered;
  }, [schedules, searchTerm]);

  const handleEdit = useCallback((schedule) => {
    setEditingSchedule(schedule);
    setAnchorEl(null);
    setSelectedSchedule(null);
  }, []);

  const handleDelete = useCallback((schedule) => {
    console.log('handleDelete called with schedule:', schedule);
    setScheduleToDelete(schedule);
    setDeleteDialogOpen(true);
    setAnchorEl(null);
    setSelectedSchedule(null);
  }, []);

  const handleMenuOpen = useCallback((event, schedule) => {
    setAnchorEl(event.currentTarget);
    setSelectedSchedule(schedule);
  }, []);

  const handleMenuClose = useCallback(() => {
    setAnchorEl(null);
    setSelectedSchedule(null);
  }, []);

  const confirmDelete = useCallback(() => {
    console.log('confirmDelete called with scheduleToDelete:', scheduleToDelete);
    if (scheduleToDelete && scheduleToDelete.id) {
      console.log('Calling deleteSchedule with id:', scheduleToDelete.id);
      deleteSchedule(scheduleToDelete.id);
    } else {
      console.error('No scheduleToDelete or missing id:', scheduleToDelete);
    }
    setDeleteDialogOpen(false);
    setScheduleToDelete(null);
  }, [scheduleToDelete, deleteSchedule]);

  const handleCloseEdit = useCallback(() => {
    setEditingSchedule(null);
  }, []);

  const handleUpdateSchedule = useCallback((updatedSchedule) => {
    updateSchedule(editingSchedule.id, updatedSchedule);
    setEditingSchedule(null);
  }, [editingSchedule, updateSchedule]);

  const handleRunNow = useCallback(async (schedule) => {
    const workflowName = schedule.workflow;

    try {
      // Mark as pending immediately for instant UI feedback
      markWorkflowPending(workflowName);

      // Execute workflow in background
      await agentApiClient.executeWorkflow(workflowName, true); // background=true

      setSnackbar({
        open: true,
        message: `Workflow "${workflowName}" started successfully`,
        severity: 'success'
      });
    } catch (e) {
      console.error('Error executing workflow:', e);

      // Clear pending state on error
      clearWorkflowPending(workflowName);

      setSnackbar({
        open: true,
        message: `Failed to execute workflow: ${e.message}`,
        severity: 'error'
      });
    }
  }, [markWorkflowPending, clearWorkflowPending]);

  // Removed type color helper

  const formatTime = useCallback((timeString) => {
    const [hours, minutes] = timeString.split(':');
    const date = new Date();
    date.setHours(parseInt(hours), parseInt(minutes));
    return date.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true
    });
  }, []);

  return (
    <Box>
      {/* Filters and Search */}
      <Box mb={3}>
        <Grid container spacing={2} alignItems="center">
          <Grid item xs={12} md={4}>
            <TextField
              fullWidth
              label="Search schedules"
              variant="outlined"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              size="small"
            />
          </Grid>
          {/* Removed Type filter */}
          {/* Removed Sort By control per request */}
        </Grid>
      </Box>

      {/* Schedule List */}
      {isLoading ? (
        <Box display="flex" justifyContent="center" alignItems="center" sx={{ py: 4 }}>
          <CircularProgress />
          <Typography variant="body1" sx={{ ml: 2 }}>
            Loading schedules...
          </Typography>
        </Box>
      ) : filteredAndSortedSchedules.length === 0 ? (
        <Alert severity="info">
          {schedules.length === 0
            ? "No schedules created yet. Click 'Add Schedule' to create your first schedule."
            : "No schedules match your current filters."
          }
        </Alert>
      ) : (
        <Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Showing {filteredAndSortedSchedules.length} of {schedules.length} schedules
          </Typography>

          <TableContainer component={Paper}>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>Title</TableCell>
                  <TableCell>Workflow</TableCell>
                  <TableCell>Time</TableCell>
                  <TableCell align="center">Run</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredAndSortedSchedules.map((schedule) => {
                  const title = schedule.title || schedule.name || 'Untitled';
                  const workflowName = schedule.workflow || schedule.name || '';
                  return (
                    <TableRow key={schedule.id || title} hover>
                      <TableCell>
                        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                          {title}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {workflowName && (
                          <Chip
                            label={workflowName.charAt(0).toUpperCase() + workflowName.slice(1)}
                            color="primary"
                            size="small"
                          />
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">
                          {schedule.startTime ? formatTime(schedule.startTime) : (schedule.schedule || '-')}
                        </Typography>
                      </TableCell>
                      <TableCell align="center">
                        {isWorkflowRunning(workflowName) ? (
                          <CircularProgress size={20} />
                        ) : (
                          <IconButton
                            size="small"
                            onClick={() => handleRunNow(schedule)}
                            title="Run now"
                            aria-label="Run workflow now"
                            color="primary"
                          >
                            <PlayArrowIcon />
                          </IconButton>
                        )}
                      </TableCell>
                      <TableCell align="right">
                        <IconButton
                          size="small"
                          onClick={(e) => handleMenuOpen(e, schedule)}
                        >
                          <MoreVertIcon />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}

      {/* Action Menu */}
      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleMenuClose}
      >
        <MenuItem onClick={() => handleEdit(selectedSchedule)}>
          <EditIcon fontSize="small" sx={{ mr: 1 }} />
          Edit
        </MenuItem>
        <MenuItem onClick={() => handleDelete(selectedSchedule)} sx={{ color: 'error.main' }}>
          <DeleteIcon fontSize="small" sx={{ mr: 1 }} />
          Delete
        </MenuItem>
      </Menu>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        disablePortal
        keepMounted={false}
        aria-labelledby="delete-dialog-title"
        aria-describedby="delete-dialog-description"
      >
        <DialogTitle id="delete-dialog-title">Confirm Delete</DialogTitle>
        <DialogContent>
          <DialogContentText id="delete-dialog-description">
            Are you sure you want to delete the schedule "{(scheduleToDelete?.title || scheduleToDelete?.name || 'Untitled')}"?
            This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={confirmDelete} color="error" variant="contained">
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Edit Dialog */}
      {editingSchedule && (
        <EditScheduleDialog
          open={!!editingSchedule}
          schedule={editingSchedule}
          onClose={handleCloseEdit}
          onUpdate={handleUpdateSchedule}
        />
      )}

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          severity={snackbar.severity}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
});

export default ScheduleList;
