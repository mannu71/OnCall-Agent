/**
 * Unified Workflow Runner
 * 
 * This is the NEW entry point for running workflows using the unified engine.
 * It replaces the old run.js which only supported ReAct agents.
 * 
 * Usage:
 *   node src/agents/run-unified.js "WorkflowName" "User query here"
 *   node src/agents/run-unified.js "Daily Report"  (for orchestrator workflows)
 */

import { WorkflowEngine } from '../workflow/index.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import logger from '../shared/logger.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load config paths
const workflowsPath = path.resolve(__dirname, '../../data/config/workflows.json');
const llmConfigPath = path.resolve(__dirname, '../../data/config/llm-config.json');

/**
 * Load LLM configurations from Settings
 */
function loadLLMConfig() {
  try {
    if (fs.existsSync(llmConfigPath)) {
      const config = JSON.parse(fs.readFileSync(llmConfigPath, 'utf-8'));
      return config.llms || {};
    }
  } catch (err) {
    logger.warn('Could not load LLM config', { error: err.message });
  }
  return {};
}

/**
 * Merge API key from LLM config into workflow's LLM node
 */
function mergeApiKeyFromConfig(workflow, llmConfigs) {
  const llmNode = workflow.nodes?.find(n => n.type === 'llm');
  if (!llmNode) {
    return workflow;
  }

  const llmName = llmNode.data.llmName || llmNode.data.label;
  const model = llmNode.data.model;
  const provider = (llmNode.data.provider || 'OpenAI').toLowerCase();
  
  // Look for exact name match first
  let matchingConfig = llmConfigs[llmName];
  
  // If not found, try to match by model name
  if (!matchingConfig) {
    for (const [name, config] of Object.entries(llmConfigs)) {
      if (config.model === model) {
        matchingConfig = config;
        break;
      }
    }
  }

  // If still not found, try to find any config with same provider
  if (!matchingConfig) {
    for (const [name, config] of Object.entries(llmConfigs)) {
      if ((config.provider || '').toLowerCase() === provider) {
        matchingConfig = config;
        break;
      }
    }
  }

  if (matchingConfig && matchingConfig.apiKey) {
    llmNode.data = {
      ...llmNode.data,
      apiKey: matchingConfig.apiKey,
      provider: matchingConfig.provider || llmNode.data.provider,
      endpoint: matchingConfig.endpoint || llmNode.data.endpoint,
      baseUrl: matchingConfig.baseUrl || llmNode.data.baseUrl
    };
  } else {
    // Check for environment variables as fallback
    const envKey = getEnvApiKey(provider);
    if (envKey) {
      llmNode.data = {
        ...llmNode.data,
        apiKey: envKey
      };
    }
  }

  return workflow;
}

/**
 * Get API key from environment variable based on provider
 */
function getEnvApiKey(provider) {
  switch (provider.toLowerCase()) {
    case 'openai':
      return process.env.OPENAI_API_KEY;
    case 'anthropic':
      return process.env.ANTHROPIC_API_KEY;
    case 'google':
    case 'gemini':
      return process.env.GOOGLE_API_KEY;
    case 'groq':
      return process.env.GROQ_API_KEY;
    case 'azure openai':
    case 'azure':
      return process.env.AZURE_OPENAI_API_KEY;
    default:
      return null;
  }
}

/**
 * Inject current date into agent instructions
 */
function injectDateVariables(workflow) {
  const now = new Date();
  const currentDateTime = now.toISOString();
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  const yesterdayDateTime = yesterday.toISOString();
  
  const agentNode = workflow.nodes?.find(n => n.type === 'agent');
  if (agentNode && agentNode.data.instructions) {
    let instructions = agentNode.data.instructions;
    
    instructions = instructions.replace(/{{current_date_time}}/g, currentDateTime);
    instructions = instructions.replace(/{{yesterday_date_time}}/g, yesterdayDateTime);
    instructions = instructions.replace(/{{current_date}}/g, now.toISOString().split('T')[0]);
    
    agentNode.data.instructions = instructions;
  }

  return workflow;
}

async function main() {
  const workflowName = process.argv[2];
  const userQuery = process.argv[3];

  if (!workflowName) {
    console.error('Usage: node run-unified.js <workflow-name> [user-query]');
    console.error('Example: node run-unified.js "OnCall" "Why did profiles fail today?"');
    console.error('Example: node run-unified.js "Daily Report"');
    process.exit(1);
  }

  logger.info('Unified workflow runner started', {
    workflowName,
    hasUserQuery: !!userQuery
  });

  try {
    // Load workflows and LLM config
    const workflows = JSON.parse(fs.readFileSync(workflowsPath, 'utf-8'));
    const llmConfigs = loadLLMConfig();
    
    // Find the specified workflow
    let workflow = workflows.find(w => w.name === workflowName || w.id === workflowName);
    
    if (!workflow) {
      logger.error('Workflow not found', { workflowName });
      console.log('\nAvailable workflows:');
      workflows.forEach(w => console.log(`  - ${w.name} (${w.id})`));
      process.exit(1);
    }

    // Prepare workflow
    workflow = mergeApiKeyFromConfig(workflow, llmConfigs);
    workflow = injectDateVariables(workflow);

    logger.info('Workflow loaded', {
      workflowId: workflow.id,
      workflowName: workflow.name,
      nodeCount: workflow.nodes?.length || 0
    });

    // Create unified engine
    const engine = new WorkflowEngine();

    // Validate workflow
    const validation = engine.validate(workflow);
    if (!validation.valid) {
      logger.error('Workflow validation failed', {
        error: validation.error
      });
      process.exit(1);
    }

    logger.info('Workflow validated', {
      strategy: validation.strategy
    });

    // Execute workflow
    const result = await engine.execute(workflow, {
      userQuery,
      variables: {
        current_date: new Date().toISOString().split('T')[0]
      }
    });

    // Display result
    logger.info('Workflow execution completed', {
      executionId: result.executionId,
      success: result.success,
      duration: result.duration
    });

    console.log('\n' + '='.repeat(80));
    console.log('WORKFLOW EXECUTION RESULT');
    console.log('='.repeat(80));
    console.log(`Workflow: ${result.workflowName}`);
    console.log(`Strategy: ${result.strategy}`);
    console.log(`Duration: ${result.duration}ms`);
    console.log(`Success: ${result.success}`);
    console.log('='.repeat(80));

    if (result.finalAnswer) {
      console.log('\n🔥 FINAL ANSWER:\n');
      console.log(result.finalAnswer);
    } else if (result.output) {
      console.log('\n📊 OUTPUT:\n');
      console.log(result.output);
    }

    console.log('\n' + '='.repeat(80));

    // Cleanup
    await engine.cleanup();

    process.exit(0);

  } catch (error) {
    logger.error('Workflow execution failed', {
      error: error.message,
      stack: error.stack
    });
    
    console.error('\n❌ ERROR:', error.message);
    process.exit(1);
  }
}

main();
