import React, { createContext, useContext, useCallback, useMemo } from 'react';
import PropTypes from 'prop-types';
import { useQueryClient } from '@tanstack/react-query';
import { agentApiClient } from '../services/agentApiClient';
import { cronToLocalTime } from '../utils/cronUtils';
import { getSchedulerNode, applyScheduleToWorkflow } from '../utils/workflowUtils';
import { useWorkflowsQuery } from '../hooks/queries/useWorkflowsQuery';
import { queryKeys } from '../lib/queryKeys';

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
    const date = new Date(2024, 0, 15);
    date.setHours(Number.parseInt(hours, 10), Number.parseInt(minutes, 10), 0, 0);
    return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return timeString;
  }
};

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
  const queryClient = useQueryClient();
  const { data: apiWorkflows, isLoading, error: queryError, refetch } = useWorkflowsQuery();

  const schedules = useMemo(() => {
    const list = Array.isArray(apiWorkflows) ? apiWorkflows : [];
    return list.map(fromApi);
  }, [apiWorkflows]);

  const error = queryError ? 'Failed to connect to agent-api' : null;

  const invalidateWorkflows = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.workflows });
  }, [queryClient]);

  const loadSchedules = useCallback(async () => {
    await refetch();
  }, [refetch]);

  const addSchedule = useCallback(async (schedule) => {
    try {
      const existingWorkflow = await agentApiClient.getWorkflow(schedule.workflow);
      const payload = applyScheduleToWorkflow(existingWorkflow, schedule);
      await agentApiClient.updateWorkflow(schedule.workflow, payload);
      await invalidateWorkflows();
    } catch (err) {
      throw err;
    }
  }, [invalidateWorkflows]);

  const updateSchedule = useCallback(async (name, updatedSchedule) => {
    try {
      const existingWorkflow = await agentApiClient.getWorkflow(name);
      const payload = applyScheduleToWorkflow(existingWorkflow, updatedSchedule);
      await agentApiClient.updateWorkflow(name, payload);
      await invalidateWorkflows();
    } catch (err) {
      throw err;
    }
  }, [invalidateWorkflows]);

  const deleteSchedule = useCallback(async (name) => {
    try {
      await agentApiClient.deleteWorkflow(name);
      await invalidateWorkflows();
    } catch (err) {
      throw err;
    }
  }, [invalidateWorkflows]);

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
