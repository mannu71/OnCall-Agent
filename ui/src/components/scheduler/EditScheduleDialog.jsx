import React, { useState, useEffect, memo, useCallback, useMemo } from 'react';
import PropTypes from 'prop-types';
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
import { useScheduler } from '../../context/SchedulerContext';

const EditScheduleDialog = memo(({ open, schedule, onClose, onUpdate }) => {
  const { schedules } = useScheduler();
  const [formData, setFormData] = useState({
    title: '',
    startTime: new Date(2024, 0, 15, 9, 0),
    workflow: '',
    recurrence: 'daily',
    enabled: true,
    description: ''
  });

  const [errors, setErrors] = useState({});

  // Filter workflows from schedules context - only non-agent workflows
  const filteredWorkflows = useMemo(() =>
    schedules.filter(s => s.type !== 'agent'),
    [schedules]
  );

  // Sync form data when schedule prop changes
  useEffect(() => {
    if (schedule && open) {
      const timeString = schedule.startTime || '09:00';
      const [hoursStr, minutesStr] = timeString.split(':');
      const hours = Number.parseInt(hoursStr, 10) || 9;
      const minutes = Number.parseInt(minutesStr, 10) || 0;

      const timeObj = new Date(2024, 0, 15);
      timeObj.setHours(hours, minutes, 0, 0);

      setFormData({
        title: schedule.title || schedule.name || '',
        startTime: timeObj,
        workflow: schedule.name || '',
        recurrence: schedule.recurrence || 'daily',
        enabled: schedule.enabled ?? true,
        description: schedule.description || ''
      });
      setErrors({});
    }
  }, [schedule, open]);

  const handleChange = useCallback((field) => (event) => {
    let value;
    if (event?.target) {
      value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    } else {
      value = event;
    }

    setFormData(prev => ({ ...prev, [field]: value }));

    if (errors[field]) {
      setErrors(prev => {
        const newErrors = { ...prev };
        delete newErrors[field];
        return newErrors;
      });
    }
  }, [errors]);

  const validateForm = useCallback(() => {
    const newErrors = {};
    if (!formData.title.trim()) newErrors.title = 'Title is required';
    if (!formData.workflow) newErrors.workflow = 'Workflow is required';
    if (!formData.startTime || Number.isNaN(formData.startTime.getTime())) {
      newErrors.startTime = 'Valid time is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [formData]);

  const handleSubmit = useCallback(() => {
    if (!validateForm()) return;

    const localTimeStr = dateToLocalTimeString(formData.startTime);
    const cronSchedule = dateToCron(formData.startTime, formData.recurrence);

    onUpdate({
      title: formData.title,
      workflow: formData.workflow,
      recurrence: formData.recurrence,
      startTime: localTimeStr,
      schedule: cronSchedule,
      enabled: formData.enabled,
      description: formData.description
    });
    onClose();
  }, [validateForm, formData, onUpdate, onClose]);

  const handleClose = useCallback(() => {
    setErrors({});
    onClose();
  }, [onClose]);

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="edit-schedule-dialog-title"
    >
      <DialogTitle id="edit-schedule-dialog-title">
        Edit Schedule
      </DialogTitle>

      <DialogContent dividers>
        <Grid container spacing={2} sx={{ mt: 0.5 }}>
          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Title"
              value={formData.title}
              onChange={handleChange('title')}
              error={!!errors.title}
              helperText={errors.title}
              required
              variant="outlined"
            />
          </Grid>

          <Grid item xs={12}>
            <FormControl fullWidth error={!!errors.workflow}>
              <InputLabel id="workflow-select-label">Workflow</InputLabel>
              <Select
                labelId="workflow-select-label"
                value={formData.workflow}
                onChange={handleChange('workflow')}
                label="Workflow"
              >
                {filteredWorkflows.length === 0 ? (
                  <MenuItem value="" disabled>No workflows available</MenuItem>
                ) : (
                  filteredWorkflows.map((wf) => (
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
              <InputLabel id="recurrence-label">Recurrence</InputLabel>
              <Select
                labelId="recurrence-label"
                value={formData.recurrence}
                onChange={handleChange('recurrence')}
                label="Recurrence"
              >
                <MenuItem value="daily">Daily</MenuItem>
                <MenuItem value="weekly">Weekly</MenuItem>
                <MenuItem value="monthly">Monthly</MenuItem>
              </Select>
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={6}>
            <LocalizationProvider dateAdapter={AdapterDateFns}>
              <TimePicker
                label="Execution Time"
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

          <Grid item xs={12}>
            <TextField
              fullWidth
              label="Description (Optional)"
              value={formData.description}
              onChange={handleChange('description')}
              multiline
              rows={2}
            />
          </Grid>
        </Grid>

        {Object.keys(errors).length > 0 && !errors.title && !errors.workflow && !errors.startTime && (
          <Alert severity="error" sx={{ mt: 2 }}>
            Please fix the errors above before submitting.
          </Alert>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={handleClose} color="inherit">
          Cancel
        </Button>
        <Button onClick={handleSubmit} variant="contained" disableElevation>
          Save Changes
        </Button>
      </DialogActions>
    </Dialog>
  );
});

EditScheduleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  schedule: PropTypes.shape({
    name: PropTypes.string,
    title: PropTypes.string,
    startTime: PropTypes.string,
    recurrence: PropTypes.string,
    enabled: PropTypes.bool,
    description: PropTypes.string,
  }),
  onClose: PropTypes.func.isRequired,
  onUpdate: PropTypes.func.isRequired,
};

export default EditScheduleDialog;