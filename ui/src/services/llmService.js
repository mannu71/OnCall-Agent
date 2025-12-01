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
    if (window.electronAPI?.loadLLMConfig) {
      const config = await window.electronAPI.loadLLMConfig();
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
    if (window.electronAPI?.saveLLMConfig) {
      const result = await window.electronAPI.saveLLMConfig(config);
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
 * Update an existing LLM configuration
 */
export const updateLLM = async (name, llmConfig) => {
  const config = await loadLLMConfig();
  if (config.llms && config.llms[name]) {
    config.llms[name] = { ...config.llms[name], ...llmConfig };
    await saveLLMConfig(config);
  }
  return config;
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
      // Use environment variable reference instead of hardcoded key
      apiKeyEnvVar: config.apiKeyEnvVar || '',
      status: 'Available'
    }
  }));
};
