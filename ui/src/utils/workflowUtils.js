/**
 * Workflow Utilities
 */

// ============================================
// Date Utilities
// ============================================

/**
 * Parse date string to Date object, handling multiple formats
 * Handles malformed formats like "2026-02-17T12:48:21.351271+00:00Z"
 */
export const parseDate = (dateStr) => {
    if (!dateStr) return null;

    // Handle the malformed format: "2026-02-17T12:48:21.351271+00:00Z"
    // This has both +00:00 and Z which is invalid
    let normalized = dateStr;
    if (normalized.includes('+00:00Z')) {
        normalized = normalized.replace('+00:00Z', 'Z');
    }
    if (normalized.includes('+00:00')) {
        normalized = normalized.replace('+00:00', 'Z');
    }

    // Try parsing
    let date = new Date(normalized);
    if (!isNaN(date.getTime())) {
        return date;
    }

    // Try ISO format with Z suffix
    if (!normalized.endsWith('Z') && !normalized.includes('+')) {
        date = new Date(normalized + 'Z');
        if (!isNaN(date.getTime())) {
            return date;
        }
    }

    // Try replacing space with T for ISO format
    if (normalized.includes(' ')) {
        date = new Date(normalized.replace(' ', 'T'));
        if (!isNaN(date.getTime())) {
            return date;
        }
    }

    return null;
};

/**
 * Format date for display
 */
export const formatDate = (dateStr) => {
    const date = parseDate(dateStr);
    if (!date) return 'N/A';
    return date.toLocaleString();
};

/**
 * Format result data for display
 */
export const formatResult = (data) => {
    if (!data) return 'No data';
    try {
        const parsed = Array.isArray(data) ? data : [data];
        if (parsed.length === 0) return 'Empty result';

        if (parsed.length === 1 && typeof parsed[0] === 'object') {
            const entries = Object.entries(parsed[0]);
            if (entries.length === 1) return String(entries[0][1]);
            return entries.map(([k, v]) => `${k}: ${v}`).join(', ');
        }

        if (parsed.length > 1) {
            const keys = Object.keys(parsed[0]);
            return keys.length === 1
                ? `${parsed.length} rows (first: ${parsed[0][keys[0]]})`
                : `${parsed.length} rows`;
        }
        return JSON.stringify(data);
    } catch {
        return String(data);
    }
};

// ============================================
// Workflow Structure Utilities
// ============================================

/**
 * Clean up orphaned edges in workflow (edges that reference non-existent nodes)
 */
export const cleanOrphanedEdges = (workflow) => {
    if (!workflow?.edges || !workflow?.nodes) return workflow;

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
