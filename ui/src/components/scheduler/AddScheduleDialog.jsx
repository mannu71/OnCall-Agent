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

const AddScheduleDialog = ({ open, onClose, onAdd }) => {
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
        const wf = await window.electronAPI.loadWorkflows();
        // Filter to only show workflow type (not agent type)
        const workflowTypeOnly = wf.filter(w => w.type !== 'agent');
        setWorkflows(workflowTypeOnly);
        // Auto-select first workflow when dialog opens
        if (workflowTypeOnly.length > 0) {
          setFormData(prev => ({ ...prev, workflow: workflowTypeOnly[0].name }));
        }
      } catch (error) {
        console.error('Error loading workflows:', error);
      }
    };
    
    if (open) {
      loadWorkflows();
    }
  }, [open]);

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

    if (!formData.workflow) {
      newErrors.workflow = 'Workflow is required';
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

    try {
      const timeObj = new Date(formData.startTime);
      const now = new Date();
      // Get local time for cron schedule
      const hour = timeObj.getHours().toString();
      const minute = timeObj.getMinutes().toString();
      const dayOfMonth = now.getDate().toString();
      
      // Generate cron schedule based on recurrence (in local time)
      let cronSchedule;
      if (formData.recurrence === 'weekly') {
        const dow = now.getDay(); // 0-6 (0=Sunday)
        cronSchedule = `${minute} ${hour} * * ${dow}`;
      } else if (formData.recurrence === 'monthly') {
        cronSchedule = `${minute} ${hour} ${dayOfMonth} * *`;
      } else {
        // Daily
        cronSchedule = `${minute} ${hour} * * *`;
      }

      const scheduleData = {
        title: formData.title,
        workflow: formData.workflow,
        recurrence: formData.recurrence,
        startTime: timeObj.toTimeString().split(' ')[0].substring(0, 5),
        schedule: cronSchedule
      };

      onAdd(scheduleData);
      handleClose();
    } catch (error) {
      console.error('Error creating schedule:', error);
      setErrors({ general: 'Failed to create schedule. Please try again.' });
    }
  };

  const handleClose = () => {
    setFormData({
      title: '',
      startTime: new Date(),
      workflow: '',
      recurrence: 'daily'
    });
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
      aria-labelledby="add-schedule-dialog-title"
      aria-describedby="add-schedule-dialog-description"
    >
      <DialogTitle id="add-schedule-dialog-title">
        <Typography variant="h6" component="h2">
          Add New Schedule
        </Typography>
      </DialogTitle>
      
      <DialogContent dividers id="add-schedule-dialog-description">
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
              <InputLabel id="workflow-label">
                Workflow
              </InputLabel>
              <Select
                labelId="workflow-label"
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
              {workflows.length === 0 && !errors.workflow && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, ml: 1.5 }}>
                  Create a workflow first to add schedules
                </Typography>
              )}
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={7}>
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

          <Grid item xs={12} sm={2}>
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
          Add Schedule
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AddScheduleDialog;