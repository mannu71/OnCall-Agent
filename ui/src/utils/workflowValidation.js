// ── Node-type awareness ──────────────────────────────────────────────────────
// Workflows come in two schemas: legacy ReactFlow (llm / tool / cloudwatchAnalyzer)
// and the newer LangflowEditor (language_model / cloudwatch_tool / postgres_tool …).
// Treat both consistently so Chat lists agents and the editor validates either.
const LLM_TYPES = ['llm', 'language_model'];
const isLlmType = (t) => LLM_TYPES.includes(t);

// Anything wired into the agent that isn't an LLM, a schedule/trigger, or the
// agent itself is treated as a tool — covers tool, cloudwatch_tool,
// cloudwatchAnalyzer, postgres_tool, codeAnalyzer, database, wiki, *_tool, …
const NON_TOOL_TYPES = new Set([
  'agent', 'llm', 'language_model', 'schedule', 'scheduler', 'trigger', 'memory',
]);
const isToolType = (t) => !!t && !NON_TOOL_TYPES.has(t);

/** Types of nodes directly connected (either direction) to the given node. */
const neighborTypesOf = (nodeId, nodes, edges) =>
  (edges || [])
    .filter(e => e.source === nodeId || e.target === nodeId)
    .map(e => {
      const otherId = e.source === nodeId ? e.target : e.source;
      return nodes?.find(n => n.id === otherId)?.type;
    })
    .filter(Boolean);

const isNodeConnected = (nodeId, edges) =>
  edges?.some(edge => edge.source === nodeId || edge.target === nodeId) || false;

const getUnconnectedNodes = (nodes, edges) =>
  nodes?.filter(node => !isNodeConnected(node.id, edges))
    .map(node => node.data?.label || node.type) || [];

const findNodeByType = (nodes, type) =>
  nodes?.find(node => node.type === type);

// Check if agent has an LLM (llm or language_model) connected.
const hasLlmConnection = (agentNodeId, nodes, edges) =>
  neighborTypesOf(agentNodeId, nodes, edges).some(isLlmType);

// Check if agent has any tool connected (tool / cloudwatch_tool / cloudwatchAnalyzer / …).
const hasToolConnection = (agentNodeId, nodes, edges) =>
  neighborTypesOf(agentNodeId, nodes, edges).some(isToolType);

const validators = {
  minNodes: (nodes) => ({
    isValid: nodes?.length >= 2,
    error: 'Workflow must have at least 2 connected nodes.'
  }),

  allConnected: (nodes, edges) => {
    const unconnected = getUnconnectedNodes(nodes, edges);
    return {
      isValid: unconnected.length === 0,
      error: `All nodes must be connected. Unconnected: ${unconnected.join(', ')}`
    };
  },

  orchestratorHasSql: (nodes) => {
    const orchestratorNodes = nodes?.filter(node => node.type === 'orchestrator') || [];
    const nodesWithoutSql = orchestratorNodes.filter(node => {
      const data = node.data || {};
      return !data.fileName && !data.fileContent;
    });

    if (nodesWithoutSql.length > 0) {
      const labels = nodesWithoutSql.map(n => n.data?.label || 'Orchestrator').join(', ');
      return {
        isValid: false,
        error: `SQL Orchestrator nodes must have an SQL file attached: ${labels}`
      };
    }
    return { isValid: true, error: null };
  },

  hasAgentNode: (nodes) => ({
    isValid: !!findNodeByType(nodes, 'agent'),
    error: 'Agent workflow must have an Agent AI node.'
  }),

  agentConnections: (nodes, edges) => {
    const agentNode = findNodeByType(nodes, 'agent');
    if (!agentNode) return { isValid: false, error: 'No agent node found.' };

    if (!hasLlmConnection(agentNode.id, nodes, edges)) {
      return { isValid: false, error: 'Agent workflow must have an LLM node connected to the Agent.' };
    }
    if (!hasToolConnection(agentNode.id, nodes, edges)) {
      return { isValid: false, error: 'Agent workflow must have at least one Tool connected to the Agent.' };
    }
    return { isValid: true, error: null };
  }
};

export const validateWorkflow = (workflowType, nodes, edges) => {
  const minNodesResult = validators.minNodes(nodes);
  if (!minNodesResult.isValid) return minNodesResult;

  const allConnectedResult = validators.allConnected(nodes, edges);
  if (!allConnectedResult.isValid) return allConnectedResult;

  // Validate orchestrator nodes have SQL files
  const orchestratorSqlResult = validators.orchestratorHasSql(nodes);
  if (!orchestratorSqlResult.isValid) return orchestratorSqlResult;

  if (workflowType === 'agent') {
    const hasAgentResult = validators.hasAgentNode(nodes);
    if (!hasAgentResult.isValid) return hasAgentResult;

    const connectionsResult = validators.agentConnections(nodes, edges);
    if (!connectionsResult.isValid) return connectionsResult;
  }

  return { isValid: true, error: null };
};

/**
 * Is this workflow usable as a chat agent?
 *
 * Agent-centric (NOT keyed on the saved `type`, which is often "workflow"):
 * a workflow is chat-ready when it has an `agent` node connected to an LLM
 * (llm/language_model) AND at least one tool (tool/cloudwatch_tool/…).
 * Standalone schedule/scheduler nodes are ignored — they don't block chat.
 */
export const isAgentWorkflowValid = (workflow) => {
  if (!workflow) return false;
  const nodes = workflow.nodes || [];
  const edges = workflow.edges || [];

  const agentNode = nodes.find(n => n.type === 'agent');
  if (!agentNode) return false;

  return (
    hasLlmConnection(agentNode.id, nodes, edges) &&
    hasToolConnection(agentNode.id, nodes, edges)
  );
};

export { validators, isLlmType, isToolType };

export default {
  validateWorkflow,
  isAgentWorkflowValid,
  validators
};
