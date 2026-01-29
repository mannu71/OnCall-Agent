/**
 * Orchestrator Strategy
 * 
 * Executes predefined SQL-based workflows in a sequential manner.
 * This strategy is used for scheduled reports and data queries where
 * the steps are known in advance.
 * 
 * Use cases:
 * - Daily reports
 * - Scheduled data queries
 * - Multi-step SQL workflows with dependencies
 */

import { BaseStrategy } from './BaseStrategy.js';
import { createOrchestratorExecutor } from '../../worker/orchestrator-executor.js';
import { WorkflowParser } from '../../worker/workflow-parser.js';

export class OrchestratorStrategy extends BaseStrategy {
  constructor() {
    super();
    this.parser = new WorkflowParser(null); // We'll pass workflow directly
  }

  /**
   * Check if workflow is an orchestrator workflow
   * 
   * A workflow is considered an orchestrator workflow if it has:
   * - An 'orchestrator' node (defines SQL workflow)
   * - One or more 'tool' nodes (MCP servers for database access)
   * 
   * @param {Object} workflow - The workflow definition
   * @returns {boolean} True if this is an orchestrator workflow
   */
  canHandle(workflow) {
    if (!workflow.nodes || !Array.isArray(workflow.nodes)) {
      return false;
    }

    const hasOrchestratorNode = workflow.nodes.some(
      node => node.type === 'orchestrator'
    );

    return hasOrchestratorNode;
  }

  /**
   * Execute orchestrator workflow
   * 
   * @param {Object} workflow - The workflow definition
   * @param {Object} context - Execution context
   * @returns {Promise<Object>} Execution result
   */
  async execute(workflow, context) {
    const { logger, executionId, variables = {} } = context;

    logger.info('OrchestratorStrategy: Starting execution', {
      executionId,
      workflowId: workflow.id,
      workflowName: workflow.name
    });

    try {
      // Parse workflow to extract execution graph
      const executionGraph = this.parser.parseWorkflow(workflow);

      // Validate the workflow structure
      this.parser.validateWorkflow(executionGraph);

      logger.info('OrchestratorStrategy: Workflow parsed and validated', {
        executionId,
        toolCount: executionGraph.tools?.length || 0,
        hasOutput: !!executionGraph.output
      });

      // Create executor with shared services and variables
      const executor = createOrchestratorExecutor(executionGraph, {
        variables,
        stepTimeoutMs: context.timeout,
        mcpManager: context.mcpManager
      });

      // Execute the workflow
      const result = await executor.execute();

      logger.info('OrchestratorStrategy: Execution completed', {
        executionId,
        success: result.success,
        stepCount: result.results?.length || 0
      });

      return {
        type: 'orchestrator',
        success: result.success,
        output: result.output,
        results: result.results,
        error: result.error
      };

    } catch (error) {
      logger.error('OrchestratorStrategy: Execution failed', {
        executionId,
        error: error.message
      });
      throw error;
    }
  }

  /**
   * Validate orchestrator workflow structure
   * 
   * @param {Object} workflow - The workflow definition
   * @throws {Error} If workflow is invalid
   */
  validateWorkflow(workflow) {
    const orchestratorNode = workflow.nodes.find(n => n.type === 'orchestrator');

    if (!orchestratorNode) {
      throw new Error('Orchestrator workflow must have an orchestrator node');
    }

    // Check for workflow definition or SQL file
    const hasDefinition = orchestratorNode.data?.workflowDefinition;
    const hasSqlFile = orchestratorNode.data?.sqlFile || orchestratorNode.data?.fileName;

    if (!hasDefinition && !hasSqlFile) {
      throw new Error(
        'Orchestrator node must have either workflowDefinition or sqlFile'
      );
    }

    // Check for connected tools
    const hasTools = workflow.nodes.some(n => n.type === 'tool');
    if (!hasTools) {
      throw new Error('Orchestrator workflow must have at least one tool node');
    }

    // Check for edges connecting tools to orchestrator
    const orchestratorId = orchestratorNode.id;
    const hasToolConnections = workflow.edges?.some(
      edge => edge.target === orchestratorId && edge.targetHandle === 'tool'
    );

    if (!hasToolConnections) {
      throw new Error('Orchestrator must be connected to at least one tool');
    }

    return true;
  }
}
