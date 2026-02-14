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

// Add response interceptor for global error logging
client.interceptors.response.use(
    response => response,
    error => {
        const message = error.response?.data?.detail || error.message;
        console.error(`[API Error] ${error.config.method.toUpperCase()} ${error.config.url}:`, message);
        return Promise.reject(error);
    }
);

export const agentApiClient = {
    /**
     * List all workflows
     */
    async listWorkflows() {
        const response = await client.get('/api/v1/workflows');
        return response.data;
    },

    /**
     * Get a specific workflow by name
     */
    async getWorkflow(workflowName) {
        const response = await client.get(`/api/v1/workflows/${encodeURIComponent(workflowName)}`);
        return response.data;
    },

    /**
     * Create a new workflow
     */
    async createWorkflow(workflow) {
        const response = await client.post('/api/v1/workflows', {
            ...workflow,
            type: workflow.type || 'workflow'
        });
        return response.data;
    },

    /**
     * Update an existing workflow
     */
    async updateWorkflow(workflowName, updates) {
        const response = await client.put(`/api/v1/workflows/${encodeURIComponent(workflowName)}`, updates);
        return response.data;
    },

    /**
     * Delete a workflow
     */
    async deleteWorkflow(workflowName) {
        await client.delete(`/api/v1/workflows/${encodeURIComponent(workflowName)}`);
    },

    /**
     * Execute a workflow manually
     */
    async executeWorkflow(workflowName, background = false) {
        const response = await client.post(`/api/v1/workflows/${encodeURIComponent(workflowName)}/execute`, null, {
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
    async getWorkflowExecutions(workflowName, limit = 50) {
        const response = await client.get(`/api/v1/workflows/${encodeURIComponent(workflowName)}/executions`, {
            params: { limit },
        });
        return response.data;
    },

    /**
     * Get all execution history
     */
    async listAllExecutions(limit = 100) {
        const response = await client.get('/api/v1/executions', {
            params: { limit },
        });
        return response.data;
    },

    async listActiveWorkflows() {
        const response = await client.get('/api/v1/executions/active');
        return response.data;
    },

    /**
     * Delete all execution history
     */
    async deleteAllExecutions() {
        const response = await client.delete('/api/v1/executions');
        return response.data;
    },

    /**
     * Check agent-api health
     */
    async getHealth() {
        const response = await client.get('/api/v1/health');
        return response.data;
    },

    /**
     * Clear in-memory data
     */
    async clearData(clearJobs = false) {
        const response = await client.post('/api/v1/clear', null, {
            params: { clear_jobs: clearJobs },
        });
        return response.data;
    },
};

export default agentApiClient;
