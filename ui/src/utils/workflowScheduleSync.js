/**
 * Workflow-Schedule Synchronization Utilities
 * Common functions for syncing between workflows and schedules
 */

import { cronToLocalTime } from './cronUtils.js';

/**
 * Clean up orphaned edges in workflow (edges that reference non-existent nodes)
 * @param {Object} workflow - The workflow object
 * @returns {Object} Cleaned workflow
 */
export const cleanOrphanedEdges = (workflow) => {
  if (!workflow || !workflow.edges || !workflow.nodes) return workflow;

  const nodeIds = new Set(workflow.nodes.map(n => n.id));
  
  const cleanedEdges = workflow.edges.filter(edge => {
    const sourceExists = nodeIds.has(edge.source);
    const targetExists = nodeIds.has(edge.target);
    
    if (!sourceExists || !targetExists) {
      return false;
    }
    
    return true;
  });

  if (cleanedEdges.length !== workflow.edges.length) {
    return {
      ...workflow,
      edges: cleanedEdges,
      updatedAt: new Date().toISOString()
    };
  }

  return workflow;
};

/**
 * Extract schedules from workflow scheduler nodes
 * @param {Object} workflow - The workflow object
 * @returns {Array} Array of schedule objects
 */
export const extractSchedulesFromWorkflow = (workflow) => {
  if (!workflow || !workflow.nodes) return [];

  const schedulerNodes = workflow.nodes.filter(node => node.type === 'scheduler');
  
  return schedulerNodes.map(node => {
    const data = node.data || {};
    
    // Find the edge to determine target node
    const edge = workflow.edges?.find(e => e.source === node.id);
    const targetNodeId = edge?.target;

    // Extract time from cron or use stored startTime
    const startTime = data.startTime || cronToLocalTime(data.cronExpression);

    return {
      id: `${workflow.id}-${node.id}`,
      workflowId: workflow.id,
      nodeId: node.id,
      name: data.label || 'Scheduler',
      title: data.label || 'Scheduler',
      workflow: workflow.name,
      schedule: data.cronExpression || '0 9 * * *',
      startTime: startTime, // Add startTime for display
      recurrence: data.recurrence || 'daily',
      enabled: data.enabled !== false,
      targetNode: targetNodeId,
      date: data.createdAt || new Date().toISOString(), // For ScheduleCard
      createdAt: data.createdAt || new Date().toISOString(),
      updatedAt: data.updatedAt || new Date().toISOString()
    };
  });
};

/**
 * Add scheduler node to workflow
 * @param {Object} workflow - The workflow object
 * @param {Object} scheduleData - The schedule data
 * @returns {Object} Updated workflow with new scheduler node and edge
 */
export const addSchedulerNodeToWorkflow = (workflow, scheduleData) => {
  const newSchedulerNode = {
    id: `scheduler-${Date.now()}`,
    type: 'scheduler',
    position: { 
      x: 100, 
      y: 100 + (workflow.nodes?.filter(n => n.type === 'scheduler').length || 0) * 150 
    },
    data: {
      label: scheduleData.title,
      cronExpression: scheduleData.schedule,
      startTime: scheduleData.startTime, // Store startTime for display
      recurrence: scheduleData.recurrence,
      enabled: scheduleData.enabled !== false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    }
  };

  // Create connection edge
  const targetNode = workflow.nodes.find(n => n.id === scheduleData.targetNode);
  const targetHandle = targetNode?.type === 'orchestrator' ? 'orchestrator-input' : 'agent-input';
  
  const newEdge = {
    id: `${newSchedulerNode.id}-to-${scheduleData.targetNode}`,
    source: newSchedulerNode.id,
    sourceHandle: 'scheduler-output',
    target: scheduleData.targetNode,
    targetHandle: targetHandle,
    type: 'default'
  };

  return {
    ...workflow,
    nodes: [...(workflow.nodes || []), newSchedulerNode],
    edges: [...(workflow.edges || []), newEdge],
    updatedAt: new Date().toISOString()
  };
};

/**
 * Update scheduler node in workflow
 * @param {Object} workflow - The workflow object
 * @param {Object} originalSchedule - The original schedule data
 * @param {Object} updatedSchedule - The updated schedule data
 * @returns {Object} Updated workflow with modified scheduler node
 */
export const updateSchedulerNodeInWorkflow = (workflow, originalSchedule, updatedSchedule) => {
  if (!workflow || !workflow.nodes) return workflow;

  // Find the scheduler node by nodeId or by matching label
  let schedulerNode = null;
  
  if (originalSchedule.nodeId) {
    schedulerNode = workflow.nodes.find(n => n.id === originalSchedule.nodeId);
  }
  
  if (!schedulerNode) {
    schedulerNode = workflow.nodes.find(n => 
      n.type === 'scheduler' && 
      n.data?.label === originalSchedule.title
    );
  }

  if (!schedulerNode) {
    return workflow;
  }

  // Update the node data
  const updatedNodes = workflow.nodes.map(n => {
    if (n.id === schedulerNode.id) {
      return {
        ...n,
        data: {
          ...n.data,
          label: updatedSchedule.title || updatedSchedule.name || n.data.label,
          cronExpression: updatedSchedule.schedule || n.data.cronExpression,
          startTime: updatedSchedule.startTime || n.data.startTime, // Preserve startTime
          recurrence: updatedSchedule.recurrence || n.data.recurrence,
          enabled: updatedSchedule.enabled !== undefined ? updatedSchedule.enabled : n.data.enabled,
          updatedAt: new Date().toISOString()
        }
      };
    }
    return n;
  });

  // If targetNode changed, update the edge
  let updatedEdges = workflow.edges || [];
  if (updatedSchedule.targetNode && updatedSchedule.targetNode !== originalSchedule.targetNode) {
    // Remove old edge
    updatedEdges = updatedEdges.filter(e => e.source !== schedulerNode.id);
    
    // Add new edge
    const targetNode = workflow.nodes.find(n => n.id === updatedSchedule.targetNode);
    const targetHandle = targetNode?.type === 'orchestrator' ? 'orchestrator-input' : 'agent-input';
    
    updatedEdges.push({
      id: `${schedulerNode.id}-to-${updatedSchedule.targetNode}`,
      source: schedulerNode.id,
      sourceHandle: 'scheduler-output',
      target: updatedSchedule.targetNode,
      targetHandle: targetHandle,
      type: 'default'
    });
  }

  return {
    ...workflow,
    nodes: updatedNodes,
    edges: updatedEdges,
    updatedAt: new Date().toISOString()
  };
};

/**
 * Remove scheduler node from workflow
 * @param {Object} workflow - The workflow object
 * @param {Object} schedule - The schedule to remove
 * @returns {Object} Updated workflow with scheduler node removed
 */
export const removeSchedulerNodeFromWorkflow = (workflow, schedule) => {
  if (!workflow || !workflow.nodes) return workflow;

  // Find the scheduler node by nodeId or by matching label
  let schedulerNode = null;
  
  if (schedule.nodeId) {
    schedulerNode = workflow.nodes.find(n => n.id === schedule.nodeId);
  }
  
  if (!schedulerNode) {
    schedulerNode = workflow.nodes.find(n => 
      n.type === 'scheduler' && 
      n.data?.label === (schedule.title || schedule.name)
    );
  }

  if (!schedulerNode) {
    return workflow;
  }

  // Remove the node
  const updatedNodes = workflow.nodes.filter(n => n.id !== schedulerNode.id);
  
  // Remove edges connected to this node
  const updatedEdges = (workflow.edges || []).filter(e => 
    e.source !== schedulerNode.id && e.target !== schedulerNode.id
  );

  return {
    ...workflow,
    nodes: updatedNodes,
    edges: updatedEdges,
    updatedAt: new Date().toISOString()
  };
};

/**
 * Sync workflow changes to schedule storage
 * @param {Object} workflow - The workflow object
 * @param {Object} apiClient - The API client object (agentApiClient or electronAPI)
 * @returns {Promise<void>}
 */
export const syncWorkflowToSchedules = async (workflow, apiClient) => {
  try {
    // Extract schedules from workflow
    const workflowSchedules = extractSchedulesFromWorkflow(workflow);
    
    if (workflowSchedules.length === 0) {
      return; // No schedulers to sync
    }

    // Load existing schedules
    let existingSchedules = [];
    if (apiClient && apiClient.loadSchedules) {
      existingSchedules = await apiClient.loadSchedules();
    } else if (window.electronAPI?.loadSchedules) {
      existingSchedules = await window.electronAPI.loadSchedules();
    } else {
      const data = localStorage.getItem('oncall-schedules');
      existingSchedules = data ? JSON.parse(data) : [];
    }

    // Remove old schedules for this workflow (by workflowId or workflow name)
    const otherSchedules = existingSchedules.filter(s => {
      return s.workflowId !== workflow.id && s.workflow !== workflow.name;
    });
    
    // Combine and save
    const allSchedules = [...otherSchedules, ...workflowSchedules];
    
    if (apiClient && apiClient.saveSchedules) {
      await apiClient.saveSchedules(allSchedules);
    } else if (window.electronAPI?.saveSchedules) {
      await window.electronAPI.saveSchedules(allSchedules);
    } else {
      localStorage.setItem('oncall-schedules', JSON.stringify(allSchedules));
    }
  } catch (error) {
    console.error('Error syncing workflow to schedules:', error);
  }
};

/**
 * Update workflow file with schedule changes
 * @param {Object} schedule - The schedule object
 * @param {Function} updateFn - Function to update workflow (add/update/remove)
 * @param {Object} apiClient - The API client object (agentApiClient or electronAPI)
 * @returns {Promise<void>}
 */
export const updateWorkflowFromSchedule = async (schedule, updateFn, apiClient) => {
  try {
    let workflows;
    if (apiClient?.listWorkflows) {
      // Using agentApiClient
      workflows = await apiClient.listWorkflows();
    } else if (apiClient?.loadWorkflows) {
      // Using electronAPI (backward compatibility)
      workflows = await apiClient.loadWorkflows();
    } else if (window.electronAPI?.loadWorkflows) {
      workflows = await window.electronAPI.loadWorkflows();
    } else {
      return;
    }

    const workflow = workflows.find(w => w.name === schedule.workflow || w.id === schedule.workflowId);
    
    if (!workflow) {
      return;
    }

    // Apply the update function
    const updatedWorkflow = updateFn(workflow, schedule);

    // Save workflow
    if (apiClient?.updateWorkflow) {
      // Using agentApiClient
      await apiClient.updateWorkflow(workflow.name, updatedWorkflow);
    } else if (apiClient?.saveWorkflows) {
      // Using electronAPI (backward compatibility)
      const updatedWorkflows = workflows.map(w => w.id === workflow.id ? updatedWorkflow : w);
      await apiClient.saveWorkflows(updatedWorkflows);
    } else if (window.electronAPI?.saveWorkflows) {
      const updatedWorkflows = workflows.map(w => w.id === workflow.id ? updatedWorkflow : w);
      await window.electronAPI.saveWorkflows(updatedWorkflows);
    }
  } catch (error) {
    console.error('Error updating workflow from schedule:', error);
  }
};
