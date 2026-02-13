import React, { createContext, useContext, useState, useEffect } from 'react';

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
  const [isLoading, setIsLoading] = useState(true);

  // Poll for running workflows globally
  useEffect(() => {
    const checkRunningWorkflows = async () => {
      try {
        if (window.electronAPI?.getSchedulesInProgress) {
          const inProgress = await window.electronAPI.getSchedulesInProgress();
          setRunningWorkflows(inProgress.schedules || []);
        }
      } catch (error) {
        console.error('Error checking running workflows:', error);
      } finally {
        setIsLoading(false);
      }
    };
    
    checkRunningWorkflows();
    const interval = setInterval(checkRunningWorkflows, 2000); // Check every 2 seconds
    
    return () => clearInterval(interval);
  }, []);

  const isWorkflowRunning = (workflowName) => {
    return runningWorkflows.includes(workflowName) || pendingWorkflows.includes(workflowName);
  };

  // Mark a workflow as pending immediately (before API call completes)
  const markWorkflowPending = (workflowName) => {
    setPendingWorkflows(prev => [...prev, workflowName]);
  };

  // Clear a workflow from pending state (e.g., when API call fails)
  const clearWorkflowPending = (workflowName) => {
    setPendingWorkflows(prev => prev.filter(w => w !== workflowName));
  };

  // Remove from pending when it appears in running (synced from main process)
  useEffect(() => {
    setPendingWorkflows(prev => prev.filter(w => !runningWorkflows.includes(w)));
  }, [runningWorkflows]);

  // Combined list for display
  const allRunningWorkflows = [...new Set([...runningWorkflows, ...pendingWorkflows])];

  const value = {
    runningWorkflows: allRunningWorkflows,
    isWorkflowRunning,
    markWorkflowPending,
    clearWorkflowPending,
    isLoading,
    count: allRunningWorkflows.length
  };

  return (
    <WorkflowStatusContext.Provider value={value}>
      {children}
    </WorkflowStatusContext.Provider>
  );
};

export default WorkflowStatusContext;
