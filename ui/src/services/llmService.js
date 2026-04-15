// LLM Configuration Service
// Manages loading and saving LLM configurations via API

let configCache = null;
let cacheTimestamp = 0;
const CACHE_TTL_MS = 5000;

const API_BASE_URL = 'http://localhost:8000/api/v1';

export const invalidateCache = () => {
  configCache = null;
  cacheTimestamp = 0;
};

export const discoverBedrockModels = async (creds) => {
  invalidateCache();
  try {
    const response = await fetch(`${API_BASE_URL}/llm-config/discover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(creds || {}),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to discover Bedrock models');
    }
    const result = await response.json();
    return result;
  } catch (error) {
    console.error('Error discovering Bedrock models:', error);
    throw error;
  }
};

export const discoverModels = async (provider) => {
  invalidateCache();
  try {
    const response = await fetch(`${API_BASE_URL}/llm-config/discover/models`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to discover models for ${provider}`);
    }
    const result = await response.json();
    return result;
  } catch (error) {
    console.error('Error discovering models:', error);
    throw error;
  }
};

export const addDiscoveredModels = async (models, region) => {
  invalidateCache();
  try {
    const response = await fetch(`${API_BASE_URL}/llm-config/discover/add`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models, region }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to add discovered models');
    }
    const result = await response.json();
    configCache = null;
    return result;
  } catch (error) {
    console.error('Error adding discovered models:', error);
    throw error;
  }
};

export const bulkDeleteLLMs = async (names) => {
  invalidateCache();
  try {
    const response = await fetch(`${API_BASE_URL}/llm-config/bulk-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete LLM configs');
    }
    const result = await response.json();
    configCache = null;
    return result;
  } catch (error) {
    console.error('Error bulk deleting LLM configs:', error);
    throw error;
  }
};

export const loadLLMConfig = async () => {
  const now = Date.now();
  if (configCache && (now - cacheTimestamp) < CACHE_TTL_MS) {
    return configCache;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/llm-config`);
    if (response.ok) {
      const config = await response.json();
      configCache = config;
      cacheTimestamp = now;
      return configCache;
    }
    return { llms: {} };
  } catch (error) {
    console.error('Error loading LLM config:', error);
    return { llms: {} };
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
  invalidateCache();
  try {
    const response = await fetch(`${API_BASE_URL}/llm-config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, ...llmConfig }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create LLM config');
    }
    const result = await response.json();
    configCache = null;
    return result;
  } catch (error) {
    console.error('Error adding LLM config:', error);
    throw error;
  }
};

/**
 * Update an existing LLM configuration and sync to workflows
 */
export const updateLLM = async (name, llmConfig, newName = null) => {
  invalidateCache();
  const actualNewName = newName || name;

  try {
    const body = { ...llmConfig };
    if (newName && newName !== name) {
      body.name = newName;
    }

    const response = await fetch(`${API_BASE_URL}/llm-config/${encodeURIComponent(name)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to update LLM config');
    }

    // Sync LLM changes to workflows
    await syncLLMToWorkflows(name, llmConfig, actualNewName);

    configCache = null;
    return await response.json();
  } catch (error) {
    console.error('Error updating LLM config:', error);
    throw error;
  }
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
      node.data.region = llmConfig.region || 'us-east-1';
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
        agentNode.data.provider = llmConfig.provider;
        agentNode.data.region = llmConfig.region || 'us-east-1';
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
  invalidateCache();
  try {
    const response = await fetch(`${API_BASE_URL}/llm-config/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
    if (!response.ok && response.status !== 204) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to delete LLM config');
    }
    configCache = null;
    return true;
  } catch (error) {
    console.error('Error deleting LLM config:', error);
    throw error;
  }
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
      region: config.region || 'us-east-1',
      temperature: config.temperature ?? 0,
      status: 'Available'
    }
  }));
};
