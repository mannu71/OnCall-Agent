// MCP Server Configuration Service
// Manages loading and saving MCP server configurations via Electron file API

// Cache for config to reduce file reads
let configCache = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 5000; // 5 second cache

/**
 * Invalidate the config cache
 */
export const invalidateCache = () => {
    configCache = null;
    cacheTimestamp = 0;
};

/**
 * Load MCP configuration from file via Electron API
 * Uses caching to prevent excessive file reads
 */
export const loadMCPConfig = async () => {
    const now = Date.now();
    if (configCache && (now - cacheTimestamp) < CACHE_TTL_MS) {
        return configCache;
    }

    try {
        if (window.electronAPI?.loadMCPConfig) {
            const config = await window.electronAPI.loadMCPConfig();
            configCache = config || { servers: {}, inputs: [] };
            cacheTimestamp = now;
            return configCache;
        }
        console.warn('Electron API not available, using defaults');
        return { servers: {}, inputs: [] };
    } catch (error) {
        console.error('Error loading MCP config:', error);
        return { servers: {}, inputs: [] };
    }
};

/**
 * Save MCP configuration to file via Electron API
 */
export const saveMCPConfig = async (config) => {
    try {
        if (window.electronAPI?.saveMCPConfig) {
            const result = await window.electronAPI.saveMCPConfig(config);
            if (result.success) {
                // Update cache on successful save
                configCache = config;
                cacheTimestamp = Date.now();
            }
            return result.success;
        }
        console.error('Electron API not available');
        return false;
    } catch (error) {
        console.error('Error saving MCP config:', error);
        return false;
    }
};

/**
 * Get all MCP servers
 */
export const getMCPServers = async () => {
    const config = await loadMCPConfig();
    return config.servers || {};
};

/**
 * Add a new MCP server
 */
export const addMCPServer = async (serverName, serverConfig) => {
    const config = await loadMCPConfig();
    config.servers[serverName] = serverConfig;
    await saveMCPConfig(config);
    return config;
};

/**
 * Update an existing MCP server and sync to workflows
 */
export const updateMCPServer = async (serverName, serverConfig, newServerName = null) => {
    // Invalidate cache first to ensure we get fresh data
    invalidateCache();
    const config = await loadMCPConfig();
    
    const actualNewName = newServerName || serverName;
    
    // If renaming, delete old and add new
    if (newServerName && newServerName !== serverName) {
        delete config.servers[serverName];
        config.servers[newServerName] = serverConfig;
    } else if (config.servers[serverName]) {
        // Fully replace the server config (don't merge, to ensure args are properly updated)
        config.servers[serverName] = serverConfig;
    } else {
        console.warn('updateMCPServer: server not found:', serverName);
        return config;
    }
    
    const success = await saveMCPConfig(config);
    console.log('updateMCPServer:', serverName, '->', actualNewName, 'success:', success, 'config:', serverConfig);
    
    // Also update workflows that use this MCP server
    if (success) {
        await syncMCPServerToWorkflows(serverName, serverConfig, actualNewName);
    }
    
    return config;
};

/**
 * Sync MCP server config changes to all workflows that use it
 */
export const syncMCPServerToWorkflows = async (oldServerName, serverConfig, newServerName = null) => {
    const actualNewName = newServerName || oldServerName;
    
    try {
    const { default: agentApiClient } = await import('../services/agentApiClient.js');
    
    const workflows = await agentApiClient.listWorkflows();
    if (!workflows || !Array.isArray(workflows)) {
      return false;
    }
    
    let updated = false;
    
    for (const workflow of workflows) {
      if (!workflow.nodes) continue;
      
      let workflowUpdated = false;
      for (const node of workflow.nodes) {
        // Check if this node is a tool that uses the MCP server
        if (node.type === 'tool' && 
            node.data?.toolType === 'mcp-server' && 
            node.data?.label === oldServerName) {
          
          // Update the node's label and mcpConfig
          node.data.label = actualNewName;
          node.data.mcpConfig = { ...serverConfig };
          workflowUpdated = true;
          updated = true;
          console.log(`Synced MCP server "${oldServerName}" -> "${actualNewName}" in workflow "${workflow.name}"`);
        }
      }
      
      if (workflowUpdated) {
        await agentApiClient.updateWorkflow(workflow.name, workflow);
      }
    }
    
    console.log('Workflows synced with MCP server changes');
    return true;
  } catch (error) {
    console.error('Error syncing MCP server to workflows:', error);
    return false;
  }
};

/**
 * Delete an MCP server
 */
export const deleteMCPServer = async (serverName) => {
    const config = await loadMCPConfig();
    delete config.servers[serverName];
    await saveMCPConfig(config);
    return config;
};

/**
 * Get input definitions
 */
export const getMCPInputs = async () => {
    const config = await loadMCPConfig();
    return config.inputs || [];
};

/**
 * Convert MCP servers to node items for the sidebar
 */
export const convertServersToNodeItems = (servers) => {
    return Object.entries(servers)
        .filter(([_, config]) => !config.disabled)
        .map(([name, config]) => ({
            type: 'tool',
            icon: config.icon || '🔧',
            title: name,
            description: config.description || 'MCP Server',
            data: {
                label: name,
                toolType: 'mcp-server',
                mcpConfig: config,
                status: 'Ready'
            }
        }));
};

/**
 * Get input values (the actual values set by user)
 */
export const getMCPInputValues = async () => {
    const config = await loadMCPConfig();
    return config.inputValues || {};
};

/**
 * Set input values
 */
export const setMCPInputValues = async (inputValues) => {
    const config = await loadMCPConfig();
    config.inputValues = inputValues;
    await saveMCPConfig(config);
    return config;
};

/**
 * Update a single input value
 */
export const updateMCPInputValue = async (inputId, value) => {
    const config = await loadMCPConfig();
    config.inputValues = config.inputValues || {};
    config.inputValues[inputId] = value;
    await saveMCPConfig(config);
    return config;
};
