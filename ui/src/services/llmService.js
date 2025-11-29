// LLM Configuration Service
// Manages loading and saving LLM configurations via Electron file API

const DEFAULT_LLMS = {
  'gpt-4o-mini': {
    provider: 'OpenAI',
    model: 'gpt-4o-mini',
    icon: '⚡',
    description: 'Fast and efficient GPT-4o Mini'
  }
};

/**
 * Load LLM configuration from file via Electron API
 */
export const loadLLMConfig = async () => {
  try {
    if (window.electronAPI?.loadLLMConfig) {
      const config = await window.electronAPI.loadLLMConfig();
      return config || { llms: DEFAULT_LLMS };
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
      apiKey: config.apiKey || '',
      status: 'Available'
    }
  }));
};
