/**
 * Workflow Validator
 * 
 * Validates workflow structure before execution to catch errors early.
 */

export class WorkflowValidator {
  /**
   * Validate a workflow definition
   * 
   * @param {Object} workflow - The workflow definition
   * @throws {Error} If workflow is invalid
   */
  validate(workflow) {
    this.validateBasicStructure(workflow);
    this.validateNodes(workflow);
    this.validateEdges(workflow);
    this.validateConnections(workflow);
  }

  /**
   * Validate basic workflow structure
   */
  validateBasicStructure(workflow) {
    if (!workflow) {
      throw new Error('Workflow is required');
    }

    if (!workflow.id) {
      throw new Error('Workflow must have an id');
    }

    if (!workflow.name) {
      throw new Error('Workflow must have a name');
    }

    if (!workflow.nodes || !Array.isArray(workflow.nodes)) {
      throw new Error('Workflow must have a nodes array');
    }

    if (workflow.nodes.length === 0) {
      throw new Error('Workflow must have at least one node');
    }

    if (!workflow.edges || !Array.isArray(workflow.edges)) {
      throw new Error('Workflow must have an edges array');
    }
  }

  /**
   * Validate nodes
   */
  validateNodes(workflow) {
    const nodeIds = new Set();

    for (const node of workflow.nodes) {
      // Check for duplicate IDs
      if (nodeIds.has(node.id)) {
        throw new Error(`Duplicate node ID: ${node.id}`);
      }
      nodeIds.add(node.id);

      // Validate node structure
      if (!node.type) {
        throw new Error(`Node ${node.id} must have a type`);
      }

      if (!node.data) {
        throw new Error(`Node ${node.id} must have data`);
      }

      // Validate specific node types
      this.validateNodeType(node);
    }
  }

  /**
   * Validate specific node types
   */
  validateNodeType(node) {
    switch (node.type) {
      case 'tool':
        if (!node.data.label) {
          throw new Error(`Tool node ${node.id} must have a label`);
        }
        break;

      case 'orchestrator':
        if (!node.data.workflowDefinition && !node.data.sqlFile && !node.data.fileName) {
          throw new Error(
            `Orchestrator node ${node.id} must have workflowDefinition, sqlFile, or fileName`
          );
        }
        break;

      case 'agent':
        if (!node.data.instructions) {
          throw new Error(`Agent node ${node.id} must have instructions`);
        }
        break;

      case 'llm':
        if (!node.data.model) {
          throw new Error(`LLM node ${node.id} must have a model`);
        }
        if (!node.data.provider) {
          throw new Error(`LLM node ${node.id} must have a provider`);
        }
        break;

      case 'output':
        // Output nodes don't require specific validation
        break;

      default:
        // Allow unknown node types (for extensibility)
        break;
    }
  }

  /**
   * Validate edges
   */
  validateEdges(workflow) {
    const nodeIds = new Set(workflow.nodes.map(n => n.id));

    for (const edge of workflow.edges) {
      if (!edge.id) {
        throw new Error('Edge must have an id');
      }

      if (!edge.source) {
        throw new Error(`Edge ${edge.id} must have a source`);
      }

      if (!edge.target) {
        throw new Error(`Edge ${edge.id} must have a target`);
      }

      // Validate that source and target nodes exist
      if (!nodeIds.has(edge.source)) {
        throw new Error(`Edge ${edge.id} references non-existent source node: ${edge.source}`);
      }

      if (!nodeIds.has(edge.target)) {
        throw new Error(`Edge ${edge.id} references non-existent target node: ${edge.target}`);
      }
    }
  }

  /**
   * Validate node connections
   */
  validateConnections(workflow) {
    // Check for orphaned nodes (nodes with no connections)
    const connectedNodes = new Set();
    
    for (const edge of workflow.edges) {
      connectedNodes.add(edge.source);
      connectedNodes.add(edge.target);
    }

    // Output nodes can be orphaned (they're endpoints)
    const orphanedNodes = workflow.nodes.filter(
      node => !connectedNodes.has(node.id) && node.type !== 'output'
    );

    if (orphanedNodes.length > 0) {
      const orphanedIds = orphanedNodes.map(n => n.id).join(', ');
      throw new Error(`Orphaned nodes detected (not connected): ${orphanedIds}`);
    }

    // Check for cycles (simple check - can be enhanced)
    this.checkForCycles(workflow);
  }

  /**
   * Check for cycles in the workflow graph
   * 
   * Uses depth-first search to detect cycles
   */
  checkForCycles(workflow) {
    const adjacencyList = new Map();
    
    // Build adjacency list
    for (const node of workflow.nodes) {
      adjacencyList.set(node.id, []);
    }
    
    for (const edge of workflow.edges) {
      adjacencyList.get(edge.source).push(edge.target);
    }

    const visited = new Set();
    const recursionStack = new Set();

    const hasCycle = (nodeId) => {
      visited.add(nodeId);
      recursionStack.add(nodeId);

      const neighbors = adjacencyList.get(nodeId) || [];
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor)) {
          if (hasCycle(neighbor)) {
            return true;
          }
        } else if (recursionStack.has(neighbor)) {
          return true;
        }
      }

      recursionStack.delete(nodeId);
      return false;
    };

    for (const nodeId of adjacencyList.keys()) {
      if (!visited.has(nodeId)) {
        if (hasCycle(nodeId)) {
          throw new Error('Workflow contains a cycle (circular dependency)');
        }
      }
    }
  }
}
