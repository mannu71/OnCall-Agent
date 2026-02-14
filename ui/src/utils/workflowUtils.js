/**
 * Workflow Utilities
 */

/**
 * Clean up orphaned edges in workflow (edges that reference non-existent nodes)
 */
export const cleanOrphanedEdges = (workflow) => {
    if (!workflow || !workflow.edges || !workflow.nodes) return workflow;

    const nodeIds = new Set(workflow.nodes.map(n => n.id));

    const cleanedEdges = workflow.edges.filter(edge => {
        const sourceExists = nodeIds.has(edge.source);
        const targetExists = nodeIds.has(edge.target);
        return sourceExists && targetExists;
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
 * Finds the scheduler node in a workflow
 */
export const getSchedulerNode = (workflow) => {
    if (!workflow?.nodes) return null;
    return workflow.nodes.find(n => n.type === 'scheduler');
};

/**
 * Ensures a workflow has a valid structure before saving
 */
export const prepareWorkflowForSave = (workflow) => {
    let cleaned = cleanOrphanedEdges(workflow);

    // Ensure timestamps are set
    const now = new Date().toISOString();
    cleaned.updatedAt = now;
    if (!cleaned.createdAt) cleaned.createdAt = now;

    return cleaned;
};

/**
 * Applies schedule data to a workflow by creating or updating a scheduler node
 * and syncing the top-level fields.
 */
export const applyScheduleToWorkflow = (workflow, schedule) => {
    const nodes = [...(workflow.nodes || [])];
    const edges = [...(workflow.edges || [])];

    let schedulerNode = nodes.find(n => n.type === 'scheduler');

    if (!schedulerNode) {
        schedulerNode = {
            id: `scheduler-${Date.now()}`,
            type: 'scheduler',
            position: { x: 100, y: 100 },
            data: {}
        };
        nodes.push(schedulerNode);

        if (schedule.targetNode) {
            edges.push({
                id: `${schedulerNode.id}-${schedule.targetNode}`,
                source: schedulerNode.id,
                target: schedule.targetNode,
                animated: true
            });
        }
    }

    // Update node data
    schedulerNode.data = {
        ...schedulerNode.data,
        label: schedule.title || schedulerNode.data.label || 'Schedule',
        startTime: schedule.startTime || schedulerNode.data.startTime,
        recurrence: schedule.recurrence || schedulerNode.data.recurrence,
        cronExpression: schedule.schedule || schedulerNode.data.cronExpression,
        enabled: schedule.enabled ?? schedulerNode.data.enabled ?? true
    };

    // Update workflow with new structure and synced fields
    const updatedWorkflow = {
        ...workflow,
        nodes,
        edges,
        schedule: schedule.schedule || workflow.schedule,
        enabled: schedule.enabled ?? workflow.enabled,
        description: schedule.description || workflow.description,
        startTime: schedule.startTime || (schedulerNode.data.startTime || workflow.startTime),
        recurrence: schedule.recurrence || (schedulerNode.data.recurrence || workflow.recurrence)
    };

    return prepareWorkflowForSave(updatedWorkflow);
};
