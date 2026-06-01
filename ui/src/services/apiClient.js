/**
 * API Client for communicating with the FastAPI backend.
 * This service provides a unified interface for the UI to interact with the backend,
 * whether running in Electron (packaged) or as a standalone web app.
 */

const DEFAULT_API_URL = 'http://localhost:8000/api/v1';

/**
 * Get the API base URL based on environment
 */
function getApiBaseUrl() {
    // In packaged Electron app, the API runs on localhost
    // In development, it also runs on localhost
    // Can be overridden via environment variable
    return import.meta.env.VITE_API_URL || DEFAULT_API_URL;
}

/**
 * Make an HTTP request to the API
 */
async function apiRequest(endpoint, options = {}) {
    const url = `${getApiBaseUrl()}${endpoint}`;

    const defaultHeaders = {
        'Content-Type': 'application/json',
    };

    const config = {
        ...options,
        headers: {
            ...defaultHeaders,
            ...options.headers,
        },
    };

    const response = await fetch(url, config);

    if (!response.ok) {
        const errorText = await response.text();
        let errorMessage = `HTTP ${response.status}`;
        try {
            const errorJson = JSON.parse(errorText);
            errorMessage = errorJson.detail || errorJson.message || errorMessage;
        } catch {
            errorMessage = errorText || errorMessage;
        }
        throw new Error(errorMessage);
    }

    // Handle 204 No Content
    if (response.status === 204) {
        return null;
    }

    return response.json();
}

// ============================================
// Health & Status APIs
// ============================================

export async function checkHealth() {
    return apiRequest('/health');
}

export async function getStatus() {
    return apiRequest('/status');
}

// ============================================
// Application Settings APIs
// ============================================

export async function getAppSettings() {
    return apiRequest('/settings');
}

export async function updateGeneralSettings(generalSettings) {
    return apiRequest('/settings/general', {
        method: 'PUT',
        body: JSON.stringify(generalSettings),
    });
}

// ============================================
// Workflow APIs
// ============================================

export async function listWorkflows() {
    return apiRequest('/workflows');
}

export async function getWorkflow(workflowName) {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}`);
}

export async function createWorkflow(workflowData) {
    return apiRequest('/workflows', {
        method: 'POST',
        body: JSON.stringify(workflowData),
    });
}

export async function updateWorkflow(workflowName, workflowData) {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}`, {
        method: 'PUT',
        body: JSON.stringify(workflowData),
    });
}

export async function deleteWorkflow(workflowName, deleteScripts = true) {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}?delete_scripts=${deleteScripts}`, {
        method: 'DELETE',
    });
}

export async function executeWorkflow(workflowName, input = null) {
    const params = input ? `?input=${encodeURIComponent(input)}` : '';
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}/execute${params}`, {
        method: 'POST',
    });
}

export async function executeWorkflowStream(workflowName, input = null, onProgress) {
    const params = input ? `?input=${encodeURIComponent(input)}` : '';
    const url = `${getApiBaseUrl()}/workflows/${encodeURIComponent(workflowName)}/stream${params}`;

    const response = await fetch(url, { method: 'POST' });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let result = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n').filter(line => line.trim());

        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const data = line.slice(6);
                try {
                    const parsed = JSON.parse(data);
                    if (onProgress) {
                        onProgress(parsed);
                    }
                    if (parsed.status === 'completed' || parsed.status === 'error') {
                        result = parsed;
                    }
                } catch {
                    // Ignore parse errors for incomplete JSON
                }
            }
        }
    }

    return result;
}

// ============================================
// Execution History APIs
// ============================================

export async function listExecutions(workflowName = null, limit = 50) {
    const params = new URLSearchParams();
    if (workflowName) params.append('workflow_name', workflowName);
    params.append('limit', limit.toString());
    return apiRequest(`/executions?${params.toString()}`);
}

export async function getActiveWorkflows() {
    return apiRequest('/executions/active');
}

export async function clearActiveExecutions() {
    return apiRequest('/executions/active', { method: 'DELETE' });
}

export async function cancelWorkflowByName(workflowName) {
    return apiRequest(`/executions/active/${encodeURIComponent(workflowName)}`, { method: 'DELETE' });
}

export async function getExecution(executionId) {
    return apiRequest(`/executions/${encodeURIComponent(executionId)}`);
}

export async function deleteExecution(executionId) {
    return apiRequest(`/executions/${encodeURIComponent(executionId)}`, { method: 'DELETE' });
}

export async function deleteAllExecutions() {
    return apiRequest('/executions', { method: 'DELETE' });
}

// ============================================
// MCP Config APIs
// ============================================

export async function getMCPConfig() {
    return apiRequest('/mcp-config');
}

export async function saveMCPConfig(config) {
    return apiRequest('/mcp-config', {
        method: 'POST',
        body: JSON.stringify(config),
    });
}

export async function testMCPServer(serverName, serverConfig) {
    return apiRequest(`/mcp-config/test/${encodeURIComponent(serverName)}`, {
        method: 'POST',
        body: JSON.stringify(serverConfig),
    });
}

// ============================================
// LLM Config APIs
// ============================================

export async function getLLMConfig() {
    return apiRequest('/llm-config');
}

export async function discoverBedrockModels(creds) {
    return apiRequest('/llm-config/discover', {
        method: 'POST',
        body: JSON.stringify(creds || {}),
    });
}

export async function addDiscoveredModels(models, region) {
    return apiRequest('/llm-config/discover/add', {
        method: 'POST',
        body: JSON.stringify({ models, region }),
    });
}

export async function bulkDeleteLLMs(names) {
    return apiRequest('/llm-config/bulk-delete', {
        method: 'POST',
        body: JSON.stringify({ names }),
    });
}

export async function createLLMConfig(name, config) {
    return apiRequest('/llm-config', {
        method: 'POST',
        body: JSON.stringify({ name, ...config }),
    });
}

export async function updateLLMConfig(name, config) {
    return apiRequest(`/llm-config/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: JSON.stringify(config),
    });
}

export async function deleteLLMConfig(name) {
    return apiRequest(`/llm-config/${encodeURIComponent(name)}`, {
        method: 'DELETE',
    });
}

export async function setLLMApiKey(llmName, apiKey) {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key`, {
        method: 'POST',
        body: JSON.stringify({ api_key: apiKey }),
    });
}

export async function getLLMApiKeyMasked(llmName) {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key/masked`);
}

export async function hasLLMApiKey(llmName) {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key/exists`);
}

export async function deleteLLMApiKey(llmName) {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key`, {
        method: 'DELETE',
    });
}

export async function testLLMConnection(llmName, llmConfig) {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/test`, {
        method: 'POST',
        body: JSON.stringify(llmConfig),
    });
}

// ============================================
// SQL File APIs
// ============================================

export async function getSqlFiles(workflowName) {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}/sql-files`);
}

export async function getSqlFile(workflowName, fileName) {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}/sql-files/${encodeURIComponent(fileName)}`);
}

export async function saveSqlFile(workflowName, fileName, content) {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}/sql-files/${encodeURIComponent(fileName)}`, {
        method: 'POST',
        body: JSON.stringify({ content }),
    });
}

// ============================================
// Certificate APIs
// ============================================

export async function getCertificates() {
    return apiRequest('/certificates');
}

export async function uploadCertificate(formData) {
    const url = `${getApiBaseUrl()}/certificates`;
    const response = await fetch(url, {
        method: 'POST',
        body: formData, // Don't set Content-Type, let browser set it with boundary
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `HTTP ${response.status}`);
    }

    return response.json();
}

export async function deleteCertificate(filename) {
    return apiRequest(`/certificates/${encodeURIComponent(filename)}`, { method: 'DELETE' });
}

// ============================================
// Code Analyzer APIs
// ============================================

/**
 * List repositories discovered under REPOS_BASE_PATH.
 * Returns { base_path, base_exists, repos: [{name, path, is_git,
 *   detected_languages, suggested_language, file_count_sample}] }.
 * When the base directory is missing, ``base_exists`` is false and
 * ``repos`` is an empty list — never throws on that case.
 */
export async function listCodeAnalyzerRepos() {
    return apiRequest('/code-analyzer/repos');
}

/**
 * Fetch metadata for a single repository (jailed under REPOS_BASE_PATH).
 * 404 if not found, 400 if the name fails the path-jail.
 */
export async function getCodeAnalyzerRepo(repoName) {
    return apiRequest(`/code-analyzer/repos/${encodeURIComponent(repoName)}`);
}

// ============================================
// Utility exports
// ============================================

export const apiClient = {
    // Health
    checkHealth,
    getStatus,

    // Workflows
    listWorkflows,
    getWorkflow,
    createWorkflow,
    updateWorkflow,
    deleteWorkflow,
    executeWorkflow,
    executeWorkflowStream,

    // Executions
    listExecutions,
    getActiveWorkflows,
    clearActiveExecutions,
    cancelWorkflowByName,
    getExecution,
    deleteExecution,
    deleteAllExecutions,

    // MCP Config
    getMCPConfig,
    saveMCPConfig,
    testMCPServer,

    // LLM Config
    getLLMConfig,
    discoverBedrockModels,
    addDiscoveredModels,
    bulkDeleteLLMs,
    createLLMConfig,
    updateLLMConfig,
    deleteLLMConfig,
    setLLMApiKey,
    getLLMApiKeyMasked,
    hasLLMApiKey,
    deleteLLMApiKey,
    testLLMConnection,

    // SQL Files
    getSqlFiles,
    getSqlFile,
    saveSqlFile,

    // Certificates
    getCertificates,
    uploadCertificate,
    deleteCertificate,

    // Code Analyzer
    listCodeAnalyzerRepos,
    getCodeAnalyzerRepo,

    // Utility
    getApiBaseUrl,
};

export default apiClient;
