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
 */
export function slotsForNode(nodeTypes, node) {
  const def = nodeTypes[node?.type];
  if (!def) return [];
  const base = def.slots || [];
  if (node?.type !== 'language_model') return base;

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

    // Which model did this edge point at?
    const model = ss.startsWith('lm::') ? ss.slice(4) : oldNames[0];

    if (!newIsMulti) {
      // 0 or 1 model → the port collapses to the static `lm`.
      return [{ ...e, sourceSlot: 'lm', sourceHandle: undefined }];
    }
    if (model && newSet.has(model)) {
      return [{ ...e, sourceSlot: `lm::${model}`, sourceHandle: undefined }];
    }
    // The model this edge referenced was deselected → drop the orphaned edge.
    return [];
  });
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
