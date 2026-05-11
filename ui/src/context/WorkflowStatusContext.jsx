import React, { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import PropTypes from 'prop-types';
import { agentApiClient } from '../services/agentApiClient';

const WorkflowStatusContext = createContext();

export const useWorkflowStatus = () => {
  const context = useContext(WorkflowStatusContext);
  if (!context) {
    throw new Error('useWorkflowStatus must be used within a WorkflowStatusProvider');
  }
  return context;
};

export const WorkflowStatusProvider = ({ children }) => {
  const [runningWorkflows, setRunningWorkflows] = useState([]);
  const [pendingWorkflows, setPendingWorkflows] = useState([]);
  const [lastCheck, setLastCheck] = useState(0);
  const activeStreamsRef = useRef(new Map());

  const checkRunningWorkflows = useCallback(async () => {
    try {
      const active = await agentApiClient.listActiveWorkflows();
      setRunningWorkflows(active || []);
      setLastCheck(Date.now());
    } catch (error) {
      console.error('Error checking running workflows:', error);
    }
  }, []);

  useEffect(() => {
    checkRunningWorkflows();

    const interval = setInterval(checkRunningWorkflows, 10000);

    const handleFocus = () => checkRunningWorkflows();
    window.addEventListener('focus', handleFocus);

    return () => {
      clearInterval(interval);
      window.removeEventListener('focus', handleFocus);
    };
  }, [checkRunningWorkflows]);

  const isWorkflowRunning = useCallback((workflowName) => {
    return runningWorkflows.includes(workflowName) || pendingWorkflows.includes(workflowName);
  }, [runningWorkflows, pendingWorkflows]);

  const markWorkflowPending = useCallback((workflowName) => {
    setPendingWorkflows(prev => [...new Set([...prev, workflowName])]);
  }, []);

  const clearWorkflowPending = useCallback((workflowName) => {
    setPendingWorkflows(prev => prev.filter(w => w !== workflowName));
  }, []);

  const subscribeToWorkflow = useCallback((workflowName) => {
    if (activeStreamsRef.current.has(workflowName)) {
      return activeStreamsRef.current.get(workflowName);
    }

    const eventSource = agentApiClient.streamWorkflowExecution(workflowName);
    activeStreamsRef.current.set(workflowName, eventSource);

    eventSource.addEventListener('workflow_completed', () => {
      activeStreamsRef.current.delete(workflowName);
      eventSource.close();
      checkRunningWorkflows();
      clearWorkflowPending(workflowName);
    });

    eventSource.addEventListener('workflow_failed', () => {
      activeStreamsRef.current.delete(workflowName);
      eventSource.close();
      checkRunningWorkflows();
      clearWorkflowPending(workflowName);
    });

    eventSource.onerror = () => {
      activeStreamsRef.current.delete(workflowName);
      eventSource.close();
      checkRunningWorkflows();
    };

    return eventSource;
  }, [checkRunningWorkflows, clearWorkflowPending]);

  const unsubscribeFromWorkflow = useCallback((workflowName) => {
    const es = activeStreamsRef.current.get(workflowName);
    if (es) {
      es.close();
      activeStreamsRef.current.delete(workflowName);
    }
  }, []);

  useEffect(() => {
    if (runningWorkflows.length > 0) {
      setPendingWorkflows(prev => prev.filter(w => !runningWorkflows.includes(w)));
    }
  }, [runningWorkflows]);

  const allRunningWorkflows = useMemo(() =>
    [...new Set([...runningWorkflows, ...pendingWorkflows])],
    [runningWorkflows, pendingWorkflows]
  );

  const value = useMemo(() => ({
    runningWorkflows: allRunningWorkflows,
    isWorkflowRunning,
    markWorkflowPending,
    clearWorkflowPending,
    subscribeToWorkflow,
    unsubscribeFromWorkflow,
    refreshNow: checkRunningWorkflows,
    count: allRunningWorkflows.length,
    lastCheck
  }), [allRunningWorkflows, isWorkflowRunning, markWorkflowPending, clearWorkflowPending, subscribeToWorkflow, unsubscribeFromWorkflow, checkRunningWorkflows, lastCheck]);

  return (
    <WorkflowStatusContext.Provider value={value}>
      {children}
    </WorkflowStatusContext.Provider>
  );
};

WorkflowStatusProvider.propTypes = {
  children: PropTypes.node.isRequired,
};

export default WorkflowStatusContext;
