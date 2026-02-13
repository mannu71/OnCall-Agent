import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Button,
  Grid,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  Typography
} from '@mui/material';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { TimePicker } from '@mui/x-date-pickers/TimePicker';
import { dateToCron, dateToLocalTimeString } from '../../utils/cronUtils';
import agentApiClient from '../../services/agentApiClient.js';

const EditScheduleDialog = ({ open, schedule, onClose, onUpdate }) => {
  const [formData, setFormData] = useState({
    title: '',
    startTime: new Date(),
    workflow: '',
    recurrence: 'daily'
  });

  const [workflows, setWorkflows] = useState([]);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    const loadWorkflows = async () => {
      try {
        const wf = await agentApiClient.listWorkflows();
        // Filter to only show workflow type (not agent type)
        const workflowTypeOnly = wf.filter(w => w.type !== 'agent');
        setWorkflows(workflowTypeOnly);
      } catch (error) {
        console.error('Error loading workflows:', error);
        setWorkflows([]);
      }
    };
    
    if (open) {
      loadWorkflows();
    }
  }, [open]);

  useEffect(() => {
    if (schedule) {
      // Parse time from existing schedule
      const timeString = schedule.startTime || '09:00';
      const [hours, minutes] = timeString.split(':');
      const timeObj = new Date();
      timeObj.setHours(parseInt(hours, 10));
      timeObj.setMinutes(parseInt(minutes, 10));

      setFormData({
        title: schedule.title || '',
        startTime: timeObj,
        workflow: (schedule.workflow || ''),
        recurrence: (schedule.recurrence || 'daily')
      });
    }
  }, [schedule]);

  const handleChange = (field) => (event) => {
    const value = event.target ? event.target.value : event;
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
    
    // Clear error when user starts typing
    if (errors[field]) {
      setErrors(prev => ({
        ...prev,
        [field]: ''
      }));
    }
  };

  const validateForm = () => {
    const newErrors = {};

    if (!formData.title.trim()) {
      newErrors.title = 'Title is required';
    }

    if (!formData.startTime || isNaN(new Date(formData.startTime).getTime())) {
      newErrors.startTime = 'Valid time is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = () => {
    if (!validateForm()) {
      return;
    }

    const timeObj = new Date(formData.startTime);
    
    // Use utility functions for conversion
    const localTimeStr = dateToLocalTimeString(timeObj);
    const cronSchedule = dateToCron(timeObj, formData.recurrence);

    const updatedData = {
      title: formData.title,
      workflow: formData.workflow,
      recurrence: formData.recurrence,
      startTime: localTimeStr, // Local time for display
      schedule: cronSchedule // UTC cron for backend
    };

    onUpdate(updatedData);
    handleClose();
  };

  const handleClose = () => {
    setErrors({});
    onClose();
  };

  return (
    <Dialog 
      open={open} 
      onClose={handleClose} 
      maxWidth="md" 
      fullWidth
      disablePortal
      keepMounted={false}
      aria-labelledby="edit-schedule-dialog-title"
      aria-describedby="edit-schedule-dialog-description"
    >
      <DialogTitle id="edit-schedule-dialog-title">
        <Typography variant="h6" component="h2">
          Edit Schedule
        </Typography>
      </DialogTitle>
      
      <DialogContent dividers id="edit-schedule-dialog-description">
        <Grid container spacing={3}>
          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Title"
              value={formData.title}
              onChange={handleChange('title')}
              error={!!errors.title}
              helperText={errors.title}
              required
            />
          </Grid>

          <Grid item xs={12} sm={3}>
            <FormControl fullWidth>
              <InputLabel>Workflow</InputLabel>
              <Select
                value={formData.workflow}
                onChange={handleChange('workflow')}
                label="Workflow"
                error={!!errors.workflow}
              >
                {workflows.length === 0 ? (
                  <MenuItem value="" disabled>
                    No workflows available
                  </MenuItem>
                ) : (
                  workflows.map((wf) => (
                    <MenuItem key={wf.id} value={wf.name}>
                      {wf.name}
                    </MenuItem>
                  ))
                )}
              </Select>
              {errors.workflow && (
                <Typography variant="caption" color="error" sx={{ mt: 0.5, ml: 1.5 }}>
                  {errors.workflow}
                </Typography>
              )}
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={6}>
            <FormControl fullWidth>
              <InputLabel id="recurrence-label">
                Recurrence
              </InputLabel>
              <Select
                labelId="recurrence-label"
                value={formData.recurrence}
                onChange={handleChange('recurrence')}
                label="Recurrence"
                error={!!errors.recurrence}
              >
                <MenuItem value="daily">Daily</MenuItem>
                <MenuItem value="weekly">Weekly</MenuItem>
                <MenuItem value="monthly">Monthly</MenuItem>
              </Select>
              {errors.recurrence && (
                <Typography variant="caption" color="error" sx={{ mt: 0.5, ml: 1.5 }}>
                  {errors.recurrence}
                </Typography>
              )}
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={3}>
            <LocalizationProvider dateAdapter={AdapterDateFns}>
              <TimePicker
                label="Time"
                value={formData.startTime}
                onChange={handleChange('startTime')}
                slotProps={{
                  textField: {
                    fullWidth: true,
                    error: !!errors.startTime,
                    helperText: errors.startTime
                  }
                }}
              />
            </LocalizationProvider>
          </Grid>
        </Grid>

        {Object.keys(errors).length > 0 && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {errors.general || 'Please fix the errors above before submitting.'}
          </Alert>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} variant="contained">
          Update Schedule
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default EditScheduleDialog;