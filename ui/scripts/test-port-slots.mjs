/**
 * Standalone, dependency-free regression test for the multi-model canvas
 * logic in src/components/workflow/portValidation.js.
 *
 * No test runner required — uses node:assert and imports the real module.
 * Run from the ui/ directory:  node scripts/test-port-slots.mjs
 * Exits 0 on success; non-zero with a message on failure.
 */
import assert from 'node:assert/strict';
import {
  modelNamesOf,
  slotsForNode,
  validateConnection,
  reconcileModelEdges,
} from '../src/components/workflow/portValidation.js';

// Minimal nodeTypes map mirroring the real slot shapes used on the canvas.
const nodeTypes = {
  language_model: {
    slots: [
      { kind: 'llm-select', id: 'llm' },
      { kind: 'port-out', id: 'lm', portType: 'model' },
    ],
  },
  agent: {
    slots: [{ kind: 'port-in', id: 'lm', portType: 'model' }],
  },
};

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`  ok - ${name}`);
}

// --- Case 1: modelNamesOf ---------------------------------------------------
test('modelNamesOf single via params.llm', () => {
  assert.deepEqual(modelNamesOf({ params: { llm: 'Sonnet' } }), ['Sonnet']);
});
test('modelNamesOf multi via params.llm', () => {
  assert.deepEqual(modelNamesOf({ params: { llm: 'Sonnet,Haiku' } }), ['Sonnet', 'Haiku']);
});
test('modelNamesOf trims whitespace and drops empties', () => {
  assert.deepEqual(modelNamesOf({ params: { llm: 'A , B ' } }), ['A', 'B']);
});
test('modelNamesOf reads structured params.models', () => {
  assert.deepEqual(
    modelNamesOf({ params: { models: [{ name: 'X' }, { name: 'Y' }] } }),
    ['X', 'Y'],
  );
});
test('modelNamesOf empty returns []', () => {
  assert.deepEqual(modelNamesOf({ params: {} }), []);
  assert.deepEqual(modelNamesOf({}), []);
  assert.deepEqual(modelNamesOf(null), []);
});

// --- Case 2: single-model language_model keeps static `lm` port -------------
test('slotsForNode single-model keeps back-compat lm port', () => {
  const node = { type: 'language_model', params: { llm: 'Sonnet' } };
  const outIds = slotsForNode(nodeTypes, node)
    .filter(s => s.kind === 'port-out')
    .map(s => s.id);
  assert.deepEqual(outIds, ['lm']);
});

// --- Case 3: 2-model expands into per-model ports ---------------------------
test('slotsForNode 2-model expands lm into lm::<name> ports', () => {
  const node = { type: 'language_model', params: { llm: 'Sonnet,Haiku' } };
  const outs = slotsForNode(nodeTypes, node).filter(s => s.kind === 'port-out');
  assert.deepEqual(outs.map(s => s.id), ['lm::Sonnet', 'lm::Haiku']);
  assert.deepEqual(outs.map(s => s.label), ['Sonnet', 'Haiku']);
  assert.ok(outs.every(s => s.portType === 'model'));
});

// --- Case 4: non-language_model nodes pass through unchanged -----------------
test('slotsForNode leaves non-language_model slots unchanged', () => {
  const node = { type: 'agent', params: {} };
  const slots = slotsForNode(nodeTypes, node);
  assert.equal(slots.length, nodeTypes.agent.slots.length);
  assert.deepEqual(slots.map(s => s.id), nodeTypes.agent.slots.map(s => s.id));
});

// --- Case 5: connection validation with a 2-model LM node -------------------
test('validateConnection ok from lm::Haiku to agent lm port', () => {
  const nodes = [
    { id: 'lm1', type: 'language_model', params: { llm: 'Sonnet,Haiku' } },
    { id: 'a1', type: 'agent', params: {} },
  ];
  const res = validateConnection({
    sourceNodeId: 'lm1', sourceSlotId: 'lm::Haiku',
    targetNodeId: 'a1', targetSlotId: 'lm',
    nodes, edges: [], nodeTypes,
  });
  assert.deepEqual(res, { ok: true });
});
test('validateConnection fails from plain lm slot on a multi-model node', () => {
  const nodes = [
    { id: 'lm1', type: 'language_model', params: { llm: 'Sonnet,Haiku' } },
    { id: 'a1', type: 'agent', params: {} },
  ];
  const res = validateConnection({
    sourceNodeId: 'lm1', sourceSlotId: 'lm',
    targetNodeId: 'a1', targetSlotId: 'lm',
    nodes, edges: [], nodeTypes,
  });
  assert.equal(res.ok, false);
});

// --- Case 6: reconcileModelEdges keeps wires aligned on model changes -------
const single = { type: 'language_model', params: { llm: 'Sonnet' } };
const dual   = { type: 'language_model', params: { llm: 'Sonnet,Haiku' } };

test('reconcile single→multi remaps lm → lm::<oldModel>', () => {
  const edges = [{ id: 'e1', source: 'lm1', sourceSlot: 'lm', target: 'a1', targetSlot: 'lm' }];
  const out = reconcileModelEdges(edges, 'lm1', single, dual);
  assert.equal(out.length, 1);
  assert.equal(out[0].sourceSlot, 'lm::Sonnet');
});
test('reconcile multi→single collapses lm::<X> → lm', () => {
  const edges = [{ id: 'e1', source: 'lm1', sourceSlot: 'lm::Haiku', target: 'a1', targetSlot: 'lm' }];
  const out = reconcileModelEdges(edges, 'lm1', dual, single);
  assert.equal(out[0].sourceSlot, 'lm');
});
test('reconcile drops edge whose model was deselected', () => {
  const edges = [{ id: 'e1', source: 'lm1', sourceSlot: 'lm::Haiku', target: 'a1', targetSlot: 'lm' }];
  // new selection is Sonnet,Opus — Haiku gone
  const out = reconcileModelEdges(edges, 'lm1', dual, { type: 'language_model', params: { llm: 'Sonnet,Opus' } });
  assert.equal(out.length, 0);
});
test('reconcile keeps still-selected model and leaves other nodes alone', () => {
  const edges = [
    { id: 'e1', source: 'lm1', sourceSlot: 'lm::Sonnet', target: 'a1', targetSlot: 'lm' },
    { id: 'e2', source: 'other', sourceSlot: 'tool', target: 'a1', targetSlot: 'tools' },
  ];
  const out = reconcileModelEdges(edges, 'lm1', dual, dual);
  assert.equal(out.length, 2);
  assert.equal(out.find(e => e.id === 'e1').sourceSlot, 'lm::Sonnet');
  assert.equal(out.find(e => e.id === 'e2').sourceSlot, 'tool'); // untouched
});

console.log(`\nAll ${passed} port-slot tests passed.`);
