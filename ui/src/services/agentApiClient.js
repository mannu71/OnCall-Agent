import axios from 'axios';

/**
 * Agent-API Client
 * 
 * Provides methods to communicate directly with the agent-api service.
 */

const AGENT_API_URL = import.meta.env.VITE_AGENT_API_URL || 'http://localhost:8000';

const client = axios.create({
    baseURL: AGENT_API_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

export const agentApiClient = {
    /**
     * List all workflows (both visual and script workflows)
     */
    async listWorkflows() {
        const response = await client.get('/api/v1/workflows');
        return response.data;
    },

    /**
     * Get a specific workflow by name
     */
    async getWorkflow(workflowName) {
        const response = await client.get(`/api/v1/workflows/${workflowName}`);
        return response.data;
    },

    /**
     * Create a new workflow
     */
    async createWorkflow(workflow) {
        // All workflows from UI are type: "workflow"
        const workflowData = {
            ...workflow,
            type: workflow.type || 'workflow'
        };
        const response = await client.post('/api/v1/workflows', workflowData);
        return response.data;
    },

    /**
     * Update an existing workflow
     */
    async updateWorkflow(workflowName, updates) {
        const response = await client.put(`/api/v1/workflows/${workflowName}`, updates);
        return response.data;
    },

    /**
     * Delete a workflow
     */
    async deleteWorkflow(workflowName) {
        await client.delete(`/api/v1/workflows/${workflowName}`);
    },

    /**
     * Execute a workflow manually
     */
    async executeWorkflow(workflowName, background = false) {
        const response = await client.post(`/api/v1/workflows/${workflowName}/execute`, null, {
            params: { background },
        });
        return response.data;
    },

    /**
     * Stream workflow execution events (SSE)
     */
    streamWorkflowExecution(workflowName) {
        return new EventSource(`${AGENT_API_URL}/api/v1/workflows/${encodeURIComponent(workflowName)}/stream`);
    },

    /**
     * Get execution history for a workflow
     */
    async getWorkflowHistory(workflowName, limit = 10) {
        const response = await client.get(`/api/v1/workflows/${workflowName}/history`, {
            params: { limit },
        });
        return response.data;
    },

    /**
     * Get details of a specific execution
     */
    async getExecutionDetails(workflowName, executionId) {
        const response = await client.get(`/api/v1/workflows/${workflowName}/history/${executionId}`);
        return response.data;
    },

    /**
     * Stream workflow execution events (SSE)
     */
    streamWorkflowExecution(workflowName) {
        return new EventSource(`${AGENT_API_URL}/api/v1/workflows/${workflowName}/stream`);
    },

    /**
     * Check agent-api health
     */
    async getHealth() {
        const response = await client.get('/api/v1/health');
        return response.data;
    },

    /**
     * Get system status
     */
    async getStatus() {
        const response = await client.get('/api/v1/status');
        return response.data;
    },

    /**
     * Clear in-memory data (active executions, event queues)
     */
    async clearData(clearJobs = false) {
        const response = await client.post('/api/v1/clear', null, {
            params: { clear_jobs: clearJobs },
        });
        return response.data;
    },
};

export default agentApiClient;
