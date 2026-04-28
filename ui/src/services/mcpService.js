// MCP Server Configuration Service
// Manages loading and saving MCP server configurations via Agent API

import agentApiClient from './agentApiClient.js';

// Cache for config to reduce API calls
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
 * Load MCP configuration from Agent API
 * Uses caching to prevent excessive API calls
 */
export const loadMCPConfig = async () => {
    const now = Date.now();
    if (configCache && (now - cacheTimestamp) < CACHE_TTL_MS) {
        return configCache;
    }

    try {
        // Try to use Agent API first
        const config = await agentApiClient.getMCPConfig();
        configCache = config || { servers: {}, inputs: [], inputValues: {} };
        cacheTimestamp = now;
        return configCache;
    } catch (error) {
        console.warn('Agent API not available, falling back to Electron API:', error.message);

        // Fallback to Electron API if available (for desktop app)
        if (globalThis.electronAPI?.loadMCPConfig) {
            try {
                const config = await globalThis.electronAPI.loadMCPConfig();
                configCache = config || { servers: {}, inputs: [], inputValues: {} };
                cacheTimestamp = now;
                return configCache;
            } catch (electronError) {
                console.error('Error loading MCP config from Electron:', electronError);
            }
        }

        return { servers: {}, inputs: [], inputValues: {} };
    }
};

/**
 * Save MCP configuration via Agent API
 */
export const saveMCPConfig = async (config) => {
    try {
        // Try Agent API first
        await agentApiClient.saveMCPConfig(config);
        configCache = config;
        cacheTimestamp = Date.now();
        return true;
    } catch (error) {
        console.warn('Agent API not available, falling back to Electron API:', error.message);

        // Fallback to Electron API
        if (globalThis.electronAPI?.saveMCPConfig) {
            try {
                const result = await globalThis.electronAPI.saveMCPConfig(config);
                if (result.success) {
                    configCache = config;
                    cacheTimestamp = Date.now();
                    return true;
                }
            } catch (electronError) {
                console.error('Error saving MCP config via Electron:', electronError);
            }
        }

        return false;
    }
};

/**
 * Get all MCP servers
 */
export const getMCPServers = async () => {
    try {
        // Try Agent API first
        return await agentApiClient.getMCPServers();
    } catch (error) {
        console.warn('Agent API not available, falling back to loadMCPConfig:', error.message);
        const config = await loadMCPConfig();
        return config.servers || {};
    }
};

/**
 * Add a new MCP server
 */
export const addMCPServer = async (serverName, serverConfig) => {
    try {
        // Try Agent API first
        await agentApiClient.createMCPServer({
            name: serverName,
            ...serverConfig
        });
        invalidateCache();
        return await getMCPServers();
    } catch (error) {
        console.warn('Agent API not available, falling back to file-based save:', error.message);

        // Fallback to file-based approach
        const config = await loadMCPConfig();
        config.servers[serverName] = serverConfig;
        await saveMCPConfig(config);
        return config.servers;
    }
};

/**
 * Update an existing MCP server and sync to workflows
 */
export const updateMCPServer = async (serverName, serverConfig, newServerName = null) => {
    // Invalidate cache first to ensure we get fresh data
    invalidateCache();

    const actualNewName = newServerName || serverName;

    try {
        // Try Agent API first
        await agentApiClient.updateMCPServer(serverName, {
            name: newServerName || undefined,
            ...serverConfig
        });

        // Also update workflows that use this MCP server
        await syncMCPServerToWorkflows(serverName, serverConfig, actualNewName);

        invalidateCache();
        return await getMCPServers();
    } catch (error) {
        console.warn('Agent API not available, falling back to file-based save:', error.message);

        // Fallback to file-based approach
        const config = await loadMCPConfig();

        // If renaming, delete old and add new
        if (newServerName && newServerName !== serverName) {
            delete config.servers[serverName];
            config.servers[newServerName] = serverConfig;
        } else if (config.servers[serverName]) {
            config.servers[serverName] = serverConfig;
        } else {
            console.warn('updateMCPServer: server not found:', serverName);
            return config.servers;
        }

        const success = await saveMCPConfig(config);

        if (success) {
            await syncMCPServerToWorkflows(serverName, serverConfig, actualNewName);
        }

        return config.servers;
    }
};

/**
 * Sync MCP server config changes to all workflows that use it
 */
export const syncMCPServerToWorkflows = async (oldServerName, serverConfig, newServerName = null) => {
    const actualNewName = newServerName || oldServerName;

    try {
        const workflows = await agentApiClient.listWorkflows();
        if (!workflows || !Array.isArray(workflows)) {
            return false;
        }

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
                }
            }

            if (workflowUpdated) {
                await agentApiClient.updateWorkflow(workflow.name, workflow);
            }
        }

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
    try {
        // Try Agent API first
        await agentApiClient.deleteMCPServer(serverName);
        invalidateCache();
        return await getMCPServers();
    } catch (error) {
        console.warn('Agent API not available, falling back to file-based save:', error.message);

        // Fallback to file-based approach
        const config = await loadMCPConfig();
        delete config.servers[serverName];
        await saveMCPConfig(config);
        return config.servers;
    }
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
    try {
        // Try Agent API first
        return await agentApiClient.getMCPInputValues();
    } catch (error) {
        console.warn('Agent API not available, falling back to loadMCPConfig:', error.message);
        const config = await loadMCPConfig();
        return config.inputValues || {};
    }
};

/**
 * Set input values
 */
export const setMCPInputValues = async (inputValues) => {
    try {
        // Try Agent API first
        return await agentApiClient.setMCPInputValues(inputValues);
    } catch (error) {
        console.warn('Agent API not available, falling back to file-based save:', error.message);

        const config = await loadMCPConfig();
        config.inputValues = inputValues;
        await saveMCPConfig(config);
        return config.inputValues;
    }
};

/**
 * Update a single input value
 */
export const updateMCPInputValue = async (inputId, value) => {
    try {
        // Try Agent API first
        return await agentApiClient.updateMCPInputValue(inputId, value);
    } catch (error) {
        console.warn('Agent API not available, falling back to file-based save:', error.message);

        const config = await loadMCPConfig();
        config.inputValues = config.inputValues || {};
        config.inputValues[inputId] = value;
        await saveMCPConfig(config);
        return config.inputValues;
    }
};
