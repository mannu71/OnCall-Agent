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
     * @param {string} workflowName - Name of the workflow
     * @param {boolean} deleteScripts - Also delete SQL scripts (default: true)
     */
    async deleteWorkflow(workflowName, deleteScripts = true) {
        await client.delete(`/api/v1/workflows/${encodeURIComponent(workflowName)}`, {
            params: { delete_scripts: deleteScripts }
        });
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
     * Stream execution events by execution ID (SSE)
     */
    streamExecution(executionId) {
        return new EventSource(`${AGENT_API_URL}/api/v1/executions/${encodeURIComponent(executionId)}/stream`);
    },

    /**
     * Approve or reject a HITL pause request
     * @param {string} executionId - The execution ID waiting for approval
     * @param {string} requestId - The HITL request ID from the hitl_pause event
     * @param {boolean} approved - true to approve, false to reject
     * @param {string} [reason] - Optional rejection reason
     */
    async approveHITL(executionId, requestId, approved = true, reason = '') {
        const response = await client.post(`/api/v1/executions/${encodeURIComponent(executionId)}/approve`, {
            request_id: requestId,
            approved,
            reason,
        });
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

    // ==================== MCP Configuration ====================

    /**
     * Get full MCP configuration
     */
    async getMCPConfig() {
        const response = await client.get('/api/v1/mcp-config');
        return response.data;
    },

    /**
     * Get all MCP servers
     */
    async getMCPServers() {
        const response = await client.get('/api/v1/mcp-config/servers');
        return response.data;
    },

    /**
     * Get a specific MCP server
     */
    async getMCPServer(serverName) {
        const response = await client.get(`/api/v1/mcp-config/servers/${encodeURIComponent(serverName)}`);
        return response.data;
    },

    /**
     * Create a new MCP server
     */
    async createMCPServer(serverData) {
        const response = await client.post('/api/v1/mcp-config/servers', serverData);
        return response.data;
    },

    /**
     * Update an MCP server
     */
    async updateMCPServer(serverName, updates) {
        const response = await client.put(`/api/v1/mcp-config/servers/${encodeURIComponent(serverName)}`, updates);
        return response.data;
    },

    /**
     * Delete an MCP server
     */
    async deleteMCPServer(serverName) {
        await client.delete(`/api/v1/mcp-config/servers/${encodeURIComponent(serverName)}`);
    },

    /**
     * Get MCP input values
     */
    async getMCPInputValues() {
        const response = await client.get('/api/v1/mcp-config/input-values');
        return response.data;
    },

    /**
     * Set MCP input values
     */
    async setMCPInputValues(values) {
        const response = await client.put('/api/v1/mcp-config/input-values', { values });
        return response.data;
    },

    /**
     * Update a single MCP input value
     */
    async updateMCPInputValue(inputId, value) {
        const response = await client.patch(`/api/v1/mcp-config/input-values/${encodeURIComponent(inputId)}`, { value });
        return response.data;
    },

    /**
     * Save full MCP configuration (for migration/import)
     */
    async saveMCPConfig(config) {
        const response = await client.post('/api/v1/mcp-config/save', config);
        return response.data;
    },

    // ==================== Certificate Management ====================

    /**
     * List all uploaded certificates
     */
    async listCertificates() {
        const response = await client.get('/api/v1/certificates');
        return response.data;
    },

    /**
     * Upload a certificate file
     */
    async uploadCertificate(file, name = null) {
        const formData = new FormData();
        formData.append('file', file);
        if (name) {
            formData.append('name', name);
        }
        const response = await client.post('/api/v1/certificates', formData, {
            headers: {
                'Content-Type': 'multipart/form-data',
            },
        });
        return response.data;
    },

    /**
     * Delete a certificate
     */
    async deleteCertificate(filename) {
        await client.delete(`/api/v1/certificates/${encodeURIComponent(filename)}`);
    },

    /**
     * Check if a certificate exists
     */
    async checkCertificateExists(filename) {
        const response = await client.get(`/api/v1/certificates/${encodeURIComponent(filename)}/exists`);
        return response.data;
    },

    // ==================== CloudWatch Log Watch ====================

    /**
     * Test AWS CloudWatch connection
     */
    async testCloudWatchConnection(region, credentials) {
        const response = await client.post('/api/v1/log-watch/test-connection', {
            region,
            credentials: {
                aws_profile: credentials.awsProfile || null
            }
        });
        return response.data;
    },
};

export default agentApiClient;
