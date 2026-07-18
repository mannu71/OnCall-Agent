/**
 * Port connection validation for LangflowEditor.
 *
 * Pure module — no React, no DOM. Takes node + edge state and decides
 * whether a proposed edge is allowed.
 *
 * Rules enforced:
 *   1. Both source and target node/slot must exist.
 *   2. No self-connections (same node).
 *   3. Source must be a `port-out`; target must be a `port-in`.
 *   4. Source and target port `portType` must match.
 *   5. Target slot cardinality: `multi: true` accepts unlimited, otherwise
 *      exactly one incoming edge per slot.
 *   6. No duplicate edge between the same source slot and target slot.
 */

export const REJECT = {
  MISSING:    'Node or slot not found.',
  SELF:       'Cannot connect a node to itself.',
  DIRECTION:  'Connect an output port to an input port.',
  PORT_TYPE:  'Port types do not match.',
  SLOT_FULL:  'This input already has a connection.',
  DUPLICATE:  'This connection already exists.',
};

/**
 * Names of the MCP servers an MCP node has selected (comma-joined params.servers).
 */
export function mcpServerNamesOf(node) {
  const raw = node?.params?.servers ?? '';
  if (Array.isArray(raw)) return raw.filter(Boolean).map(String);
  return String(raw).split(',').map(s => s.trim()).filter(Boolean);
}

/**
 * Names of the models a Language Model node offers. Supports the multi-select
 * comma-joined `params.llm` ("A,B") and the structured `params.models` list.
 */
export function modelNamesOf(node) {
  const p = node?.params || {};
  if (Array.isArray(p.models) && p.models.length) {
    return p.models
      .map(m => (typeof m === 'string' ? m : m?.name))
      .filter(Boolean)
      .map(String);
  }
  const raw = p.llm ?? '';
  return String(raw).split(',').map(s => s.trim()).filter(Boolean);
}

/**
 * Effective slot list for a node — like `nodeTypes[type].slots`, but for a
 * `language_model` node with 2+ selected models it expands the single `lm`
 * output port into one `lm::<name>` port per model so each can be wired to a
 * different consumer. A single (or zero) model keeps the static `lm` port for
 * backward compatibility with existing workflows/edges.
 *
 * Similarly, an `mcp_server` node with 2+ selected servers expands the single
 * `tool` output into one `mcp::<name>` port per server.
 */
export function slotsForNode(nodeTypes, node) {
  const def = nodeTypes[node?.type];
  if (!def) return [];
  const base = def.slots || [];

  if (node?.type === 'language_model') {
    const names = modelNamesOf(node);
    if (names.length <= 1) return base;
    const out = [];
    for (const s of base) {
      if (s.kind === 'port-out' && s.id === 'lm') {
        for (const name of names) {
          out.push({ kind: 'port-out', id: `lm::${name}`, label: name, portType: 'model' });
        }
      } else {
        out.push(s);
      }
    }
    return out;
  }

  if (node?.type === 'mcp_server') {
    const names = mcpServerNamesOf(node);
    if (names.length <= 1) return base;
    const out = [];
    for (const s of base) {
      if (s.kind === 'port-out' && s.id === 'tool') {
        for (const name of names) {
          out.push({ kind: 'port-out', id: `mcp::${name}`, label: name, portType: 'tool' });
        }
      } else {
        out.push(s);
      }
    }
    return out;
  }

  return base;
}

/**
 * Re-point/prune edges out of a Language Model node after its model selection
 * changes. A Language Model node's output port id depends on its model set: a
 * single model uses the static `lm` port; 2+ models use one `lm::<name>` port
 * each. Without reconciliation, an edge wired to the old port id dangles (its
 * slot no longer exists) and renders misaligned. Mapping:
 *   • → single/none:  `lm` or `lm::<X>` → `lm`
 *   • → multi:        `lm` → `lm::<oldModel>` (if still selected); `lm::<X>`
 *                     kept if X still selected, else the edge is dropped.
 */
export function reconcileModelEdges(edges, nodeId, oldNode, newNode) {
  const oldNames = modelNamesOf(oldNode);
  const newNames = modelNamesOf(newNode);
  const newSet = new Set(newNames);
  const newIsMulti = newNames.length >= 2;

  return edges.flatMap(e => {
    if (e.source !== nodeId) return [e];
    const ss = e.sourceSlot || e.sourceHandle || '';
    const isModelSlot = ss === 'lm' || ss.startsWith('lm::');
    if (!isModelSlot) return [e];

    const model = ss.startsWith('lm::') ? ss.slice(4) : oldNames[0];

    if (!newIsMulti) {
      return [{ ...e, sourceSlot: 'lm', sourceHandle: undefined }];
    }
    if (model && newSet.has(model)) {
      return [{ ...e, sourceSlot: `lm::${model}`, sourceHandle: undefined }];
    }
    return [];
  });
}

/**
 * Re-point/prune edges out of an MCP node after its server selection changes.
 * Mirrors reconcileModelEdges: single server → static `tool` port;
 * 2+ servers → one `mcp::<name>` port each.
 */
export function reconcileMcpEdges(edges, nodeId, oldNode, newNode) {
  const oldNames = mcpServerNamesOf(oldNode);
  const newNames = mcpServerNamesOf(newNode);
  const newSet = new Set(newNames);
  const newIsMulti = newNames.length >= 2;

  return edges.flatMap(e => {
    if (e.source !== nodeId) return [e];
    const ss = e.sourceSlot || e.sourceHandle || '';
    const isMcpSlot = ss === 'tool' || ss.startsWith('mcp::');
    if (!isMcpSlot) return [e];

    const server = ss.startsWith('mcp::') ? ss.slice(5) : oldNames[0];

    if (!newIsMulti) {
      return [{ ...e, sourceSlot: 'tool', sourceHandle: undefined }];
    }
    if (server && newSet.has(server)) {
      return [{ ...e, sourceSlot: `mcp::${server}`, sourceHandle: undefined }];
    }
    return [];
  });
}

/**
 * True when the given agent node has a Memory node wired to its `memory`
 * input port. Mirrors the backend `has_memory_node` graph check.
 */
export function agentHasMemoryNode(agentNode, nodes, edges) {
  if (!agentNode) return false;
  const memoryTypes = new Set(['vector_memory', 'memory']);
  const memoryNodeIds = new Set(
    (nodes || []).filter(n => memoryTypes.has(n?.type)).map(n => n.id)
  );
  if (!memoryNodeIds.size) return false;
  // Undirected: a Memory node connected to this agent in either direction.
  return (edges || []).some(e =>
    (e.source === agentNode.id && memoryNodeIds.has(e.target)) ||
    (e.target === agentNode.id && memoryNodeIds.has(e.source))
  );
}

const AGENT_AUTO_LEARN_KEYS = ['autoLearn', 'auto_learn'];

function agentAutoLearnOn(node) {
  const p = node?.params || {};
  const cfg = node || {};
  for (const k of AGENT_AUTO_LEARN_KEYS) {
    const v = p[k] ?? cfg[k];
    if (v !== undefined && v !== null) {
      return String(v).trim().toLowerCase() === 'true' || v === true;
    }
  }
  return false;
}

const MEMORY_TYPE_KEYS = ['memoryTypes', 'memory_types'];
const VALID_MEMORY_TYPES = new Set(['semantic', 'pinned', 'kb', 'session']);

/**
 * Returns { present, types } for a Memory node's `memoryTypes`. `present` is
 * false when the key is absent (older node → treated as "all types"); a present
 * key that parses to an empty selection is the state the save-time validator
 * rejects. Mirrors the backend `_read_memory_types`.
 */
function readMemoryTypes(node) {
  for (const src of [node?.params || {}, node || {}]) {
    for (const k of MEMORY_TYPE_KEYS) {
      if (k in src) {
        const raw = src[k];
        const parts = (Array.isArray(raw) ? raw : String(raw ?? '').split(','))
          .map(s => String(s).trim().toLowerCase())
          .filter(s => VALID_MEMORY_TYPES.has(s));
        return { present: true, types: new Set(parts) };
      }
    }
  }
  return { present: false, types: new Set(VALID_MEMORY_TYPES) };
}

/**
 * Non-blocking workflow lint. Returns an array of { nodeId, message } warnings.
 * Auto-learn requires a Memory node with the semantic + kb tiers; a Memory node
 * with every type unticked is un-usable. These mirror the save-time 400s so the
 * user sees them before hitting save.
 */
export function workflowWarnings(nodes, edges) {
  const warnings = [];
  for (const n of nodes || []) {
    if (n?.type === 'vector_memory') {
      const { present, types } = readMemoryTypes(n);
      if (present && types.size === 0) {
        warnings.push({
          nodeId: n.id,
          message: 'Memory node: select at least one memory type (semantic, pinned, kb, or session).',
        });
      }
    }
    if (n?.type === 'agent' && agentAutoLearnOn(n)) {
      if (!agentHasMemoryNode(n, nodes, edges)) {
        warnings.push({
          nodeId: n.id,
          message: 'Auto-learn requires a Memory node — connect one to this agent so learned findings persist and are recalled.',
        });
      }
    }
  }
  return warnings;
}

function findSlot(nodeTypes, node, slotId) {
  if (!node) return null;
  return slotsForNode(nodeTypes, node).find(s => s.id === slotId) || null;
}

/**
 * @returns {{ ok: true } | { ok: false, reason: string }}
 */
export function validateConnection({
  sourceNodeId, sourceSlotId, targetNodeId, targetSlotId,
  nodes, edges, nodeTypes,
}) {
  if (sourceNodeId === targetNodeId) return { ok: false, reason: REJECT.SELF };

  const srcNode = nodes.find(n => n.id === sourceNodeId);
  const tgtNode = nodes.find(n => n.id === targetNodeId);
  if (!srcNode || !tgtNode) return { ok: false, reason: REJECT.MISSING };

  const srcSlot = findSlot(nodeTypes, srcNode, sourceSlotId);
  const tgtSlot = findSlot(nodeTypes, tgtNode, targetSlotId);
  if (!srcSlot || !tgtSlot) return { ok: false, reason: REJECT.MISSING };

  if (srcSlot.kind !== 'port-out' || tgtSlot.kind !== 'port-in') {
    return { ok: false, reason: REJECT.DIRECTION };
  }

  const srcType = srcSlot.portType || 'message';
  const tgtType = tgtSlot.portType || 'message';
  const isCompatible = srcType === tgtType || (srcType === 'data' && tgtType === 'message');
  if (!isCompatible) return { ok: false, reason: REJECT.PORT_TYPE };

  const incomingToSlot = edges.filter(
    e => e.target === targetNodeId && e.targetSlot === targetSlotId
  );

  if (incomingToSlot.some(
    e => e.source === sourceNodeId && e.sourceSlot === sourceSlotId
  )) {
    return { ok: false, reason: REJECT.DUPLICATE };
  }

  if (!tgtSlot.multi && incomingToSlot.length > 0) {
    return { ok: false, reason: REJECT.SLOT_FULL };
  }

  return { ok: true };
}

/**
 * Returns a Set of "<nodeId>|<slotId>" strings for every port-in that would
 * accept a new edge from (sourceNodeId, sourceSlotId). Used to light up valid
 * drop targets while a port-out is being dragged.
 */
export function getValidDropTargets({
  sourceNodeId, sourceSlotId, nodes, edges, nodeTypes,
}) {
  const valid = new Set();
  for (const n of nodes) {
    const def = nodeTypes[n.type];
    if (!def) continue;
    for (const s of slotsForNode(nodeTypes, n)) {
      if (s.kind !== 'port-in') continue;
      const res = validateConnection({
        sourceNodeId, sourceSlotId,
        targetNodeId: n.id, targetSlotId: s.id,
        nodes, edges, nodeTypes,
      });
      if (res.ok) valid.add(`${n.id}|${s.id}`);
    }
  }
  return valid;
}
