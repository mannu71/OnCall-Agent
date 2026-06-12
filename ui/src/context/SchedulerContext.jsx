import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import PropTypes from 'prop-types';
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

const formatTime = (timeString) => {
  if (!timeString) return '-';
  try {
    const [hours, minutes] = timeString.split(':');
    const date = new Date(2024, 0, 15); // Fixed date to avoid DST issue
    date.setHours(Number.parseInt(hours, 10), Number.parseInt(minutes, 10), 0, 0);
    return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return timeString;
  }
};

function schedulesMetadataEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.name !== y.name ||
      x.enabled !== y.enabled ||
      x.schedule !== y.schedule ||
      x.updatedAt !== y.updatedAt ||
      x.startTime !== y.startTime ||
      x.recurrence !== y.recurrence ||
      x.indexingStatus !== y.indexingStatus ||
      x.type !== y.type
    ) {
      return false;
    }
  }
  return true;
}

const fromApi = (wf) => {
  let startTime;
  let recurrence;
  const schedulerNode = getSchedulerNode(wf);

  if (schedulerNode?.data) {
    startTime = schedulerNode.data.startTime || cronToLocalTime(schedulerNode.data.cronExpression);
    recurrence = schedulerNode.data.recurrence;
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
    indexingStatus: wf.indexing_status ?? null,
    startTime: startTime,
    recurrence: recurrence || wf.recurrence || 'daily',
    nodes: wf.nodes || [],
    edges: wf.edges || [],
    tasks: wf.tasks || [],
    type: wf.type,
    createdAt: wf.createdAt || wf.created_at || new Date().toISOString(),
    updatedAt: wf.updatedAt || wf.updated_at || new Date().toISOString()
  };
};

export const SchedulerProvider = ({ children }) => {
  const [schedules, setSchedules] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadSchedules = useCallback(async (silent = false) => {
    try {
      if (!silent) setIsLoading(true);
      setError(null);
      const apiWorkflows = await agentApiClient.listWorkflows();
      const normalized = Array.isArray(apiWorkflows) ? apiWorkflows.map(fromApi) : [];
      setSchedules(prev => {
        if (silent && schedulesMetadataEqual(prev, normalized)) return prev;
        return normalized;
      });
    } catch (err) {
      console.error('Failed to load schedules:', err);
      if (!silent) setError('Failed to connect to agent-api');
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // Initial Load
    loadSchedules(false);

    // Background polling every 30 seconds (increased from 15s to reduce load)
    const interval = setInterval(() => {
      if (!document.hidden) loadSchedules(true);
    }, 30000);

    const handleVisibilityChange = () => {
      if (!document.hidden) loadSchedules(true);
    };

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
      await loadSchedules(true);
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
      await loadSchedules(true);
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
      await loadSchedules(true);
    } catch (err) {
      setError('Failed to delete schedule');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [loadSchedules]);

  const triggerWorkflow = useCallback(async (name) => {
    return await agentApiClient.executeWorkflow(name, true);
  }, []);

  const getSchedule = useCallback((idOrName) => {
    return schedules.find(s => s.name === idOrName || s.id === idOrName);
  }, [schedules]);

  const getFreshSchedule = useCallback(async (name) => {
    const raw = await agentApiClient.getWorkflow(name);
    return fromApi(raw);
  }, []);

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
  }), [
    schedules,
    isLoading,
    error,
    addSchedule,
    updateSchedule,
    deleteSchedule,
    getSchedule,
    getFreshSchedule,
    triggerWorkflow,
    loadSchedules
  ]);

  return (
    <SchedulerContext.Provider value={value}>
      {children}
    </SchedulerContext.Provider>
  );
};

SchedulerProvider.propTypes = {
  children: PropTypes.node.isRequired,
};