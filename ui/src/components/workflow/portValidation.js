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

function findSlot(nodeTypes, node, slotId) {
  const def = nodeTypes[node?.type];
  if (!def) return null;
  return (def.slots || []).find(s => s.id === slotId) || null;
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
    for (const s of (def.slots || [])) {
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
