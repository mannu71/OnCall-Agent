import logger from '../shared/logger.js';
import { createMCPClientManager } from './mcp-client.js';
import { WorkflowParserRegistry } from './workflow-format-parser.js';
import { SQLASTParser } from './sql-ast-parser.js';
import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Cached regex patterns for performance
const TEMPLATE_VAR_REGEX = /\{\{\s*([^}]+?)\s*\}\}/g;
const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

/* -----------------------
   Helper: Format value for SQL interpolation
----------------------- */

function formatSqlValue(value, varPath) {
  if (value !== null && typeof value === 'object') {
    logger.warn(`Variable {{${varPath}}} resolved to an object/complex value; using NULL to avoid SQL injection`);
    return 'NULL';
  }

  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'true' : 'false';

  if (value === null || value === undefined) return 'NULL';

  // Quote all strings (including UUIDs, dates, etc.)
  if (typeof value === 'string') {
    return `'${value.replace(/'/g, "''")}'`; // Escape single quotes
  }

  return String(value);
}

/* -----------------------
   SQL File Parser (AST-based) wrapper
----------------------- */
function parseSqlFileToWorkflow(sqlContent, orchestratorData) {
  const tools = orchestratorData.tools || [];
  logger.info(`parseSqlFileToWorkflow: received ${tools.length} tools: ${tools.map(t => t.id || t.data?.label).join(', ')}`);

  const dataWithContent = { ...orchestratorData, sqlContent };

  const astParser = new SQLASTParser();
  const parseResult = astParser.parse(sqlContent, dataWithContent);

  logger.info(`Parsed ${parseResult.queries.length} queries with AST`);
  logger.info(`Execution order: ${parseResult.executionOrder.join(' -> ')}`);

  astParser.printDependencyGraph();

  const parallelGroups = astParser.getParallelGroups();
  if (parallelGroups.some(g => g.length > 1)) {
    logger.info(`Found ${parallelGroups.filter(g => g.length > 1).length} groups that can be parallelized`);
  }

  let targetServer = null;
  if (tools.length > 0) {
    targetServer = tools[0].id;
    logger.info(`Using database tool: ${tools[0].data?.label || tools[0].id}`);
  }

  if (!targetServer) {
    throw new Error('No database tool/server available for SQL execution');
  }

  const steps = astParser.toWorkflowSteps(targetServer, dataWithContent);

  return JSON.stringify({ steps });
}

/* -----------------------
   VariableResolver factory (updated)
----------------------- */
export function createVariableResolver(initialVariables = {}) {
  const vars = new Map();

  for (const [key, value] of Object.entries(initialVariables)) {
    vars.set(key, value);
  }

  const setStepResult = (stepName, result) => {
    vars.set(stepName, result);
    logger.debug(`Stored result for step: ${stepName}`);
  };

  const unwrapArray = (val, stepName) => {
    if (!Array.isArray(val)) return val;
    if (val.length === 0) {
      logger.warn(`Step ${stepName} returned empty result set`);
      return undefined;
    }
    return val[0];
  };

  const getProperty = (obj, propName) => {
    const keys = Object.keys(obj);
    return keys.find(k => k.toLowerCase() === propName.toLowerCase());
  };

  const _resolveString = (value) => {
    if (typeof value !== 'string') return value;

    return value.replace(TEMPLATE_VAR_REGEX, (match, path) => {
      const parts = path.trim().split('.').map(p => p.trim()).filter(Boolean);
      if (parts.length === 0) return 'NULL';

      const stepName = parts[0];

      if (!vars.has(stepName)) {
        logger.warn(`Variable not found: ${stepName}`);
        return 'NULL';
      }

      let result = vars.get(stepName);
      
      // Parse JSON string results
      if (typeof result === 'string') {
        try {
          result = JSON.parse(result);
        } catch (e) {
          logger.warn(`Failed to parse result for ${stepName}: ${e.message}`);
        }
      }
      
      result = unwrapArray(result, stepName);
      if (result === undefined) return 'NULL';

      for (let i = 1; i < parts.length; i++) {
        const propName = parts[i];

        if (result == null) {
          logger.warn(`Cannot access property ${propName} on ${stepName} (value is ${String(result)})`);
          return 'NULL';
        }

        result = unwrapArray(result, stepName);
        if (result === undefined) return 'NULL';

        if (typeof result !== 'object') {
          logger.warn(`Cannot access property ${propName} on ${stepName} (not an object)`);
          return 'NULL';
        }

        const matchedKey = getProperty(result, propName);
        if (!matchedKey) {
          logger.warn(`Cannot access property ${propName} on ${stepName}`);
          return 'NULL';
        }

        result = result[matchedKey];
      }

      result = unwrapArray(result, stepName);
      return formatSqlValue(result, path);
    });
  };

  const resolveObject = (input) => {
    if (input === null || input === undefined) return input;
    if (typeof input === 'string') return _resolveString(input);
    if (Array.isArray(input)) return input.map(item => resolveObject(item));
    if (typeof input === 'object') {
      const out = {};
      for (const [k, v] of Object.entries(input)) {
        out[k] = resolveObject(v);
      }
      return out;
    }
    return input;
  };

  const has = (key) => vars.has(key);
  const clear = () => vars.clear();

  return { setStepResult, resolveObject, has, clear, _internals: { vars } };
}

/* -----------------------
   OrchestratorExecutor factory (functional)
----------------------- */
export function createOrchestratorExecutor(executionGraph, opts = {}) {
  const mcpManager = opts.mcpManager || createMCPClientManager();
  const parserRegistry = opts.parserRegistry || new WorkflowParserRegistry();

  const defaultVariables = {
    current_date: new Date().toISOString().split('T')[0],
    next_date: new Date(Date.now() + 86400000).toISOString().split('T')[0],
    ...(opts.variables || {})
  };

  const variableResolver = opts.variableResolver || createVariableResolver(defaultVariables);
  const stepTimeoutMs = opts.stepTimeoutMs || null;

  const results = [];

  /* -----------------------
     Optimized summary that prefers metadata.label
  ----------------------- */
  const _formatResultsSummary = () => {
    if (results.length === 0) {
      return "Workflow completed\n0 steps successful";
    }

    const summaryLines = [];

    for (const result of results) {
      if (!result.success || !result.result) continue;

      let data = result.result;
      if (typeof data === "string") {
        try {
          data = JSON.parse(data);
        } catch {
          continue;
        }
      }

      if (!Array.isArray(data) || data.length === 0) continue;
      const row = data[0];

      // 1) Primary: metadata.label (explicit from parser)
      let label = result.metadata && (result.metadata.label || result.metadata.Label || result.metadata.LABEL);

      // 2) Fallback: first numeric column
      let value = null;
      let fallbackLabel = null;
      for (const key in row) {
        const val = row[key];
        if (val !== "" && val !== null && !isNaN(val)) {
          value = val;
          fallbackLabel = key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
          break;
        }
      }

      if (value === null) continue;

      const finalLabel = label || fallbackLabel || result.step;
      summaryLines.push(`${finalLabel}\n${value}`);
    }

    return summaryLines.length > 0
      ? summaryLines.join("\n\n")
      : `Workflow completed\n${results.filter(r => r.success).length} steps successful`;
  };

  const _extractToolResult = (response) => {
    if (!response?.content) return response;
    if (!Array.isArray(response.content)) return response;

    if (response.content.length === 1 && response.content[0].type === 'text') {
      return response.content[0].text;
    }

    return response.content;
  };

  const _callToolWithOptionalTimeout = async (client, toolName, params) => {
    if (!stepTimeoutMs) {
      return client.callTool(toolName, params);
    }
    return await Promise.race([
      client.callTool(toolName, params),
      new Promise((_, rej) => setTimeout(() => rej(new Error('Step timeout exceeded')), stepTimeoutMs))
    ]);
  };

  const connectToMCPServers = async () => {
    const nodes = executionGraph.tools || [];
    logger.info(`Connecting to ${nodes.length} MCP servers`);
    return mcpManager.connectAll(nodes);
  };

  const loadWorkflowDefinition = async () => {
    const orchestratorData = executionGraph.orchestrator?.data || {};
    const contextData = {
      tools: executionGraph.tools,
      orchestrator: executionGraph.orchestrator
    };

    let definition;

    const sqlFile = orchestratorData.sqlFile || orchestratorData.fileName;
    if (sqlFile) {
      const sqlFilePath = path.resolve(__dirname, '..', '..', 'data', 'config', sqlFile);
      logger.info(`Loading SQL workflow from file: ${sqlFilePath}`);

      try {
        const sqlContent = await fs.readFile(sqlFilePath, 'utf-8');
        definition = parseSqlFileToWorkflow(sqlContent, contextData);
        logger.info('Parsing SQL file workflow definition');
      } catch (err) {
        if (err.code === 'ENOENT') throw new Error(`SQL file not found: ${sqlFilePath}`);
        throw err;
      }
    } else {
      definition = orchestratorData.workflowDefinition;
      if (!definition) throw new Error('No workflow definition found in orchestrator node');
      logger.info('Parsing workflow definition');
    }

    const steps = parserRegistry.parse(definition, contextData);
    if (!Array.isArray(steps)) throw new Error('Parsed workflow did not return an array of steps');

    return steps;
  };

  const executeStep = async (step) => {
    const { tool, server, params } = step;
    const resolvedParams = variableResolver.resolveObject(params);

    if (logger.isDebugEnabled?.()) {
      logger.debug(`Calling tool: ${tool} on server: ${server}`);
      logger.debug('Original params:', params);
      logger.debug('Resolved params:', resolvedParams);
    }

    const client = mcpManager.getClient(server);
    if (!client) throw new Error(`MCP client not found for server: ${server}`);

    const rawResponse = await _callToolWithOptionalTimeout(client, tool, resolvedParams);
    return _extractToolResult(rawResponse);
  };

  const executeStepsSequential = async (steps) => {
    for (let i = 0; i < steps.length; i++) {
      const step = steps[i];
      logger.info(`Executing step ${i + 1}/${steps.length}: ${step.name}`);

      try {
        const result = await executeStep(step);
        variableResolver.setStepResult(step.name, result);

        results.push({
          step: step.name,
          success: true,
          result,
          metadata: step.metadata,
          originalSql: step.originalSql
        });
        logger.info(`Step ${step.name} completed successfully`);
      } catch (err) {
        logger.error(`Step ${step.name} failed:`, err);
        results.push({ step: step.name, success: false, error: err.message });
        throw new Error(`Step ${step.name} failed: ${err.message}`);
      }
    }
  };

  const execute = async () => {
    try {
      logger.info('Starting workflow execution');

      await connectToMCPServers();
      const steps = await loadWorkflowDefinition();

      // default sequential; could add parallel mode in opts later
      await executeStepsSequential(steps);

      const output = _formatResultsSummary();
      logger.info('Workflow execution completed successfully');

      return { success: true, output, results: [...results] };
    } catch (err) {
      logger.error('Workflow execution failed:', err);
      return { success: false, error: err.message, results: [...results] };
    } finally {
      try { await mcpManager.disconnectAll(); } catch (e) { logger.debug('Error during disconnectAll:', e?.message || e); }
    }
  };

  const getResults = () => [...results];

  return {
    execute,
    connectToMCPServers,
    loadWorkflowDefinition,
    executeStep,
    getResults,
    _internals: { mcpManager, parserRegistry, variableResolver }
  };
}
