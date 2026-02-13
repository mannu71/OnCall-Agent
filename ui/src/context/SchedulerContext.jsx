import React, { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { agentApiClient } from '../services/agentApiClient';

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
  const [error, setError] = useState(null);
  const initialLoadDone = useRef(false);

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

  // Transform from agent-api workflow format to UI schedule shape
  const fromApi = (wf) => {
    // Extract workflow info from first task if it's a workflow task
    const workflowTask = wf.tasks?.find(t => t.type === 'workflow');

    return {
      id: wf.name, // Use name as ID
      title: wf.name,
      name: wf.name,
      description: wf.description,
      workflow: workflowTask?.workflow_name || 'daily',
      schedule: wf.schedule,
      enabled: wf.enabled ?? true,
      startTime: parseCronTime(wf.schedule),
      tasks: wf.tasks || [],
      createdAt: wf.createdAt || new Date().toISOString(),
      updatedAt: wf.updatedAt || new Date().toISOString()
    };
  };

  // Transform internal schedule back to agent-api workflow format
  const toApi = (sch) => {
    // Determine cron schedule
    const cronSchedule = sch.schedule || (() => {
      if (sch.startTime) {
        const [hour, minute] = sch.startTime.split(':');
        const localDate = new Date();
        localDate.setHours(parseInt(hour) || 0, parseInt(minute) || 0, 0, 0);
        const utcH = localDate.getUTCHours().toString();
        const utcM = localDate.getUTCMinutes().toString();
        if ((sch.recurrence === 'weekly' || sch.workflow === 'weekly') && sch.date) {
          const dow = new Date(sch.date).getUTCDay(); // 0-6
          return `${utcM} ${utcH} * * ${dow}`;
        }
        return `${utcM} ${utcH} * * *`;
      }
      return '*/5 * * * *'; // Fallback
    })();

    return {
      name: sch.title || sch.name,
      description: sch.description || `Scheduled execution of ${sch.workflow}`,
      schedule: cronSchedule,
      enabled: sch.enabled ?? true,
      tasks: sch.tasks || [{
        name: `execute-${sch.workflow}`,
        type: 'workflow',
        workflow_name: sch.workflow || 'daily',
        timeout: 3600
      }]
    };
  };

  // Load schedules from API
  const loadSchedules = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const apiWorkflows = await agentApiClient.listWorkflows();
      const normalized = apiWorkflows.map(fromApi);
      setSchedules(normalized);
      initialLoadDone.current = true;
    } catch (err) {
      console.error('Error loading schedules from API:', err);
      setError('Failed to connect to agent-api');
      // Falling back to empty or cached
      setSchedules([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSchedules();
  }, [loadSchedules]);

  const addSchedule = useCallback(async (schedule) => {
    try {
      setIsLoading(true);
      const apiData = toApi(schedule);
      await agentApiClient.createWorkflow(apiData);
      await loadSchedules(); // Reload to get fresh data
    } catch (err) {
      console.error('Error adding schedule:', err);
      setError('Failed to create schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const updateSchedule = useCallback(async (id, updatedSchedule) => {
    try {
      setIsLoading(true);
      const apiData = toApi({ ...updatedSchedule, name: id });
      await agentApiClient.updateWorkflow(id, apiData);
      await loadSchedules();
    } catch (err) {
      console.error('Error updating schedule:', err);
      setError('Failed to update schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const deleteSchedule = useCallback(async (id) => {
    try {
      setIsLoading(true);
      await agentApiClient.deleteWorkflow(id);
      await loadSchedules();
    } catch (err) {
      console.error('Error deleting schedule:', err);
      setError('Failed to delete schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const triggerWorkflow = useCallback(async (id) => {
    try {
      return await agentApiClient.executeWorkflow(id);
    } catch (err) {
      console.error('Error triggering workflow:', err);
      throw err;
    }
  }, []);

  const getSchedule = useCallback((id) => {
    return schedules.find(schedule => schedule.id === id);
  }, [schedules]);

  const value = useMemo(() => ({
    schedules,
    isLoading,
    error,
    addSchedule,
    updateSchedule,
    deleteSchedule,
    getSchedule,
    triggerWorkflow,
    loadSchedules
  }), [schedules, isLoading, error, addSchedule, updateSchedule, deleteSchedule, getSchedule, triggerWorkflow, loadSchedules]);

  return (
    <SchedulerContext.Provider value={value}>
      {children}
    </SchedulerContext.Provider>
  );
};