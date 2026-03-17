import React, { useState, useEffect, useCallback, memo, useMemo } from 'react';
import PropTypes from 'prop-types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
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
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Add New Schedule</DialogTitle>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <div>
            <label htmlFor="title" className="text-sm font-medium mb-2 block">
              Title <span className="text-red-500">*</span>
            </label>
            <Input
              id="title"
              value={formData.title}
              onChange={handleChange('title')}
              className={errors.title ? 'border-red-500' : ''}
            />
            {errors.title && (
              <p className="text-sm text-red-500 mt-1">{errors.title}</p>
            )}
          </div>

          <div>
            <label htmlFor="workflow" className="text-sm font-medium mb-2 block">
              Workflow <span className="text-red-500">*</span>
            </label>
            <select
              id="workflow"
              value={formData.workflow}
              onChange={handleChange('workflow')}
              className={`flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ${
                errors.workflow ? 'border-red-500' : ''
              }`}
            >
              {workflows.length === 0 ? (
                <option value="" disabled>No workflows available</option>
              ) : (
                workflows.map((wf) => (
                  <option key={wf.id} value={wf.name}>{wf.name}</option>
                ))
              )}
            </select>
            {errors.workflow && (
              <p className="text-sm text-red-500 mt-1">{errors.workflow}</p>
            )}
          </div>

          <div>
            <label htmlFor="targetNode" className="text-sm font-medium mb-2 block">
              Connect To Node <span className="text-red-500">*</span>
            </label>
            <select
              id="targetNode"
              value={formData.targetNode}
              onChange={handleChange('targetNode')}
              disabled={targetNodes.length === 0}
              className={`flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm disabled:opacity-50 ${
                errors.targetNode ? 'border-red-500' : ''
              }`}
            >
              {targetNodes.length === 0 ? (
                <option value="" disabled>No valid target nodes</option>
              ) : (
                targetNodes.map((node) => (
                  <option key={node.id} value={node.id}>{node.displayName}</option>
                ))
              )}
            </select>
            {errors.targetNode && (
              <p className="text-sm text-red-500 mt-1">{errors.targetNode}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="recurrence" className="text-sm font-medium mb-2 block">
                Recurrence
              </label>
              <select
                id="recurrence"
                value={formData.recurrence}
                onChange={handleChange('recurrence')}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
              </select>
            </div>

            <div>
              <label htmlFor="startTime" className="text-sm font-medium mb-2 block">
                Time
              </label>
              <Input
                id="startTime"
                type="time"
                value={`${String(formData.startTime.getHours()).padStart(2, '0')}:${String(formData.startTime.getMinutes()).padStart(2, '0')}`}
                onChange={(e) => {
                  const [hours, minutes] = e.target.value.split(':');
                  const newTime = new Date(formData.startTime);
                  newTime.setHours(Number.parseInt(hours, 10), Number.parseInt(minutes, 10), 0, 0);
                  handleChange('startTime')(newTime);
                }}
                className={errors.startTime ? 'border-red-500' : ''}
              />
              {errors.startTime && (
                <p className="text-sm text-red-500 mt-1">{errors.startTime}</p>
              )}
            </div>
          </div>
        </div>

        {errors.general && (
          <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded">
            {errors.general}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>Cancel</Button>
          <Button onClick={handleSubmit}>Add Schedule</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});

AddScheduleDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onAdd: PropTypes.func.isRequired,
};

export default AddScheduleDialog;