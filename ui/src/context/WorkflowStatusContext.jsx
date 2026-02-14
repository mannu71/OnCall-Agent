import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
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

    // Check every 10 seconds (less aggressive than 2s)
    const interval = setInterval(checkRunningWorkflows, 10000);

    // If window returns to focus, check immediately
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

  // Synchronize pending and running
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
    count: allRunningWorkflows.length,
    lastCheck
  }), [allRunningWorkflows, isWorkflowRunning, markWorkflowPending, clearWorkflowPending, lastCheck]);

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
