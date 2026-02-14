import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { agentApiClient } from '../services/agentApiClient';
import { cronToLocalTime } from '../utils/cronUtils';
import { getSchedulerNode, applyScheduleToWorkflow } from '../utils/workflowUtils';

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

  /**
   * Helper: Format HH:MM string to user-friendly local time
   */
  const formatTime = useCallback((timeString) => {
    if (!timeString) return '-';
    try {
      const [hours, minutes] = timeString.split(':');
      const date = new Date();
      date.setHours(parseInt(hours), parseInt(minutes));
      return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    } catch {
      return timeString;
    }
  }, []);

  /**
   * Transform from API workflow model to UI schedule model
   */
  const fromApi = useCallback((wf) => {
    let startTime = undefined;
    const schedulerNode = getSchedulerNode(wf);

    if (schedulerNode?.data) {
      startTime = schedulerNode.data.startTime || cronToLocalTime(schedulerNode.data.cronExpression);
    } else if (wf.schedule) {
      startTime = cronToLocalTime(wf.schedule);
    }

    return {
      id: wf.name || wf.id,
      title: wf.name,
      name: wf.name,
      description: wf.description || '',
      workflow: wf.name,
      schedule: wf.schedule,
      enabled: wf.enabled ?? true,
      startTime: startTime,
      nodes: wf.nodes || [],
      edges: wf.edges || [],
      tasks: wf.tasks || [],
      createdAt: wf.createdAt || wf.created_at || new Date().toISOString(),
      updatedAt: wf.updatedAt || wf.updated_at || new Date().toISOString()
    };
  }, []);

  const loadSchedules = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const apiWorkflows = await agentApiClient.listWorkflows();
      const normalized = Array.isArray(apiWorkflows) ? apiWorkflows.map(fromApi) : [];
      setSchedules(normalized);
    } catch (err) {
      console.error('Failed to load schedules:', err);
      setError('Failed to connect to agent-api');
      setSchedules([]);
    } finally {
      setIsLoading(false);
    }
  }, [fromApi]);

  useEffect(() => {
    loadSchedules();
    const interval = setInterval(loadSchedules, 5000);
    const handleVisibilityChange = () => { if (!document.hidden) loadSchedules(); };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [loadSchedules]);

  const addSchedule = useCallback(async (schedule) => {
    try {
      setIsLoading(true);
      const existingWorkflow = await agentApiClient.getWorkflow(schedule.workflow);
      const payload = applyScheduleToWorkflow(existingWorkflow, schedule);

      await agentApiClient.updateWorkflow(schedule.workflow, payload);
      await loadSchedules();
    } catch (err) {
      setError('Failed to create schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const updateSchedule = useCallback(async (name, updatedSchedule) => {
    try {
      setIsLoading(true);
      const existingWorkflow = await agentApiClient.getWorkflow(name);
      const payload = applyScheduleToWorkflow(existingWorkflow, updatedSchedule);

      await agentApiClient.updateWorkflow(name, payload);
      await loadSchedules();
    } catch (err) {
      setError('Failed to update schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const deleteSchedule = useCallback(async (name) => {
    try {
      setIsLoading(true);
      await agentApiClient.deleteWorkflow(name);
      await loadSchedules();
    } catch (err) {
      setError('Failed to delete schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const triggerWorkflow = useCallback(async (name) => {
    return await agentApiClient.executeWorkflow(name);
  }, []);

  const getSchedule = useCallback((name) => {
    return schedules.find(s => s.name === name || s.id === name);
  }, [schedules]);

  const getFreshSchedule = useCallback(async (name) => {
    const raw = await agentApiClient.getWorkflow(name);
    return fromApi(raw);
  }, [fromApi]);

  const value = useMemo(() => ({
    schedules,
    isLoading,
    error,
    addSchedule,
    updateSchedule,
    deleteSchedule,
    getSchedule,
    getFreshSchedule,
    triggerWorkflow,
    loadSchedules,
    formatTime
  }), [schedules, isLoading, error, addSchedule, updateSchedule, deleteSchedule, getSchedule, getFreshSchedule, triggerWorkflow, loadSchedules, formatTime]);

  return (
    <SchedulerContext.Provider value={value}>
      {children}
    </SchedulerContext.Provider>
  );
};