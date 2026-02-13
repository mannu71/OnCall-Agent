import React, { useState, useCallback, memo } from 'react';
import { Button, Fab } from '@mui/material';
import { Add as AddIcon } from '@mui/icons-material';
import { SchedulerProvider, useScheduler } from '../context/SchedulerContext';
import ScheduleList from '../components/scheduler/ScheduleList';
import AddScheduleDialog from '../components/scheduler/AddScheduleDialog';
import { addSchedulerNodeToWorkflow } from '../utils/workflowScheduleSync';

const SchedulerContent = memo(() => {
  const { addSchedule } = useScheduler();
  const [addDialogOpen, setAddDialogOpen] = useState(false);

  const handleAddSchedule = useCallback(async (scheduleData) => {
    // Add schedule to Schedule Management
    addSchedule(scheduleData);

    // Also add scheduler node to the workflow file using common utility
    try {
      const { default: agentApiClient } = await import('../services/agentApiClient.js');
      const wf = await agentApiClient.listWorkflows();
      const workflow = wf.find(w => w.name === scheduleData.workflow);

      if (workflow) {
        // Use common utility to add scheduler node
        const updatedWorkflow = addSchedulerNodeToWorkflow(workflow, scheduleData);

        // Save workflow
        await agentApiClient.updateWorkflow(workflow.name, updatedWorkflow);
      }
    } catch (error) {
      console.error('Error adding scheduler to workflow:', error);
    }
  }, [addSchedule]);

  const handleOpenDialog = useCallback(() => {
    setAddDialogOpen(true);
  }, []);

  const handleCloseDialog = useCallback(() => {
    setAddDialogOpen(false);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="p-8">
        <div className="max-w-7xl mx-auto">
          {/* Header */}
          <div className="mb-6 flex justify-between items-center">
            <div>
              <h1 className="text-3xl font-semibold text-gray-900 mb-2">
                Scheduler Management
              </h1>
              <p className="text-gray-600">
                Manage and organize your on-call schedules
              </p>
            </div>

            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={handleOpenDialog}
            >
              Add Schedule
            </Button>
          </div>

          {/* Schedule List */}
          <ScheduleList />

          {/* Floating Action Button for mobile */}
          <Fab
            color="primary"
            aria-label="add"
            onClick={handleOpenDialog}
            sx={{
              position: 'fixed',
              bottom: 16,
              right: 16,
              display: { xs: 'flex', md: 'none' }
            }}
          >
            <AddIcon />
          </Fab>

          {/* Add Schedule Dialog */}
          <AddScheduleDialog
            open={addDialogOpen}
            onClose={handleCloseDialog}
            onAdd={handleAddSchedule}
          />
        </div>
      </div>
    </div>
  );
});

export default function Schedule() {
  return (
    <SchedulerProvider>
      <SchedulerContent />
    </SchedulerProvider>
  );
}