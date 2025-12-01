const hasConnectionToType = (nodeId, targetType, nodes, edges) =>
  edges?.some(edge => {
    const sourceNode = nodes.find(n => n.id === edge.source);
    const targetNode = nodes.find(n => n.id === edge.target);
    return (
      (sourceNode?.type === targetType && edge.target === nodeId) ||
      (targetNode?.type === targetType && edge.source === nodeId)
    );
  }) || false;

const isNodeConnected = (nodeId, edges) =>
  edges?.some(edge => edge.source === nodeId || edge.target === nodeId) || false;

const getUnconnectedNodes = (nodes, edges) =>
  nodes?.filter(node => !isNodeConnected(node.id, edges))
    .map(node => node.data?.label || node.type) || [];

const findNodeByType = (nodes, type) =>
  nodes?.find(node => node.type === type);

const agentConnectionRules = [
  { type: 'llm', message: 'Agent workflow must have an LLM node connected to the Agent.' },
  { type: 'tool', message: 'Agent workflow must have at least one Tool connected to the Agent.' }
];

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

  hasAgentNode: (nodes) => ({
    isValid: !!findNodeByType(nodes, 'agent'),
    error: 'Agent workflow must have an Agent AI node.'
  }),

  agentConnections: (nodes, edges) => {
    const agentNode = findNodeByType(nodes, 'agent');
    if (!agentNode) return { isValid: false, error: 'No agent node found.' };
    
    for (const rule of agentConnectionRules) {
      if (!hasConnectionToType(agentNode.id, rule.type, nodes, edges)) {
        return { isValid: false, error: rule.message };
      }
    }
    return { isValid: true, error: null };
  }
};

export const validateWorkflow = (workflowType, nodes, edges) => {
  const minNodesResult = validators.minNodes(nodes);
  if (!minNodesResult.isValid) return minNodesResult;

  const allConnectedResult = validators.allConnected(nodes, edges);
  if (!allConnectedResult.isValid) return allConnectedResult;

  if (workflowType === 'agent') {
    const hasAgentResult = validators.hasAgentNode(nodes);
    if (!hasAgentResult.isValid) return hasAgentResult;

    const connectionsResult = validators.agentConnections(nodes, edges);
    if (!connectionsResult.isValid) return connectionsResult;
  }

  return { isValid: true, error: null };
};

export const isAgentWorkflowValid = (workflow) => {
  const { type, nodes, edges } = workflow;
  
  if (type !== 'agent') return false;
  if (!validators.minNodes(nodes).isValid) return false;
  if (!validators.allConnected(nodes, edges).isValid) return false;
  if (!validators.hasAgentNode(nodes).isValid) return false;
  if (!validators.agentConnections(nodes, edges).isValid) return false;

  return true;
};

export { validators };

export default {
  validateWorkflow,
  isAgentWorkflowValid,
  validators
};
