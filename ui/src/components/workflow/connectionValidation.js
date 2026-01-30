// ----------------------------------------------------
// 1️⃣ MAPPER DEFINITIONS
// ----------------------------------------------------

export const CONNECTION_MAP = {
  agent: {
    sourceHandles: {
      "agent-output": ["gmail", "teams", "chat", "output", "orchestrator"]
    },
    targetHandles: {
      "agent-input": ["teams", "chat", "scheduler"],
      "model": ["llm"],
      "memory": ["memory"],
      "tool": ["tool", "database"]
    },
    constraints: { model: 1, memory: 1 }
  },

  llm: {
    allowedTargets: ["agent"],
    maxOutputs: 1
  },

  memory: {
    allowedTargets: ["agent"],
    maxOutputs: 1
  },

  tool: {
    allowedTargets: ["agent", "database", "orchestrator"],
    maxOutputs: 1
  },

  database: {
    allowedTargets: ["tool", "agent"],
    maxOutputs: 1
  },

  gmail: { allowedTargets: ["agent"] },
  teams: { allowedTargets: ["agent"] },
  chat: { allowedTargets: ["agent"] },

  scheduler: {
    allowedTargets: ["agent", "orchestrator"],
    sourceHandles: {
      "scheduler-output": ["agent", "orchestrator"]
    }
  },

  // ⭐ Updated Orchestrator rules — TOOL and SCHEDULER INPUT
  orchestrator: {
    sourceHandles: {
      "orchestrator-output": ["agent", "output", "orchestrator"]
    },
    targetHandles: {
      "orchestrator-input": ["scheduler"],               // left handle for scheduler
      "tool": ["tool"]                                   // bottom handle for tool
    },
    allowedSources: ["tool", "scheduler"]                // Tool or Scheduler allowed to connect INTO orchestrator
  },

  output: {
    allowedSources: ["agent", "orchestrator"]
  }
};

// Standard error messages
export const ERROR_MESSAGES = {
  "llm-multiple": "LLM nodes can only have one outgoing connection.",
  "tool-multiple": "Tool nodes can only have one outgoing connection.",
  "database-multiple": "Database nodes can only have one outgoing connection.",
  "memory-multiple": "Memory nodes can only have one outgoing connection.",
  "output-invalid":
    "Output Display can only accept connections from Agent or Orchestrator output.",
  "agent-output-invalid":
    "Agent output can only connect to Gmail, Teams, Chat, Output or Orchestrator.",
  "orchestrator-input-invalid":
    "Orchestrator can only accept Tool (bottom) or Scheduler (left) connections.",
  "orchestrator-scheduler-handle":
    "Scheduler must connect to the left input handle of the Orchestrator.",
  "orchestrator-tool-handle":
    "Tool must connect to the bottom 'Tool' handle of the Orchestrator."
};


// ----------------------------------------------------
// 2️⃣ VALIDATION – CLEAN LOGIC
// ----------------------------------------------------

export const isValidConnection = (
  source,
  target,
  sourceHandle,
  targetHandle,
  nodes,
  edges
) => {
  const sourceNode = nodes.find((n) => n.id === source);
  const targetNode = nodes.find((n) => n.id === target);

  if (!sourceNode || !targetNode) return false;

  const src = CONNECTION_MAP[sourceNode.type] || {};
  const tgt = CONNECTION_MAP[targetNode.type] || {};

  // 🔹 Orchestrator incoming rule — Tool or Scheduler allowed
  if (targetNode.type === "orchestrator") {
    // Scheduler must connect to left input handle
    if (sourceNode.type === "scheduler") {
      return targetHandle === "orchestrator-input";
    }
    // Tool must connect to bottom tool handle
    if (sourceNode.type === "tool") {
      return targetHandle === "tool";
    }
    return false;
  }

  // 🔹 Orchestrator outgoing rule
  if (sourceNode.type === "orchestrator") {
    return CONNECTION_MAP.orchestrator.sourceHandles[sourceHandle]?.includes(targetNode.type) || false;
  }

  // 🔹 Agent handle-specific rules
  if (src.sourceHandles?.[sourceHandle]) {
    return src.sourceHandles[sourceHandle].includes(targetNode.type);
  }

  if (tgt.targetHandles?.[targetHandle]) {
    return tgt.targetHandles[targetHandle].includes(sourceNode.type);
  }

  // 🔹 Basic allowedTargets rule
  if (src.allowedTargets && !src.allowedTargets.includes(targetNode.type)) {
    return false;
  }

  // 🔹 Basic allowedSources rule
  if (tgt.allowedSources && !tgt.allowedSources.includes(sourceNode.type)) {
    return false;
  }

  // 🔹 One-output constraints
  if (src.maxOutputs !== undefined) {
    const outCount = edges.filter((e) => e.source === source).length;
    if (outCount > 0) return false;
  }

  return true;
};


// ----------------------------------------------------
// 3️⃣ EXPLANATION MESSAGES
// ----------------------------------------------------

export const getConnectionMessage = (
  source,
  target,
  sourceHandle,
  targetHandle,
  nodes,
  edges
) => {
  const sourceNode = nodes.find((n) => n.id === source);
  const targetNode = nodes.find((n) => n.id === target);
  if (!sourceNode || !targetNode) return "Invalid connection";

  const srcType = sourceNode.type;

  // 🔹 Orchestrator message rule
  if (targetNode.type === "orchestrator") {
    if (sourceNode.type !== "tool" && sourceNode.type !== "scheduler") {
      return ERROR_MESSAGES["orchestrator-input-invalid"];
    }
    if (sourceNode.type === "scheduler" && targetHandle !== "orchestrator-input") {
      return ERROR_MESSAGES["orchestrator-scheduler-handle"];
    }
    if (sourceNode.type === "tool" && targetHandle !== "tool") {
      return ERROR_MESSAGES["orchestrator-tool-handle"];
    }
  }

  // 🔹 One-output types
  if (["llm", "tool", "database", "memory"].includes(srcType)) {
    const outCount = edges.filter((e) => e.source === source).length;
    if (outCount > 0) {
      return ERROR_MESSAGES[`${srcType}-multiple`];
    }
  }

  // 🔹 Output node rule
  if (targetNode.type === "output" && !["agent", "orchestrator"].includes(sourceNode.type)) {
    return ERROR_MESSAGES["output-invalid"];
  }

  // 🔹 Agent output rule
  if (srcType === "agent" && sourceHandle === "agent-output") {
    if (!["gmail", "teams", "chat", "output", "orchestrator"].includes(targetNode.type)) {
      return ERROR_MESSAGES["agent-output-invalid"];
    }
  }

  return "Connection allowed";
};


// ----------------------------------------------------
// 4️⃣ NODE GROUPS (for sidebar UI)
// ----------------------------------------------------

export const nodeCategories = {
  core: ["agent"],
  ai: ["llm"],
  data: ["database"],
  communication: ["teams", "chat", "output"],
  tools: ["tool"],
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
