import React, { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { updateSchedulerNodeInWorkflow, removeSchedulerNodeFromWorkflow, updateWorkflowFromSchedule } from '../utils/workflowScheduleSync';

const SchedulerContext = createContext();

export const useScheduler = () => {
  const context = useContext(SchedulerContext);
  if (!context) {
    throw new Error('useScheduler must be used within a SchedulerProvider');
  }
  return context;
};

export const SchedulerProvider = ({ children }) => {
  const [schedules, setSchedules] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const initialLoadDone = useRef(false);
  const saveTimeoutRef = useRef(null);

  const isElectron = Boolean(typeof window !== 'undefined' && window?.electronAPI);

  // Helper: parse simple cron (m h * * *) into HH:MM (converts UTC cron to local time for display)
  const parseCronTime = (cron) => {
    if (!cron || typeof cron !== 'string') return undefined;
    const parts = cron.trim().split(/\s+/);
    if (parts.length < 2) return undefined;
    const [min, hour] = parts;
    if (isNaN(parseInt(hour)) || isNaN(parseInt(min))) return undefined;
    
    // Convert UTC time from cron to local time for display
    const utcDate = new Date();
    utcDate.setUTCHours(parseInt(hour), parseInt(min), 0, 0);
    
    const pad = (n) => n.toString().padStart(2, '0');
    // Return local time
    return `${pad(utcDate.getHours())}:${pad(utcDate.getMinutes())}`;
  };

  // Transform from workflow file object to internal schedule shape
  const fromWorkflow = (wf) => {
    // Use a stable ID based on name hash to prevent regeneration on reload
    const stableId = wf.id || `${wf.name}-${wf.name.split('').reduce((a, b) => ((a << 5) - a) + b.charCodeAt(0), 0)}`;
    return {
      id: stableId,
      workflowId: wf.workflowId, // Preserve workflowId
      nodeId: wf.nodeId, // Preserve nodeId
      title: wf.title || wf.name,
      name: wf.name,
      workflow: wf.workflow || wf.name,
      schedule: wf.schedule,
      recurrence: wf.recurrence, // Preserve recurrence
      enabled: wf.enabled ?? true,
      // Derive display time if cron fits pattern
      startTime: wf.startTime || parseCronTime(wf.schedule),
      targetNode: wf.targetNode, // Preserve targetNode
      // Preserve date from saved data
      date: wf.date,
      createdAt: wf.createdAt || new Date().toISOString(),
      updatedAt: wf.updatedAt || new Date().toISOString()
    };
  };

  // Transform internal schedule back to workflow file object
  const toWorkflow = (sch) => {
    return {
      id: sch.id,
      workflowId: sch.workflowId, // Preserve workflowId
      nodeId: sch.nodeId, // Preserve nodeId
      title: sch.title || sch.name || 'untitled',
      name: sch.title || sch.name || 'untitled',
      workflow: sch.workflow || 'daily',
      date: sch.date,
      startTime: sch.startTime, // Preserve startTime
      recurrence: sch.recurrence, // Preserve recurrence
      targetNode: sch.targetNode, // Preserve targetNode
      createdAt: sch.createdAt,
      updatedAt: sch.updatedAt,
      schedule: sch.schedule || (() => {
        // If we have startTime and type, synthesize cron in UTC
        if (sch.startTime) {
          const [hour, minute] = sch.startTime.split(':');
          // startTime is in local time, convert to UTC for cron
          const localDate = new Date();
          localDate.setHours(parseInt(hour) || 0, parseInt(minute) || 0, 0, 0);
          const utcH = localDate.getUTCHours().toString();
          const utcM = localDate.getUTCMinutes().toString();
          if ((sch.workflow === 'weekly') && sch.date) {
            // Use day-of-week from date (in UTC)
            const dow = new Date(sch.date).getUTCDay(); // 0-6
            return `${utcM} ${utcH} * * ${dow}`;
          }
          // Daily / default
          return `${utcM} ${utcH} * * *`;
        }
        return '*/5 * * * *'; // Fallback every 5 minutes
      })(),
      enabled: sch.enabled ?? true
    };
  };

  // Load schedules on mount
  useEffect(() => {
    const loadSchedules = async () => {
      try {
        setIsLoading(true);
        let savedSchedules = [];
        
        if (isElectron) {
          // Use Electron file operations
          try {
            savedSchedules = await window.electronAPI.loadSchedules();
          } catch (error) {
            console.error('Electron load failed:', error);
            savedSchedules = [];
          }
        } else {
          // Use localStorage for web
          const data = localStorage.getItem('oncall-schedules');
          savedSchedules = data ? JSON.parse(data) : [];
        }
        
        const validSchedules = Array.isArray(savedSchedules) ? savedSchedules : [];
        // Detect workflow format: objects with name & schedule keys
        const normalized = validSchedules.map(obj => {
          if (obj && 'name' in obj && 'schedule' in obj && !('title' in obj)) {
            return fromWorkflow(obj);
          }
            // Already internal shape
          return obj;
        });
        setSchedules(normalized);
        initialLoadDone.current = true;
      } catch (error) {
        console.error('Error loading schedules:', error);
        setSchedules([]);
        initialLoadDone.current = true;
      } finally {
        setIsLoading(false);
      }
    };

    loadSchedules();
    
    // Cleanup on unmount
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [isElectron]);

  // Save schedules with debouncing to prevent excessive writes
  useEffect(() => {
    if (isLoading || !initialLoadDone.current) {
      return; // Don't save during initial load
    }
    
    // Clear any pending save
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
    }
    
    // Debounce saves by 500ms
    saveTimeoutRef.current = setTimeout(async () => {
      try {
        const workflowPayload = schedules.map(toWorkflow);
        if (isElectron) {
          // Use Electron file operations only; pass workflow format
          const result = await window.electronAPI.saveSchedules(workflowPayload);
          if (!result.success) {
            console.error('Failed to save schedules to file:', result.error);
          }
        } else {
          // Use localStorage for web in workflow format for consistency
          localStorage.setItem('oncall-schedules', JSON.stringify(workflowPayload));
        }
      } catch (error) {
        console.error('Error saving schedules:', error);
      }
    }, 500);
  }, [schedules, isLoading, isElectron]);

  const addSchedule = useCallback((schedule) => {
    const newSchedule = {
      id: `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      ...schedule,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };
    setSchedules(prev => [...prev, newSchedule]);
  }, []);

  const updateSchedule = useCallback(async (id, updatedSchedule) => {
    // Find the original schedule
    const originalSchedule = schedules.find(s => s.id === id);
    
    // Update schedules state
    setSchedules(prev => prev.map(schedule => 
      schedule.id === id 
        ? { ...schedule, ...updatedSchedule, updatedAt: new Date().toISOString() }
        : schedule
    ));

    // Also update scheduler node in workflow file using common utility
    if (originalSchedule) {
      await updateWorkflowFromSchedule(
        { ...originalSchedule, ...updatedSchedule },
        (workflow, schedule) => updateSchedulerNodeInWorkflow(workflow, originalSchedule, schedule),
        window.electronAPI
      );
    }
  }, [schedules]);

  const deleteSchedule = useCallback(async (id) => {
    // Find the schedule being deleted
    const scheduleToDelete = schedules.find(s => s.id === id);
    
    // Remove from schedules state
    setSchedules(prev => prev.filter(schedule => schedule.id !== id));

    // Also remove scheduler node from workflow file using common utility
    if (scheduleToDelete) {
      await updateWorkflowFromSchedule(
        scheduleToDelete,
        (workflow, schedule) => removeSchedulerNodeFromWorkflow(workflow, schedule),
        window.electronAPI
      );
    }
  }, [schedules]);

  const getSchedule = useCallback((id) => {
    return schedules.find(schedule => schedule.id === id);
  }, [schedules]);

  const getSchedulesByDate = useCallback((date) => {
    const targetDate = new Date(date).toDateString();
    return schedules.filter(schedule => 
      new Date(schedule.date).toDateString() === targetDate
    );
  }, [schedules]);

  const value = useMemo(() => ({
    schedules,
    isLoading,
    addSchedule,
    updateSchedule,
    deleteSchedule,
    getSchedule,
    getSchedulesByDate
  }), [schedules, isLoading, addSchedule, updateSchedule, deleteSchedule, getSchedule, getSchedulesByDate]);

  return (
    <SchedulerContext.Provider value={value}>
      {children}
    </SchedulerContext.Provider>
  );
};