/**
 * Base Strategy Interface
 * 
 * All workflow execution strategies must implement this interface.
 * This ensures consistent behavior across different execution types.
 */

export class BaseStrategy {
  /**
   * Check if this strategy can handle the given workflow
   * 
   * @param {Object} workflow - The workflow definition
   * @returns {boolean} True if this strategy can handle the workflow
   */
  canHandle(workflow) {
    throw new Error('canHandle() must be implemented by strategy');
  }

  /**
   * Execute the workflow
   * 
   * @param {Object} workflow - The workflow definition
   * @param {Object} context - Execution context with shared services
   * @returns {Promise<Object>} Execution result
   */
  async execute(workflow, context) {
    throw new Error('execute() must be implemented by strategy');
  }

  /**
   * Get strategy name for logging
   * 
   * @returns {string} Strategy name
   */
  getName() {
    return this.constructor.name;
  }

  /**
   * Validate workflow for this specific strategy
   * 
   * @param {Object} workflow - The workflow definition
   * @throws {Error} If workflow is invalid for this strategy
   */
  validateWorkflow(workflow) {
    // Override in subclasses for strategy-specific validation
    return true;
  }
}
