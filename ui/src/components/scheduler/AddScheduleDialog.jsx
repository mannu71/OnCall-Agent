import React, { useState, useEffect, useCallback, memo, useMemo } from 'react';
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

const AddScheduleDialog = memo(({ open, onClose, onAdd }) => {
  const { schedules } = useScheduler();
  const [formData, setFormData] = useState({
    title: '',
    startTime: new Date(2024, 0, 15, 9, 0),
    workflow: '',
    targetNode: '',
    recurrence: 'daily'
  });

  const [errors, setErrors] = useState({});

  // Filter workflows from context - only non-agent workflows
  const workflows = useMemo(() =>
    schedules.filter(s => s.type !== 'agent'),
    [schedules]
  );

  // Get available nodes for the selected workflow
  const targetNodes = useMemo(() => {
    const selectedWorkflow = workflows.find(w => w.name === formData.workflow);
    if (!selectedWorkflow?.nodes) return [];

    return selectedWorkflow.nodes
      .filter(node => node.type === 'orchestrator' || node.type === 'agent')
      .map(node => ({
        id: node.id,
        type: node.type,
        displayName: `${node.data?.label || node.type} (${node.type})`
      }));
  }, [workflows, formData.workflow]);

  // Initial workflow/node selection
  useEffect(() => {
    if (open && workflows.length > 0 && !formData.workflow) {
      const firstWorkflow = workflows[0];
      setFormData(prev => ({
        ...prev,
        workflow: firstWorkflow.name
      }));
    }
  }, [open, workflows, formData.workflow]);

  // Handle target node selection when workflow changes
  useEffect(() => {
    if (targetNodes.length > 0 && !formData.targetNode) {
      setFormData(prev => ({ ...prev, targetNode: targetNodes[0].id }));
    } else if (targetNodes.length === 0 && formData.targetNode) {
      setFormData(prev => ({ ...prev, targetNode: '' }));
    }
  }, [targetNodes, formData.targetNode]);

  const handleChange = useCallback((field) => (event) => {
    const value = event?.target ? event.target.value : event;
    setFormData(prev => {
      const updates = { [field]: value };
      // Reset targetNode if workflow changes
      if (field === 'workflow') updates.targetNode = '';
      return { ...prev, ...updates };
    });

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
    if (!formData.targetNode) newErrors.targetNode = 'Target node is required';
    if (!formData.startTime || Number.isNaN(formData.startTime.getTime())) {
      newErrors.startTime = 'Valid time is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [formData]);

  const handleSubmit = useCallback(() => {
    if (!validateForm()) return;

    try {
      const localTimeStr = dateToLocalTimeString(formData.startTime);
      const cronSchedule = dateToCron(formData.startTime, formData.recurrence);

      onAdd({
        title: formData.title,
        workflow: formData.workflow,
        targetNode: formData.targetNode,
        recurrence: formData.recurrence,
        startTime: localTimeStr,
        schedule: cronSchedule
      });
      handleClose();
    } catch (error) {
      console.error('Error creating schedule:', error);
      setErrors({ general: 'Failed to create schedule. Please try again.' });
    }
  }, [validateForm, formData, onAdd]);

  const handleClose = useCallback(() => {
    setFormData({
      title: '',
      startTime: new Date(2024, 0, 15, 9, 0),
      workflow: '',
      targetNode: '',
      recurrence: 'daily'
    });
    setErrors({});
    onClose();
  }, [onClose]);

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Add New Schedule</DialogTitle>

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
            />
          </Grid>

          <Grid item xs={12}>
            <FormControl fullWidth error={!!errors.workflow}>
              <InputLabel id="workflow-label">Workflow</InputLabel>
              <Select
                labelId="workflow-label"
                value={formData.workflow}
                onChange={handleChange('workflow')}
                label="Workflow"
              >
                {workflows.length === 0 ? (
                  <MenuItem value="" disabled>No workflows available</MenuItem>
                ) : (
                  workflows.map((wf) => (
                    <MenuItem key={wf.id} value={wf.name}>{wf.name}</MenuItem>
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

          <Grid item xs={12}>
            <FormControl fullWidth error={!!errors.targetNode} disabled={targetNodes.length === 0}>
              <InputLabel id="target-node-label">Connect To Node</InputLabel>
              <Select
                labelId="target-node-label"
                value={formData.targetNode}
                onChange={handleChange('targetNode')}
                label="Connect To Node"
              >
                {targetNodes.length === 0 ? (
                  <MenuItem value="" disabled>No valid target nodes</MenuItem>
                ) : (
                  targetNodes.map((node) => (
                    <MenuItem key={node.id} value={node.id}>{node.displayName}</MenuItem>
                  ))
                )}
              </Select>
              {errors.targetNode && (
                <Typography variant="caption" color="error" sx={{ mt: 0.5, ml: 1.5 }}>
                  {errors.targetNode}
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

        {errors.general && (
          <Alert severity="error" sx={{ mt: 2 }}>{errors.general}</Alert>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={handleClose} color="inherit">Cancel</Button>
        <Button onClick={handleSubmit} variant="contained" disableElevation>
          Add Schedule
        </Button>
      </DialogActions>
    </Dialog>
  );
});

AddScheduleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
};

export default AddScheduleDialog;