import React, { useState, useMemo, memo } from 'react';
import {
  Box,
  Typography,
  TextField,
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
  MenuItem,
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
  Delete as DeleteIcon,
  Search as SearchIcon
} from '@mui/icons-material';
import { useScheduler } from '../../context/SchedulerContext';
import { useWorkflowStatus } from '../../context/WorkflowStatusContext';
import EditScheduleDialog from './EditScheduleDialog';
import agentApiClient from '../../services/agentApiClient.js';

const ScheduleList = memo(() => {
  const {
    schedules,
    deleteSchedule,
    updateSchedule,
    isLoading,
    getFreshSchedule,
    formatTime
  } = useScheduler();
  const { isWorkflowRunning, markWorkflowPending, clearWorkflowPending } = useWorkflowStatus();
  const [searchTerm, setSearchTerm] = useState('');
  const [editingSchedule, setEditingSchedule] = useState(null);
  const [anchorEl, setAnchorEl] = useState(null);
  const [selectedSchedule, setSelectedSchedule] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scheduleToDelete, setScheduleToDelete] = useState(null);
  const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'success' });

  const filteredSchedules = useMemo(() => {
    const searchLower = searchTerm.toLowerCase();
    return schedules.filter(s => {
      const name = (s.name || '').toLowerCase();
      const title = (s.title || '').toLowerCase();
      return name.includes(searchLower) || title.includes(searchLower);
    });
  }, [schedules, searchTerm]);

  const handleMenuOpen = (event, schedule) => {
    setAnchorEl(event.currentTarget);
    setSelectedSchedule(schedule);
  };

  const handleMenuClose = () => {
    setAnchorEl(null);
    setSelectedSchedule(null);
  };

  const handleEditSchedule = async (schedule) => {
    try {
      const fresh = await getFreshSchedule(schedule.name);
      setEditingSchedule(fresh);
    } catch (error) {
      console.error('Error loading schedule:', error);
      setSnackbar({ open: true, message: 'Failed to load schedule', severity: 'error' });
    }
    handleMenuClose();
  };

  const confirmDelete = () => {
    if (scheduleToDelete?.name) {
      deleteSchedule(scheduleToDelete.name);
    }
    setDeleteDialogOpen(false);
    setScheduleToDelete(null);
  };

  const handleRunNow = async (schedule) => {
    const name = schedule.name;
    try {
      markWorkflowPending(name);
      await agentApiClient.executeWorkflow(name, true);
      setSnackbar({ open: true, message: `Workflow "${name}" started`, severity: 'success' });
    } catch (e) {
      clearWorkflowPending(name);
      setSnackbar({ open: true, message: `Error: ${e.message}`, severity: 'error' });
    }
  };


  return (
    <Box>
      <Box sx={{ mb: 3 }}>
        <TextField
          size="small"
          placeholder="Search schedules..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          slotProps={{
            input: {
              startAdornment: (
                <SearchIcon sx={{ color: 'text.secondary', mr: 1, fontSize: 20 }} />
              ),
            },
          }}
          sx={{
            width: '100%',
            maxWidth: 400,
            '& .MuiOutlinedInput-root': {
              borderRadius: 2,
              bgcolor: 'background.paper',
              transition: 'all 0.2s ease-in-out',
              '&:hover': {
                boxShadow: '0 2px 8px rgba(0,0,0,0.05)',
                borderColor: 'primary.main',
              },
              '&.Mui-focused': {
                boxShadow: '0 4px 12px rgba(25, 118, 210, 0.1)',
              }
            }
          }}
        />
      </Box>

      {(() => {
        if (isLoading) {
          return (
            <Box display="flex" justifyContent="center" sx={{ py: 4 }}>
              <CircularProgress size={30} />
            </Box>
          );
        }
        if (filteredSchedules.length === 0) {
          return (
            <Alert severity="info">
              {schedules.length === 0 ? "No schedules found." : "No schedules match your search."}
            </Alert>
          );
        }
        return (
          <TableContainer component={Paper} elevation={0} variant="outlined" sx={{ borderRadius: 2 }}>
            <Table>
              <TableHead sx={{ bgcolor: 'action.hover' }}>
              <TableRow>
                <TableCell sx={{ fontWeight: 600 }}>Title</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Workflow</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Local Time</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Schedule</TableCell>
                <TableCell align="center" sx={{ fontWeight: 600 }}>Run</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filteredSchedules.map((schedule) => (
                <TableRow key={schedule.name} hover>
                  <TableCell sx={{ fontWeight: 500 }}>{schedule.title || schedule.name}</TableCell>
                  <TableCell>
                    <Chip label={schedule.name} size="small" variant="outlined" />
                  </TableCell>
                  <TableCell>{schedule.startTime ? formatTime(schedule.startTime) : '-'}</TableCell>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', color: 'text.secondary' }}>
                      {schedule.schedule || 'Manual'}
                    </Typography>
                  </TableCell>
                  <TableCell align="center">
                    {isWorkflowRunning(schedule.name) ? (
                      <CircularProgress size={20} />
                    ) : (
                      <IconButton size="small" color="primary" onClick={() => handleRunNow(schedule)}>
                        <PlayArrowIcon />
                      </IconButton>
                    )}
                  </TableCell>
                  <TableCell align="right">
                    <IconButton size="small" onClick={(e) => handleMenuOpen(e, schedule)}>
                      <MoreVertIcon />
                    </IconButton>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
        );
      })()}

      <Menu anchorEl={anchorEl} open={Boolean(anchorEl)} onClose={handleMenuClose}>
        <MenuItem onClick={() => handleEditSchedule(selectedSchedule)}>
          <EditIcon fontSize="small" sx={{ mr: 1 }} /> Edit
        </MenuItem>
        <MenuItem onClick={() => { setScheduleToDelete(selectedSchedule); setDeleteDialogOpen(true); handleMenuClose(); }} sx={{ color: 'error.main' }}>
          <DeleteIcon fontSize="small" sx={{ mr: 1 }} /> Delete
        </MenuItem>
      </Menu>

      <Dialog open={deleteDialogOpen} onClose={() => setDeleteDialogOpen(false)}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete the schedule "{scheduleToDelete?.title || scheduleToDelete?.name}"?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
          <Button onClick={confirmDelete} color="error" variant="contained">Delete</Button>
        </DialogActions>
      </Dialog>

      {editingSchedule && (
        <EditScheduleDialog
          open={!!editingSchedule}
          schedule={editingSchedule}
          onClose={() => setEditingSchedule(null)}
          onUpdate={(updated) => { updateSchedule(editingSchedule.name, updated); setEditingSchedule(null); }}
        />
      )}

      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
      >
        <Alert severity={snackbar.severity} sx={{ width: '100%' }}>{snackbar.message}</Alert>
      </Snackbar>
    </Box>
  );
});

export default ScheduleList;
