import React, { useState, useCallback, memo } from 'react';
import { Button } from '@/components/ui/button';
import { Plus } from 'lucide-react';
import { useScheduler } from '../context/SchedulerContext';
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
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Schedule Management</h1>
          <p className="text-muted-foreground mt-1">Automate and monitor your on-call routines</p>
        </div>

        <Button onClick={() => setAddDialogOpen(true)}>
          <Plus className="w-4 h-4 mr-2" />
          Add Schedule
        </Button>
      </div>

      {/* Schedule List Component */}
      <ScheduleList />

      {/* Add Schedule Dialog */}
      <AddScheduleDialog
        open={addDialogOpen}
        onClose={() => setAddDialogOpen(false)}
        onAdd={handleAddSchedule}
      />
    </div>
  );
});

export default SchedulerContent;