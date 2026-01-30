/**
 * MCP Client Manager
 * 
 * Shared service for managing MCP client connections across all strategies.
 * This replaces the duplicated MCP client logic in both ReAct and Orchestrator.
 */

import { MultiServerMCPClient } from '../../agents/multiserver-mcp-client.js';
import { buildMCPConfigFromJSON } from '../../agents/build-mcp-config.js';
import { createMCPClient } from '../../worker/mcp-client.js';
import logger from '../../shared/logger.js';

export class MCPClientManager {
  constructor(options = {}) {
    this.clients = new Map(); // workflowId -> client
    this.nodeClients = new Map(); // nodeId -> individual client (for orchestrator)
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
   * Connect to multiple MCP servers (for orchestrator workflows)
   * This method is used by orchestrator-executor.js
   * 
   * @param {Array} toolNodes - Array of tool nodes from workflow
   * @returns {Promise<Array>} Array of connection results
   */
  async connectAll(toolNodes = []) {
    const promises = toolNodes.map(async (node) => {
      const client = createMCPClient(node.data);
      try {
        await client.connect();
        this.nodeClients.set(node.id, client);
        return { id: node.id, success: true };
      } catch (err) {
        logger.error(`Failed to connect to ${node.data?.label || node.id}:`, err);
        return { id: node.id, success: false, error: err };
      }
    });

    const results = await Promise.all(promises);
    const successCount = results.filter(r => r.success).length;
    logger.info(`Connected to ${successCount}/${toolNodes.length} MCP servers`);

    if (successCount === 0) {
      throw new Error('Failed to connect to any MCP servers');
    }

    return results;
  }

  /**
   * Get a specific node client (for orchestrator workflows)
   * 
   * @param {string} nodeId - The node ID
   * @returns {Object} MCP client instance
   */
  getNodeClient(nodeId) {
    return this.nodeClients.get(nodeId);
  }

  /**
   * Disconnect all MCP clients
   */
  async disconnectAll() {
    logger.info('Disconnecting all MCP clients', {
      workflowClients: this.clients.size,
      nodeClients: this.nodeClients.size
    });

    // Disconnect workflow clients (MultiServerMCPClient)
    const workflowDisconnectPromises = Array.from(this.clients.entries()).map(
      async ([workflowId, client]) => {
        try {
          await client.disconnectAll();
        } catch (error) {
          logger.error('Error disconnecting workflow MCP client', {
            workflowId,
            error: error.message
          });
        }
      }
    );

    // Disconnect node clients (individual MCP clients)
    const nodeDisconnectPromises = Array.from(this.nodeClients.entries()).map(
      async ([nodeId, client]) => {
        try {
          await client.disconnect();
        } catch (error) {
          logger.error('Error disconnecting node MCP client', {
            nodeId,
            error: error.message
          });
        }
      }
    );

    await Promise.all([...workflowDisconnectPromises, ...nodeDisconnectPromises]);
    this.clients.clear();
    this.nodeClients.clear();
  }

  /**
   * Get statistics about active connections
   * 
   * @returns {Object} Connection statistics
   */
  getStats() {
    return {
      activeWorkflowClients: this.clients.size,
      activeNodeClients: this.nodeClients.size,
      workflowIds: Array.from(this.clients.keys()),
      nodeIds: Array.from(this.nodeClients.keys())
    };
  }
}
