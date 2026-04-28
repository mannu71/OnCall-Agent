import React, { useState, useEffect, memo, useCallback, useMemo } from 'react';
import PropTypes from 'prop-types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
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
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Edit Schedule</DialogTitle>
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
              {filteredWorkflows.length === 0 ? (
                <option value="" disabled>No workflows available</option>
              ) : (
                filteredWorkflows.map((wf) => (
                  <option key={wf.id} value={wf.name}>{wf.name}</option>
                ))
              )}
            </select>
            {errors.workflow && (
              <p className="text-sm text-red-500 mt-1">{errors.workflow}</p>
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
                Execution Time
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

          <div>
            <label htmlFor="description" className="text-sm font-medium mb-2 block">
              Description (Optional)
            </label>
            <textarea
              id="description"
              value={formData.description}
              onChange={handleChange('description')}
              rows={2}
              className="flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </div>
        </div>

        {Object.keys(errors).length > 0 && !errors.title && !errors.workflow && !errors.startTime && (
          <div className="bg-red-50 border border-red-200 text-red-800 px-4 py-3 rounded">
            Please fix the errors above before submitting.
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>Cancel</Button>
          <Button onClick={handleSubmit}>Save Changes</Button>
        </DialogFooter>
      </DialogContent>
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