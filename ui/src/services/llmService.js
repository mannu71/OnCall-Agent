// LLM Configuration Service
// Manages loading and saving LLM configurations via Electron file API

// Cache for config to reduce file reads
let configCache = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 5000; // 5 second cache

const DEFAULT_LLMS = {
  'gpt-4o-mini': {
    provider: 'OpenAI',
    model: 'gpt-4o-mini',
    icon: '⚡',
    description: 'Fast and efficient GPT-4o Mini'
  }
};

/**
 * Invalidate the config cache
 */
export const invalidateCache = () => {
  configCache = null;
  cacheTimestamp = 0;
};

/**
 * Load LLM configuration from file via Electron API
 * Uses caching to prevent excessive file reads
 */
export const loadLLMConfig = async () => {
  const now = Date.now();
  if (configCache && (now - cacheTimestamp) < CACHE_TTL_MS) {
    return configCache;
  }

  try {
    if (globalThis.electronAPI?.loadLLMConfig) {
      const config = await globalThis.electronAPI.loadLLMConfig();
      configCache = config || { llms: DEFAULT_LLMS };
      cacheTimestamp = now;
      return configCache;
    }
    console.warn('Electron API not available, using defaults');
    return { llms: DEFAULT_LLMS };
  } catch (error) {
    console.error('Error loading LLM config:', error);
    return { llms: DEFAULT_LLMS };
  }
};

/**
 * Save LLM configuration to file via Electron API
 */
export const saveLLMConfig = async (config) => {
  try {
    if (globalThis.electronAPI?.saveLLMConfig) {
      const result = await globalThis.electronAPI.saveLLMConfig(config);
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
    console.error('Error saving LLM config:', error);
    return false;
  }
};

/**
 * Get all configured LLMs
 */
export const getLLMs = async () => {
  const config = await loadLLMConfig();
  return config.llms || {};
};

/**
 * Add a new LLM configuration
 */
export const addLLM = async (name, llmConfig) => {
  const config = await loadLLMConfig();
  config.llms = config.llms || {};
  config.llms[name] = llmConfig;
  await saveLLMConfig(config);
  return config;
};

/**
 * Update an existing LLM configuration and sync to workflows
 */
export const updateLLM = async (name, llmConfig, newName = null) => {
  invalidateCache();
  const config = await loadLLMConfig();
  
  const actualNewName = newName || name;
  
  // If renaming, delete old and add new
  if (newName && newName !== name) {
    delete config.llms[name];
    config.llms[newName] = llmConfig;
  } else if (config.llms?.[name]) {
    config.llms[name] = { ...config.llms[name], ...llmConfig };
  }
  
  const success = await saveLLMConfig(config);
  
  // Sync LLM changes to workflows
  if (success) {
    await syncLLMToWorkflows(name, llmConfig, actualNewName);
  }
  
  return config;
};

/**
 * Update LLM nodes in a workflow
 */
const updateLLMNodes = (nodes, oldName, actualNewName, llmConfig) => {
  const updatedNodeIds = [];
  for (const node of nodes) {
    if (node.type === 'llm' && (node.data?.label === oldName || node.data?.model === oldName)) {
      node.data.label = actualNewName;
      node.data.model = llmConfig.model;
      node.data.provider = llmConfig.provider;
      node.data.agent = llmConfig.provider?.toLowerCase() === 'openai' ? 'openai' : 'groq';
      if (llmConfig.temperature !== undefined) {
        node.data.temperature = llmConfig.temperature;
      }
      updatedNodeIds.push(node.id);
    }
  }
  return updatedNodeIds;
};

/**
 * Update agent nodes connected to LLM nodes via edges
 */
const updateConnectedAgentNodes = (nodes, edges, updatedLLMNodeIds, llmConfig) => {
  let updated = false;
  for (const edge of edges) {
    if (updatedLLMNodeIds.includes(edge.source) && edge.targetHandle === 'model') {
      const agentNode = nodes.find(n => n.id === edge.target && n.type === 'agent');
      if (agentNode) {
        agentNode.data.model = llmConfig.model;
        agentNode.data.agent = llmConfig.provider?.toLowerCase() === 'openai' ? 'openai' : 'groq';
        updated = true;
      }
    }
  }
  return updated;
};

/**
 * Update agent nodes that directly reference the LLM model
 */
const updateDirectAgentReferences = (nodes, oldName, actualNewName) => {
  let updated = false;
  for (const node of nodes) {
    if (node.type === 'agent' && node.data?.model === oldName) {
      node.data.model = actualNewName;
      updated = true;
    }
  }
  return updated;
};

/**
 * Sync LLM config changes to all workflows that use it
 */
export const syncLLMToWorkflows = async (oldName, llmConfig, newName = null) => {
  const actualNewName = newName || oldName;
  
  try {
    const { default: agentApiClient } = await import('../services/agentApiClient.js');
    
    const workflows = await agentApiClient.listWorkflows();
    if (!workflows || !Array.isArray(workflows)) {
      return false;
    }
    
    for (const workflow of workflows) {
      if (!workflow.nodes || !workflow.edges) continue;
      
      const updatedLLMNodeIds = updateLLMNodes(workflow.nodes, oldName, actualNewName, llmConfig);
      const connectedUpdated = updateConnectedAgentNodes(workflow.nodes, workflow.edges, updatedLLMNodeIds, llmConfig);
      const directUpdated = updateDirectAgentReferences(workflow.nodes, oldName, actualNewName);
      
      const workflowUpdated = updatedLLMNodeIds.length > 0 || connectedUpdated || directUpdated;
      
      if (workflowUpdated) {
        await agentApiClient.updateWorkflow(workflow.name, workflow);
      }
    }
    
    return true;
  } catch (error) {
    console.error('Error syncing LLM to workflows:', error);
    return false;
  }
};

/**
 * Delete an LLM configuration
 */
export const deleteLLM = async (name) => {
  const config = await loadLLMConfig();
  if (config.llms) {
    delete config.llms[name];
    await saveLLMConfig(config);
  }
  return config;
};

/**
 * Convert LLMs to node items for the sidebar
 */
export const convertLLMsToNodeItems = (llms) => {
  if (!llms || typeof llms !== 'object') {
    return [];
  }
  return Object.entries(llms).map(([name, config]) => ({
    type: 'llm',
    icon: config.icon || '🧠',
    title: name,
    description: config.description || `${config.provider} - ${config.model}`,
    data: {
      label: name,
      model: config.model,
      provider: config.provider,
      agent: config.provider?.toLowerCase() === 'openai' ? 'openai' : 'groq',
      temperature: config.temperature ?? 0,
      // Use environment variable reference instead of hardcoded key
      apiKeyEnvVar: config.apiKeyEnvVar || '',
      status: 'Available'
    }
  }));
};
