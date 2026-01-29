/**
 * Unified Workflow Engine
 * 
 * This is the main facade for workflow execution. It provides a single entry point
 * for executing any type of workflow (ReAct Agent or Orchestrator) by delegating
 * to the appropriate strategy.
 * 
 * @example
 * const engine = new WorkflowEngine();
 * const result = await engine.execute(workflow, { userQuery: "Why did profiles fail?" });
 */

import logger from '../../shared/logger.js';
import { ReactStrategy } from '../strategies/ReactStrategy.js';
import { OrchestratorStrategy } from '../strategies/OrchestratorStrategy.js';
import { WorkflowValidator } from '../validators/WorkflowValidator.js';
import { MCPClientManager } from '../shared/MCPClientManager.js';
import { ResultFormatter } from '../shared/ResultFormatter.js';
import { WorkflowExecutionError } from '../../shared/errors.js';

export class WorkflowEngine {
  constructor(options = {}) {
    this.strategies = [
      new ReactStrategy(),
      new OrchestratorStrategy()
    ];
    
    // Shared services that all strategies can use
    this.mcpManager = options.mcpManager || new MCPClientManager();
    this.resultFormatter = options.resultFormatter || new ResultFormatter();
    this.validator = options.validator || new WorkflowValidator();
    
    // Configuration
    this.maxExecutionTime = options.maxExecutionTime || 300_000; // 5 minutes default
    this.enableMetrics = options.enableMetrics !== false;
    
    logger.info('WorkflowEngine initialized', {
      strategies: this.strategies.map(s => s.constructor.name),
      maxExecutionTime: this.maxExecutionTime
    });
  }

  /**
   * Execute a workflow using the appropriate strategy
   * 
   * @param {Object} workflow - The workflow definition (from workflows.json)
   * @param {Object} context - Execution context (userQuery, variables, etc.)
   * @returns {Promise<Object>} Execution result
   */
  async execute(workflow, context = {}) {
    const executionId = context.executionId || this.generateExecutionId();
    const startTime = Date.now();
    
    logger.info('Workflow execution started', {
      executionId,
      workflowId: workflow.id,
      workflowName: workflow.name,
      context: this.sanitizeContext(context)
    });

    try {
      // 1. Validate workflow
      this.validator.validate(workflow);
      
      // 2. Select appropriate strategy
      const strategy = this.selectStrategy(workflow);
      logger.info('Strategy selected', {
        executionId,
        strategy: strategy.constructor.name
      });
      
      // 3. Prepare execution context with shared services
      const executionContext = {
        ...context,
        executionId,
        mcpManager: this.mcpManager,
        resultFormatter: this.resultFormatter,
        logger: logger
      };
      
      // 4. Execute with timeout
      const result = await this.executeWithTimeout(
        strategy,
        workflow,
        executionContext
      );
      
      // 5. Format and return result
      const duration = Date.now() - startTime;
      const formattedResult = {
        success: true,
        executionId,
        workflowId: workflow.id,
        workflowName: workflow.name,
        strategy: strategy.constructor.name,
        duration,
        timestamp: new Date().toISOString(),
        ...result
      };
      
      logger.info('Workflow execution completed', {
        executionId,
        duration,
        success: true
      });
      
      if (this.enableMetrics) {
        this.recordMetrics(workflow, strategy, duration, true);
      }
      
      return formattedResult;
      
    } catch (error) {
      const duration = Date.now() - startTime;
      
      logger.error('Workflow execution failed', {
        executionId,
        workflowId: workflow.id,
        workflowName: workflow.name,
        duration,
        error: error.message,
        stack: error.stack
      });
      
      if (this.enableMetrics) {
        this.recordMetrics(workflow, null, duration, false);
      }
      
      throw new WorkflowExecutionError(
        `Workflow execution failed: ${error.message}`,
        {
          executionId,
          workflowId: workflow.id,
          workflowName: workflow.name,
          originalError: error,
          duration
        }
      );
    }
  }

  /**
   * Select the appropriate strategy for a workflow
   * 
   * @param {Object} workflow - The workflow definition
   * @returns {Object} Strategy instance
   * @throws {Error} If no strategy can handle the workflow
   */
  selectStrategy(workflow) {
    for (const strategy of this.strategies) {
      if (strategy.canHandle(workflow)) {
        logger.debug('Strategy matched', {
          strategy: strategy.constructor.name,
          workflowId: workflow.id
        });
        return strategy;
      }
    }
    
    throw new Error(
      `No strategy found for workflow: ${workflow.name} (${workflow.id}). ` +
      `Available strategies: ${this.strategies.map(s => s.constructor.name).join(', ')}`
    );
  }

  /**
   * Execute strategy with timeout protection
   * 
   * @param {Object} strategy - The strategy to execute
   * @param {Object} workflow - The workflow definition
   * @param {Object} context - Execution context
   * @returns {Promise<Object>} Execution result
   */
  async executeWithTimeout(strategy, workflow, context) {
    return Promise.race([
      strategy.execute(workflow, context),
      new Promise((_, reject) => {
        setTimeout(() => {
          reject(new Error(
            `Workflow execution timeout after ${this.maxExecutionTime}ms`
          ));
        }, this.maxExecutionTime);
      })
    ]);
  }

  /**
   * Validate a workflow without executing it
   * 
   * @param {Object} workflow - The workflow definition
   * @returns {Object} Validation result
   */
  validate(workflow) {
    try {
      this.validator.validate(workflow);
      const strategy = this.selectStrategy(workflow);
      
      return {
        valid: true,
        strategy: strategy.constructor.name,
        workflow: {
          id: workflow.id,
          name: workflow.name,
          nodeCount: workflow.nodes?.length || 0
        }
      };
    } catch (error) {
      return {
        valid: false,
        error: error.message,
        workflow: {
          id: workflow.id,
          name: workflow.name
        }
      };
    }
  }

  /**
   * Get status of a running execution (if tracking is enabled)
   * 
   * @param {string} executionId - The execution ID
   * @returns {Object|null} Execution status or null if not found
   */
  async getStatus(executionId) {
    // TODO: Implement execution tracking
    // This would query the database for execution status
    logger.warn('getStatus not yet implemented', { executionId });
    return null;
  }

  /**
   * Cancel a running execution (if possible)
   * 
   * @param {string} executionId - The execution ID
   * @returns {Promise<boolean>} True if cancelled, false otherwise
   */
  async cancel(executionId) {
    // TODO: Implement execution cancellation
    // This would require tracking running executions and their abort controllers
    logger.warn('cancel not yet implemented', { executionId });
    return false;
  }

  /**
   * Generate a unique execution ID
   * 
   * @returns {string} Execution ID
   */
  generateExecutionId() {
    return `exec-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * Sanitize context for logging (remove sensitive data)
   * 
   * @param {Object} context - The context object
   * @returns {Object} Sanitized context
   */
  sanitizeContext(context) {
    const sanitized = { ...context };
    
    // Remove sensitive fields
    delete sanitized.apiKey;
    delete sanitized.password;
    delete sanitized.token;
    
    // Truncate long values
    if (sanitized.userQuery && sanitized.userQuery.length > 100) {
      sanitized.userQuery = sanitized.userQuery.substring(0, 100) + '...';
    }
    
    return sanitized;
  }

  /**
   * Record metrics for monitoring
   * 
   * @param {Object} workflow - The workflow
   * @param {Object} strategy - The strategy used
   * @param {number} duration - Execution duration in ms
   * @param {boolean} success - Whether execution succeeded
   */
  recordMetrics(workflow, strategy, duration, success) {
    // TODO: Integrate with metrics system (Prometheus, etc.)
    logger.debug('Workflow metrics', {
      workflowId: workflow.id,
      workflowName: workflow.name,
      strategy: strategy?.constructor.name,
      duration,
      success
    });
  }

  /**
   * Clean up resources
   */
  async cleanup() {
    logger.info('WorkflowEngine cleanup started');
    
    try {
      await this.mcpManager.disconnectAll();
      logger.info('WorkflowEngine cleanup completed');
    } catch (error) {
      logger.error('WorkflowEngine cleanup failed', { error: error.message });
    }
  }
}
