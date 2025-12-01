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
 * Update an existing MCP server
 */
export const updateMCPServer = async (serverName, serverConfig) => {
    const config = await loadMCPConfig();
    if (config.servers[serverName]) {
        config.servers[serverName] = { ...config.servers[serverName], ...serverConfig };
        await saveMCPConfig(config);
    }
    return config;
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
