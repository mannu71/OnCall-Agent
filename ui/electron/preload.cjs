// Preload script (CommonJS) to avoid ESM loading issues
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  loadSchedules: () => ipcRenderer.invoke('schedules:load'),
  saveSchedules: (schedules) => ipcRenderer.invoke('schedules:save', schedules),
  loadWorkflows: () => ipcRenderer.invoke('workflows:load'),
  saveWorkflows: (workflows) => ipcRenderer.invoke('workflows:save', workflows),
  triggerWorkflow: (workflowName) => ipcRenderer.invoke('workflows:trigger', workflowName),
  // MCP config management
  loadMCPConfig: () => ipcRenderer.invoke('mcp-config:load'),
  saveMCPConfig: (config) => ipcRenderer.invoke('mcp-config:save', config),
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