// Preload script (CommonJS) to avoid ESM loading issues
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  loadSchedules: () => ipcRenderer.invoke('schedules:load'),
  saveSchedules: (schedules) => ipcRenderer.invoke('schedules:save', schedules),
  loadWorkflows: () => ipcRenderer.invoke('workflows:load'),
  saveWorkflows: (workflows) => ipcRenderer.invoke('workflows:save', workflows),
  triggerWorkflow: (workflowName) => ipcRenderer.invoke('workflows:trigger', workflowName),
  runAgent: (workflowName, userQuery) => ipcRenderer.invoke('agent:run', workflowName, userQuery),
  // MCP config management
  loadMCPConfig: () => ipcRenderer.invoke('mcp-config:load'),
  saveMCPConfig: (config) => ipcRenderer.invoke('mcp-config:save', config),
  testMCPServer: (serverName, serverConfig) => ipcRenderer.invoke('mcp-server:test', serverName, serverConfig),
  // LLM config management
  loadLLMConfig: () => ipcRenderer.invoke('llm-config:load'),
  saveLLMConfig: (config) => ipcRenderer.invoke('llm-config:save', config),
  setApiKey: (llmName, apiKey) => ipcRenderer.invoke('llm-config:set-api-key', llmName, apiKey),
  getApiKeyMasked: (llmName) => ipcRenderer.invoke('llm-config:get-api-key-masked', llmName),
  hasApiKey: (llmName) => ipcRenderer.invoke('llm-config:has-api-key', llmName),
  deleteApiKey: (llmName) => ipcRenderer.invoke('llm-config:delete-api-key', llmName),
  saveSqlFile: (filename, content) => ipcRenderer.invoke('sql:save', filename, content),
  loadSqlFile: (relativePath) => ipcRenderer.invoke('sql:load', relativePath),
  loadWorkflowRuns: () => ipcRenderer.invoke('workflow-runs:load'),
  loadLatestWorkflowResult: () => ipcRenderer.invoke('workflow-result:latest'),
  loadWorkflowResult: (filename) => ipcRenderer.invoke('workflow-result:load', filename),
  clearWorkflowOutputs: () => ipcRenderer.invoke('workflow-outputs:clear'),
  // Docker management
  checkDocker: () => ipcRenderer.invoke('docker:check'),
  dockerStart: () => ipcRenderer.invoke('docker:start'),
  dockerStop: () => ipcRenderer.invoke('docker:stop'),
  dockerStatus: () => ipcRenderer.invoke('docker:status'),
  isElectron: true
});