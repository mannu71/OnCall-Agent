/**
 * MCP Client Manager
 * 
 * Shared service for managing MCP client connections across all strategies.
 * This replaces the duplicated MCP client logic in both ReAct and Orchestrator.
 */

import { MultiServerMCPClient } from '../../agents/multiserver-mcp-client.js';
import { buildMCPConfigFromJSON } from '../../agents/build-mcp-config.js';
import logger from '../../shared/logger.js';

export class MCPClientManager {
  constructor(options = {}) {
    this.clients = new Map(); // workflowId -> client
    this.defaultTimeout = options.defaultTimeout || 900_000; // 15 minutes
    this.defaultRetries = options.defaultRetries || 2;
  }

  /**
   * Get or create MCP client for a workflow
   * 
   * @param {Object} workflow - The workflow definition
   * @returns {Promise<Object>} MCP client instance
   */
  async getClient(workflow) {
    const workflowId = workflow.id;

    // Return existing client if available
    if (this.clients.has(workflowId)) {
      logger.debug('Reusing existing MCP client', { workflowId });
      return this.clients.get(workflowId);
    }

    // Create new client
    logger.info('Creating new MCP client', { workflowId });
    
    const mcpConfig = buildMCPConfigFromJSON(workflow);
    const client = new MultiServerMCPClient(mcpConfig, {
      defaultTimeout: this.defaultTimeout,
      defaultRetries: this.defaultRetries
    });

    await client.connectAll();
    
    this.clients.set(workflowId, client);
    
    return client;
  }

  /**
   * Disconnect a specific workflow's MCP client
   * 
   * @param {string} workflowId - The workflow ID
   */
  async disconnect(workflowId) {
    const client = this.clients.get(workflowId);
    
    if (client) {
      logger.info('Disconnecting MCP client', { workflowId });
      await client.disconnectAll();
      this.clients.delete(workflowId);
    }
  }

  /**
   * Disconnect all MCP clients
   */
  async disconnectAll() {
    logger.info('Disconnecting all MCP clients', {
      count: this.clients.size
    });

    const disconnectPromises = Array.from(this.clients.entries()).map(
      async ([workflowId, client]) => {
        try {
          await client.disconnectAll();
        } catch (error) {
          logger.error('Error disconnecting MCP client', {
            workflowId,
            error: error.message
          });
        }
      }
    );

    await Promise.all(disconnectPromises);
    this.clients.clear();
  }

  /**
   * Get statistics about active connections
   * 
   * @returns {Object} Connection statistics
   */
  getStats() {
    return {
      activeClients: this.clients.size,
      workflowIds: Array.from(this.clients.keys())
    };
  }
}
