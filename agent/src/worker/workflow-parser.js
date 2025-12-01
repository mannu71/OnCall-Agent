import fs from 'fs';
import logger from '../shared/logger.js';

/**
 * Parse workflow from workflows.json and build execution graph
 */
export class WorkflowParser {
    constructor(workflowsFilePath) {
        this.workflowsFilePath = workflowsFilePath;
    }

    /**
     * Load workflow by ID or name
     */
    loadWorkflow(workflowIdOrName) {
        try {
            const data = fs.readFileSync(this.workflowsFilePath, 'utf8');
            const workflows = JSON.parse(data);

            const workflow = workflows.find(
                w => w.id === workflowIdOrName || w.name === workflowIdOrName
            );

            if (!workflow) {
                throw new Error(`Workflow not found: ${workflowIdOrName}`);
            }

            logger.info(`Loaded workflow: ${workflow.name} (${workflow.id})`);
            return workflow;
        } catch (error) {
            logger.error(`Failed to load workflow: ${error.message}`);
            throw error;
        }
    }

    /**
     * Parse workflow and extract execution graph
     */
    parseWorkflow(workflow) {
        const { nodes, edges } = workflow;

        // Find orchestrator node
        const orchestratorNode = nodes.find(n => n.type === 'orchestrator');
        if (!orchestratorNode) {
            throw new Error('No orchestrator node found in workflow');
        }

        // Find connected tool nodes (MCP servers)
        const toolNodes = this.getConnectedTools(orchestratorNode.id, nodes, edges);

        // Find output node
        const outputNode = this.getConnectedOutput(orchestratorNode.id, nodes, edges);

        const executionGraph = {
            orchestrator: orchestratorNode,
            tools: toolNodes,
            output: outputNode,
            edges: edges
        };

        logger.info(`Parsed workflow graph: ${toolNodes.length} tools, output: ${outputNode ? 'yes' : 'no'}`);
        return executionGraph;
    }

    /**
     * Get all tool nodes connected to orchestrator
     */
    getConnectedTools(orchestratorId, nodes, edges) {
        // Find edges where target is orchestrator and targetHandle is 'tool'
        const toolEdges = edges.filter(
            e => e.target === orchestratorId && e.targetHandle === 'tool'
        );

        // Get the source nodes (tools)
        const toolNodes = toolEdges
            .map(edge => nodes.find(n => n.id === edge.source))
            .filter(node => node && node.type === 'tool');

        return toolNodes;
    }

    /**
     * Get output node connected to orchestrator
     */
    getConnectedOutput(orchestratorId, nodes, edges) {
        // Find edge where source is orchestrator and sourceHandle is 'orchestrator-output'
        const outputEdge = edges.find(
            e => e.source === orchestratorId && e.sourceHandle === 'orchestrator-output'
        );

        if (!outputEdge) {
            return null;
        }

        // Get the target node (output)
        const outputNode = nodes.find(n => n.id === outputEdge.target);
        return outputNode && outputNode.type === 'output' ? outputNode : null;
    }

    /**
     * Validate workflow structure
     */
    validateWorkflow(executionGraph) {
        const errors = [];

        if (!executionGraph.orchestrator) {
            errors.push('No orchestrator node found');
        }

        if (executionGraph.tools.length === 0) {
            errors.push('No tools connected to orchestrator');
        }

        // Check if orchestrator has workflow definition
        const orchestratorData = executionGraph.orchestrator?.data;
        if (!orchestratorData?.fileName && !orchestratorData?.workflowDefinition) {
            errors.push('Orchestrator has no workflow definition (no file uploaded)');
        }

        if (errors.length > 0) {
            logger.error(`Workflow validation failed: ${errors.join(', ')}`);
            throw new Error(`Invalid workflow: ${errors.join(', ')}`);
        }

        logger.info('Workflow validation passed');
        return true;
    }
}
