// Preload script (CommonJS) to avoid ESM loading issues
const { contextBridge } = require('electron');

// API base URL - FastAPI server runs on localhost
const API_BASE_URL = 'http://127.0.0.1:8000/api/v1';

/**
 * Make an HTTP request to the FastAPI backend
 */
async function apiRequest(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`;

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
// Expose API to renderer
// ============================================

contextBridge.exposeInMainWorld('electronAPI', {
  // ============================================
  // Health & Status
  // ============================================

  checkHealth: async () => apiRequest('/health'),
  getStatus: async () => apiRequest('/status'),

  // ============================================
  // Schedules (derived from workflows with schedule)
  // ============================================

  loadSchedules: async () => {
    const workflows = await apiRequest('/workflows');
    return workflows
      .filter(w => w.schedule)
      .map(w => ({
        id: w.name,
        name: w.name,
        cronExpression: w.schedule,
        enabled: w.enabled !== false,
        workflowName: w.name,
      }));
  },

  saveSchedules: async (schedules) => {
    // Update each workflow with schedule info
    for (const schedule of schedules) {
      await apiRequest(`/workflows/${encodeURIComponent(schedule.workflowName || schedule.name)}`, {
        method: 'PUT',
        body: JSON.stringify({
          schedule: schedule.cronExpression,
          enabled: schedule.enabled,
        }),
      });
    }
    return { success: true };
  },

  getSchedulesInProgress: async () => {
    const active = await apiRequest('/executions/active');
    return { count: active.length, schedules: active };
  },

  // ============================================
  // Workflows
  // ============================================

  loadWorkflows: async () => apiRequest('/workflows'),

  saveWorkflows: async (workflows) => {
    // Save each workflow individually
    const results = [];
    for (const workflow of workflows) {
      try {
        // Try update first
        const result = await apiRequest(`/workflows/${encodeURIComponent(workflow.name)}`, {
          method: 'PUT',
          body: JSON.stringify(workflow),
        });
        results.push(result);
      } catch (err) {
        // If update fails (404), try create
        try {
          const result = await apiRequest('/workflows', {
            method: 'POST',
            body: JSON.stringify(workflow),
          });
          results.push(result);
        } catch (createErr) {
          console.error(`Failed to save workflow ${workflow.name}:`, createErr);
        }
      }
    }
    return { success: true, count: results.length };
  },

  triggerWorkflow: async (workflowName) => {
    return apiRequest(`/workflows/${encodeURIComponent(workflowName)}/execute`, {
      method: 'POST',
    });
  },

  // ============================================
  // Agent
  // ============================================

  runAgent: async (workflowName, userQuery) => {
    const result = await apiRequest(`/workflows/${encodeURIComponent(workflowName)}/execute?input=${encodeURIComponent(userQuery)}`, {
      method: 'POST',
    });
    return { success: true, result };
  },

  // Subscribe to agent progress events (placeholder - would need SSE implementation)
  onAgentProgress: (callback) => {
    // TODO: Implement SSE-based progress events from FastAPI
    // For now, return a no-op unsubscribe function
    console.log('Agent progress subscription requested');
    return () => { };
  },

  // ============================================
  // MCP Config
  // ============================================

  loadMCPConfig: async () => apiRequest('/mcp-config'),

  saveMCPConfig: async (config) => {
    return apiRequest('/mcp-config/save', {
      method: 'POST',
      body: JSON.stringify(config),
    });
  },

  testMCPServer: async (serverName, serverConfig) => {
    return apiRequest(`/mcp-config/test/${encodeURIComponent(serverName)}`, {
      method: 'POST',
      body: JSON.stringify(serverConfig),
    });
  },

  // ============================================
  // LLM Config
  // ============================================

  loadLLMConfig: async () => apiRequest('/llm-config'),

  discoverBedrockModels: async (creds) => {
    return apiRequest('/llm-config/discover', {
      method: 'POST',
      body: JSON.stringify(creds || {}),
    });
  },

  addDiscoveredModels: async (models, region) => {
    return apiRequest('/llm-config/discover/add', {
      method: 'POST',
      body: JSON.stringify({ models, region }),
    });
  },

  bulkDeleteLLMs: async (names) => {
    return apiRequest('/llm-config/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ names }),
    });
  },

  saveLLMConfig: async (config) => {
    // Save each LLM config individually
    const results = [];
    for (const [name, llmConfig] of Object.entries(config.llms || {})) {
      try {
        const result = await apiRequest(`/llm-config/${encodeURIComponent(name)}`, {
          method: 'PUT',
          body: JSON.stringify(llmConfig),
        });
        results.push(result);
      } catch (err) {
        // If update fails, try creating
        try {
          const result = await apiRequest('/llm-config', {
            method: 'POST',
            body: JSON.stringify({ name, ...llmConfig }),
          });
          results.push(result);
        } catch (createErr) {
          console.error(`Failed to save LLM config ${name}:`, createErr);
        }
      }
    }
    return { success: true, results };
  },

  setApiKey: async (llmName, apiKey) => {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key`, {
      method: 'POST',
      body: JSON.stringify({ api_key: apiKey }),
    });
  },

  getApiKeyMasked: async (llmName) => {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key/masked`);
  },

  hasApiKey: async (llmName) => {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key/exists`);
  },

  deleteApiKey: async (llmName) => {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/api-key`, {
      method: 'DELETE',
    });
  },

  testLLM: async (llmName, llmConfig) => {
    return apiRequest(`/llm-config/${encodeURIComponent(llmName)}/test`, {
      method: 'POST',
      body: JSON.stringify(llmConfig),
    });
  },

  // ============================================
  // SQL Files (managed through workflow nodes)
  // ============================================

  saveSqlFile: async (filename, content) => {
    console.log('SQL files are now managed through workflow orchestrator nodes');
    return { success: true, message: 'SQL files managed through workflow nodes' };
  },

  loadSqlFile: async (relativePath) => {
    console.log('SQL files are now managed through workflow orchestrator nodes');
    return { success: false, message: 'SQL files managed through workflow nodes' };
  },

  // ============================================
  // Workflow Runs / Execution History
  // ============================================

  loadWorkflowRuns: async () => {
    const executions = await apiRequest('/executions?limit=100');
    // Transform to match expected format
    return executions.map(exec => ({
      id: exec.execution_id || exec.id,
      workflowName: exec.workflow_name,
      status: exec.status,
      startTime: exec.start_time,
      endTime: exec.end_time,
      results: exec.results,
      output: exec.output,
    }));
  },

  loadLatestWorkflowResult: async () => {
    const executions = await apiRequest('/executions?limit=1');
    return executions.length > 0 ? executions[0] : null;
  },

  loadWorkflowResult: async (filename) => {
    const executionId = filename.replace('.json', '');
    return apiRequest(`/executions/${encodeURIComponent(executionId)}`);
  },

  clearWorkflowOutputs: async () => {
    return apiRequest('/executions', { method: 'DELETE' });
  },

  // ============================================
  // Debug
  // ============================================

  getPathsInfo: async () => {
    return {
      apiUrl: API_BASE_URL,
      electron: true,
    };
  },

  // Flag to indicate Electron environment
  isElectron: true,
});
