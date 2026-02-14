import React, { useState, useCallback, memo } from 'react';
import { Box, Typography, Button, Container } from '@mui/material';
import { Add as AddIcon } from '@mui/icons-material';
import { SchedulerProvider, useScheduler } from '../context/SchedulerContext';
import ScheduleList from '../components/scheduler/ScheduleList';
import AddScheduleDialog from '../components/scheduler/AddScheduleDialog';

const SchedulerContent = memo(() => {
  const { addSchedule } = useScheduler();
  const [addDialogOpen, setAddDialogOpen] = useState(false);

  const handleAddSchedule = useCallback(async (scheduleData) => {
    // Add schedule to context (which handles the API call)
    await addSchedule(scheduleData);
    setAddDialogOpen(false);
  }, [addSchedule]);

  return (
    <Box sx={{ p: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 4 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 700 }}>Schedule Management</Typography>
          <Typography color="text.secondary">Automate and monitor your on-call routines</Typography>
        </Box>

        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setAddDialogOpen(true)}
        >
          Add Schedule
        </Button>
      </Box>

      {/* Schedule List Component */}
      <ScheduleList />

      {/* Add Schedule Dialog */}
      <AddScheduleDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        onAdd={handleAddSchedule}
      />
    </Box>
  );
});

export default function Scheduler() {
  return (
    <SchedulerProvider>
      <SchedulerContent />
    </SchedulerProvider>
  );
}