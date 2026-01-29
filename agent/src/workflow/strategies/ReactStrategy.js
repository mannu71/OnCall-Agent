/**
 * ReAct Strategy
 * 
 * Executes AI-driven agentic workflows using LangChain and the ReAct pattern.
 * This strategy is used for investigative queries where the AI decides which
 * tools to call and in what order.
 * 
 * Use cases:
 * - "Why did profiles fail today?"
 * - "What's causing high CPU usage?"
 * - "Investigate database connection issues"
 */

import { BaseStrategy } from './BaseStrategy.js';
import { buildDynamicWorkflow } from '../../agents/generic-multi-step-workflow.js';

export class ReactStrategy extends BaseStrategy {
  /**
   * Check if workflow is a ReAct agent workflow
   * 
   * A workflow is considered a ReAct workflow if it has:
   * - An 'agent' node (defines agent behavior)
   * - An 'llm' node (provides AI capabilities)
   * 
   * @param {Object} workflow - The workflow definition
   * @returns {boolean} True if this is a ReAct workflow
   */
  canHandle(workflow) {
    if (!workflow.nodes || !Array.isArray(workflow.nodes)) {
      return false;
    }

    const hasAgentNode = workflow.nodes.some(node => node.type === 'agent');
    const hasLLMNode = workflow.nodes.some(node => node.type === 'llm');

    return hasAgentNode && hasLLMNode;
  }

  /**
   * Execute ReAct agent workflow
   * 
   * @param {Object} workflow - The workflow definition
   * @param {Object} context - Execution context
   * @returns {Promise<Object>} Execution result
   */
  async execute(workflow, context) {
    const { logger, userQuery, executionId } = context;

    logger.info('ReactStrategy: Starting execution', {
      executionId,
      workflowId: workflow.id,
      userQuery: userQuery?.substring(0, 100)
    });

    // Validate that we have a user query
    if (!userQuery) {
      throw new Error('ReactStrategy requires a userQuery in the context');
    }

    try {
      // Build the LangChain workflow using existing implementation
      // This reuses the existing ReAct agent logic
      const { workflow: langchainWorkflow, mcpClient } = await buildDynamicWorkflow(
        workflow,
        {
          defaultTimeout: context.timeout,
          defaultRetries: context.retries
        }
      );

      // Execute the workflow with the user query
      const result = await langchainWorkflow.invoke({ userQuery });

      // Clean up MCP connections
      await mcpClient.disconnectAll();

      logger.info('ReactStrategy: Execution completed', {
        executionId,
        messageCount: result.messages?.length || 0
      });

      return {
        type: 'react',
        userQuery,
        finalAnswer: result.finalAnswer,
        messages: result.messages,
        messageCount: result.messages?.length || 0,
        correlationId: result.cid
      };

    } catch (error) {
      logger.error('ReactStrategy: Execution failed', {
        executionId,
        error: error.message
      });
      throw error;
    }
  }

  /**
   * Validate ReAct workflow structure
   * 
   * @param {Object} workflow - The workflow definition
   * @throws {Error} If workflow is invalid
   */
  validateWorkflow(workflow) {
    const agentNode = workflow.nodes.find(n => n.type === 'agent');
    const llmNode = workflow.nodes.find(n => n.type === 'llm');

    if (!agentNode) {
      throw new Error('ReAct workflow must have an agent node');
    }

    if (!llmNode) {
      throw new Error('ReAct workflow must have an LLM node');
    }

    // Validate agent node has instructions
    if (!agentNode.data?.instructions) {
      throw new Error('Agent node must have instructions');
    }

    // Validate LLM node has required config
    if (!llmNode.data?.model) {
      throw new Error('LLM node must specify a model');
    }

    if (!llmNode.data?.provider) {
      throw new Error('LLM node must specify a provider');
    }

    return true;
  }
}
