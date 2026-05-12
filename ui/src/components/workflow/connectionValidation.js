// ----------------------------------------------------
// CONNECTION MAP - Single source of truth for all node connections
// ----------------------------------------------------

export const CONNECTION_MAP = {
  // Core nodes
  agent: {
    outputs: ["gmail", "teams", "chat", "output", "orchestrator"],
    inputs: {
      input: ["teams", "chat", "scheduler"],
      model: ["llm"],
      memory: ["memory"],
      tool: ["tool", "database", "cloudwatchAnalyzer", "codeAnalyzer"]
    },
    maxInputs: { model: 1, memory: 1 }
  },

  // Input nodes (single output)
  llm: { outputs: ["agent"], maxOutputs: 1 },
  memory: { outputs: ["agent"], maxOutputs: 1 },
  tool: { outputs: ["agent", "database", "orchestrator"], maxOutputs: 1 },
  database: { outputs: ["tool", "agent"], maxOutputs: 1 },

  // Output nodes
  gmail: { outputs: ["agent"] },
  teams: { outputs: ["agent"] },
  chat: { outputs: ["agent"] },
  output: { inputs: ["agent", "orchestrator"] },

  // Scheduler
  scheduler: { outputs: ["agent", "orchestrator"] },

  // Orchestrator
  orchestrator: {
    outputs: ["agent", "output", "orchestrator"],
    inputs: {
      input: ["scheduler"],
      tool: ["tool"]
    }
  },

  // CloudWatch Analyzer (behaves like a tool)
  cloudwatchAnalyzer: { outputs: ["agent"], maxOutputs: 1 },

  // Code Analyzer (behaves like a tool)
  codeAnalyzer: { outputs: ["agent"], maxOutputs: 1 }
};

// Error messages
export const ERROR_MESSAGES = {
  maxOutputs: "This node can only have one outgoing connection.",
  invalidTarget: "This connection is not allowed.",
  invalidSource: "This node cannot accept connections from this source.",
  agentOutput: "Agent output can only connect to Gmail, Teams, Chat, Output or Orchestrator.",
  orchestratorInput: "Orchestrator can only accept Tool or Scheduler connections."
};


// ----------------------------------------------------
// VALIDATION
// ----------------------------------------------------

export const isValidConnection = (source, target, sourceHandle, targetHandle, nodes, edges) => {
  const sourceNode = nodes.find(n => n.id === source);
  const targetNode = nodes.find(n => n.id === target);

  if (!sourceNode || !targetNode) return false;

  const srcConfig = CONNECTION_MAP[sourceNode.type];
  const tgtConfig = CONNECTION_MAP[targetNode.type];

  if (!srcConfig || !tgtConfig) return false;

  // Check if source can output to target type
  if (srcConfig.outputs && !srcConfig.outputs.includes(targetNode.type)) {
    return false;
  }

  // Check if target can accept from source type
  if (tgtConfig.inputs) {
    // Handle-specific input check
    if (targetHandle && tgtConfig.inputs[targetHandle]) {
      if (!tgtConfig.inputs[targetHandle].includes(sourceNode.type)) {
        return false;
      }
    }
    // General inputs check (flatten all inputs)
    const allInputs = Object.values(tgtConfig.inputs).flat();
    if (!allInputs.includes(sourceNode.type)) {
      return false;
    }
  }

  // Check max outputs constraint
  if (srcConfig.maxOutputs) {
    const existingOutputs = edges.filter(e => e.source === source).length;
    if (existingOutputs >= srcConfig.maxOutputs) {
      return false;
    }
  }

  return true;
};


// ----------------------------------------------------
// CONNECTION MESSAGE
// ----------------------------------------------------

export const getConnectionMessage = (source, target, sourceHandle, targetHandle, nodes, edges) => {
  const sourceNode = nodes.find(n => n.id === source);
  const targetNode = nodes.find(n => n.id === target);

  if (!sourceNode || !targetNode) return "Invalid connection";

  const srcConfig = CONNECTION_MAP[sourceNode.type];

  // Check max outputs
  if (srcConfig?.maxOutputs) {
    const existingOutputs = edges.filter(e => e.source === source).length;
    if (existingOutputs >= srcConfig.maxOutputs) {
      return ERROR_MESSAGES.maxOutputs;
    }
  }

  // Check valid output
  if (srcConfig?.outputs && !srcConfig.outputs.includes(targetNode.type)) {
    if (sourceNode.type === "agent") {
      return ERROR_MESSAGES.agentOutput;
    }
    return ERROR_MESSAGES.invalidTarget;
  }

  return "Connection allowed";
};


// ----------------------------------------------------
// NODE CATEGORIES (for sidebar UI)
// ----------------------------------------------------

export const nodeCategories = {
  core: ["agent"],
  ai: ["llm"],
  data: ["database"],
  communication: ["teams", "chat", "output"],
  tools: ["cloudwatchAnalyzer", "codeAnalyzer"],
  memory: ["memory"],
  workflow: ["orchestrator"],
  scheduling: ["scheduler"]
};

export const getNodeCategory = (nodeType) => {
  for (const [category, types] of Object.entries(nodeCategories)) {
    if (types.includes(nodeType)) return category;
  }
  return "unknown";
};
