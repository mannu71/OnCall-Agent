import React, { useState, useMemo, memo } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Play, Search, MoreVertical, Edit2, Trash2, Loader2 } from 'lucide-react';
import { useScheduler } from '../../context/SchedulerContext';
import { useWorkflowStatus } from '../../context/WorkflowStatusContext';
import EditScheduleDialog from './EditScheduleDialog';
import agentApiClient from '../../services/agentApiClient.js';

const ScheduleList = memo(() => {
  const {
    schedules,
    deleteSchedule,
    updateSchedule,
    isLoading,
    getFreshSchedule,
    formatTime
  } = useScheduler();
  const { isWorkflowRunning, markWorkflowPending, clearWorkflowPending } = useWorkflowStatus();
  const [searchTerm, setSearchTerm] = useState('');
  const [editingSchedule, setEditingSchedule] = useState(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scheduleToDelete, setScheduleToDelete] = useState(null);

  const filteredSchedules = useMemo(() => {
    const searchLower = searchTerm.toLowerCase();
    return schedules.filter(s => {
      if (s.type === 'agent') return false;
      const name = (s.name || '').toLowerCase();
      const title = (s.title || '').toLowerCase();
      return name.includes(searchLower) || title.includes(searchLower);
    });
  }, [schedules, searchTerm]);



  const handleEditSchedule = async (schedule) => {
    try {
      const fresh = await getFreshSchedule(schedule.name);
      setEditingSchedule(fresh);
    } catch (error) {
      console.error('Error loading schedule:', error);
    }
  };

  const confirmDelete = () => {
    if (scheduleToDelete?.name) {
      deleteSchedule(scheduleToDelete.name);
    }
    setDeleteDialogOpen(false);
    setScheduleToDelete(null);
  };

  const handleRunNow = async (schedule) => {
    const name = schedule.name;
    
    // Prevent duplicate execution if already running/pending
    if (isWorkflowRunning(name)) {
      return;
    }
    
    try {
      markWorkflowPending(name);
      await agentApiClient.executeWorkflow(name, true);
    } catch (e) {
      clearWorkflowPending(name);
      console.error('Error running workflow:', e);
    }
  };

  const getEmptyMessage = () => {
    if (schedules.length === 0) {
      return "No schedules found.";
    }
    return "No schedules match your search.";
  };

  const renderContent = () => {
    if (isLoading) {
      return (
        <div className="flex justify-center py-8">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      );
    }

    if (filteredSchedules.length === 0) {
      return (
        <div className="bg-blue-50 border border-blue-200 text-blue-800 px-4 py-3 rounded">
          {getEmptyMessage()}
        </div>
      );
    }

    return (
      <div className="border rounded-lg bg-white">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50/50">
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Title</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Workflow</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Local Time</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider">Schedule</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider text-center">Run</TableHead>
              <TableHead className="text-slate-500 text-xs font-bold uppercase tracking-wider text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className="text-sm">
            {filteredSchedules.map((schedule) => (
              <TableRow key={schedule.name}>
                <TableCell className="font-semibold text-slate-900">{schedule.title || schedule.name}</TableCell>
                <TableCell className="text-slate-600">
                  <Badge variant="outline">{schedule.name}</Badge>
                </TableCell>
                <TableCell>{schedule.startTime ? formatTime(schedule.startTime) : '-'}</TableCell>
                <TableCell>
                  <span className="font-mono text-sm text-muted-foreground">
                    {schedule.schedule || 'Manual'}
                  </span>
                </TableCell>
                <TableCell className="text-center">
                  {isWorkflowRunning(schedule.name) ? (
                    <Loader2 className="w-4 h-4 animate-spin mx-auto" />
                  ) : (
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => handleRunNow(schedule)}
                    >
                      <Play className="w-4 h-4" />
                    </Button>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button size="icon" variant="ghost">
                        <MoreVertical className="w-4 h-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => handleEditSchedule(schedule)}>
                        <Edit2 className="w-4 h-4 mr-2" />
                        Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => {
                          setScheduleToDelete(schedule);
                          setDeleteDialogOpen(true);
                        }}
                        className="text-red-600"
                      >
                        <Trash2 className="w-4 h-4 mr-2" />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    );
  };


  return (
    <div>
      <div className="mb-4">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search schedules..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {renderContent()}

      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Delete</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete the schedule "{scheduleToDelete?.title || scheduleToDelete?.name}"?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {editingSchedule && (
        <EditScheduleDialog
          open={!!editingSchedule}
          schedule={editingSchedule}
          onClose={() => setEditingSchedule(null)}
          onUpdate={(updated) => { updateSchedule(editingSchedule.name, updated); setEditingSchedule(null); }}
        />
      )}
    </div>
  );
});

export default ScheduleList;
