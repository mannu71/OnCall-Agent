import React, { useState, useEffect, useCallback, memo } from 'react';
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

const AddScheduleDialog = memo(({ open, onClose, onAdd }) => {
  const [formData, setFormData] = useState({
    title: '',
    startTime: new Date(),
    workflow: '',
    targetNode: '', // Added: which node to connect to
    recurrence: 'daily'
  });

  const [workflows, setWorkflows] = useState([]);
  const [targetNodes, setTargetNodes] = useState([]); // Added: available nodes in selected workflow

  const [errors, setErrors] = useState({});

  useEffect(() => {
    const loadWorkflows = async () => {
      try {
        const wf = await agentApiClient.listWorkflows();
        // Filter to only show workflow type (not agent type)
        const workflowTypeOnly = wf.filter(w => w.type !== 'agent');
        setWorkflows(workflowTypeOnly);
        // Auto-select first workflow when dialog opens
        if (workflowTypeOnly.length > 0) {
          const firstWorkflow = workflowTypeOnly[0];
          setFormData(prev => ({ ...prev, workflow: firstWorkflow.name }));
          // Load target nodes for first workflow
          loadTargetNodes(firstWorkflow);
        }
      } catch (error) {
        console.error('Error loading workflows:', error);
        setWorkflows([]);
      }
    };

    if (open) {
      loadWorkflows();
    }
  }, [open]);

  const loadTargetNodes = (workflow) => {
    if (!workflow || !workflow.nodes) {
      setTargetNodes([]);
      return;
    }

    // Find nodes that can accept scheduler connections (orchestrator, agent)
    const validTargets = workflow.nodes.filter(node =>
      node.type === 'orchestrator' || node.type === 'agent'
    ).map(node => ({
      id: node.id,
      type: node.type,
      label: node.data?.label || node.type,
      displayName: `${node.data?.label || node.type} (${node.type})`
    }));

    setTargetNodes(validTargets);

    // Auto-select first target node
    if (validTargets.length > 0) {
      setFormData(prev => ({ ...prev, targetNode: validTargets[0].id }));
    }
  };

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

    if (!formData.targetNode) {
      newErrors.targetNode = 'Target node is required';
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

      // Use utility functions for conversion
      const localTimeStr = dateToLocalTimeString(timeObj);
      const cronSchedule = dateToCron(timeObj, formData.recurrence);

      const scheduleData = {
        title: formData.title,
        workflow: formData.workflow,
        targetNode: formData.targetNode,
        recurrence: formData.recurrence,
        startTime: localTimeStr, // Local time for display
        schedule: cronSchedule // UTC cron for backend
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
      targetNode: '',
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
                onChange={(e) => {
                  const workflowName = e.target.value;
                  handleChange('workflow')(e);
                  // Load target nodes for selected workflow
                  const selectedWorkflow = workflows.find(w => w.name === workflowName);
                  if (selectedWorkflow) {
                    loadTargetNodes(selectedWorkflow);
                  }
                }}
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

          <Grid item xs={12} sm={4}>
            <FormControl fullWidth>
              <InputLabel id="target-node-label">
                Connect To
              </InputLabel>
              <Select
                labelId="target-node-label"
                value={formData.targetNode}
                onChange={handleChange('targetNode')}
                label="Connect To"
                error={!!errors.targetNode}
                disabled={targetNodes.length === 0}
              >
                {targetNodes.length === 0 ? (
                  <MenuItem value="" disabled>
                    No target nodes available
                  </MenuItem>
                ) : (
                  targetNodes.map((node) => (
                    <MenuItem key={node.id} value={node.id}>
                      {node.displayName}
                    </MenuItem>
                  ))
                )}
              </Select>
              {errors.targetNode && (
                <Typography variant="caption" color="error" sx={{ mt: 0.5, ml: 1.5 }}>
                  {errors.targetNode}
                </Typography>
              )}
              {targetNodes.length === 0 && !errors.targetNode && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, ml: 1.5 }}>
                  Selected workflow has no orchestrator or agent
                </Typography>
              )}
            </FormControl>
          </Grid>

          <Grid item xs={12} sm={5}>
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
});

export default AddScheduleDialog;