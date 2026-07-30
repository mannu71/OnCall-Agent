// Single source of colour truth for the galaxy view.
//
// Three orthogonal axes, one shared vocabulary:
//
//   1. FOLDER_HUES  — "where in the codebase". Primary identity. Drives the
//      arm-segment gas, the legend, the sidebar rows, and the star tint.
//   2. the stellar ramp — "how important". Comes from the C engine as
//      node.color (a Hertzsprung-Russell degree ramp); we keep it and tint it
//      toward the folder hue so each arm reads as its own colour family
//      without losing the temperature signal.
//   3. EDGE_FAMILIES — "what kind of relationship". The old per-type map was
//      23 arbitrary Tailwind colours, which is unreadable at 4k edges.

// 12 hues that stay distinguishable against a black background and survive
// additive bloom without collapsing to white.
export const FOLDER_HUES = [
  '#4cc9f0', // cyan
  '#f72585', // magenta
  '#7bf1a8', // mint
  '#ffb703', // amber
  '#b892ff', // violet
  '#ff6b6b', // coral
  '#4ea8de', // azure
  '#ffd166', // gold
  '#06d6a0', // teal
  '#e07be0', // orchid
  '#8ecae6', // sky
  '#ff9f1c', // orange
];

// 5 and 12 are coprime, so stepping by 5 walks all twelve hues while keeping
// consecutively-indexed folders far apart on the wheel.
export function folderHue(index) {
  return FOLDER_HUES[(index * 5) % FOLDER_HUES.length];
}

// Warm core / cool halo biases, applied on top of the stellar ramp.
export const BULGE_TINT = '#ffd9a0';
export const HALO_TINT = '#bcd0ff';
export const HII_COLOR = '#ff6b9d'; // H-alpha emission
export const DUST_COLOR = '#3a2418';

// --- Edge families -------------------------------------------------------
//
// Type names below cover both what the codegraph engine emits today
// (DEFINES / CALLS / WRITES / USAGE / IMPORTS / DECORATES / CONTAINS_* /
// INHERITS / THROWS) and the cross-service types the engine can emit on
// multi-repo indexes. Anything unknown lands in `structure`, which is the
// quietest family — an unrecognised edge should recede, not shout.

// Edge colours are drawn from the galaxy's own light, not from a UI palette.
// The previous set used saturated Tailwind hues — teal-green for CALLS, blue
// for IMPORTS — and a green web over a warm-gold spiral looked like a network
// diagram pasted onto a photo of space. Everything here is a tinted WHITE:
// starlight temperatures (warm gold through pale blue) plus H-alpha pink,
// which are the only colours a real galaxy contains. Hue still distinguishes
// the families, but they now read as light rather than as wires.
export const EDGE_FAMILIES = {
  structure: { color: '#8f9bb3', label: 'Structure', alpha: 0.30 }, // cool grey starlight
  flow: { color: '#ffd9a0', label: 'Calls', alpha: 1.00 }, // warm gold, like the core
  deps: { color: '#b9d4ff', label: 'Imports', alpha: 0.80 }, // pale blue, like the arms
  data: { color: '#e8ddff', label: 'Data', alpha: 0.60 }, // faint violet-white
  verify: { color: '#ffc8dc', label: 'Types & tests', alpha: 0.70 }, // H-alpha pink
  network: { color: '#fff0c8', label: 'Network', alpha: 1.20 }, // hottest, near-white
};

const TYPE_TO_FAMILY = {
  // structure — the majority of edges; these are texture, not information
  CONTAINS_FILE: 'structure',
  CONTAINS_FOLDER: 'structure',
  CONTAINS_PACKAGE: 'structure',
  DEFINES: 'structure',
  DEFINES_METHOD: 'structure',
  MEMBER_OF: 'structure',
  // flow
  CALLS: 'flow',
  ASYNC_CALLS: 'flow',
  // deps
  IMPORTS: 'deps',
  // data
  WRITES: 'data',
  USAGE: 'data',
  // verify
  TESTS_FILE: 'verify',
  HANDLES: 'verify',
  IMPLEMENTS: 'verify',
  INHERITS: 'verify',
  DECORATES: 'verify',
  THROWS: 'verify',
  // network — cross-service links, the most interesting edges in the graph
  HTTP_CALLS: 'network',
  GRPC_CALLS: 'network',
  GRAPHQL_CALLS: 'network',
  TRPC_CALLS: 'network',
  CROSS_HTTP_CALLS: 'network',
  CROSS_ASYNC_CALLS: 'network',
  CROSS_GRPC_CALLS: 'network',
  CROSS_GRAPHQL_CALLS: 'network',
  CROSS_TRPC_CALLS: 'network',
  CROSS_CHANNEL: 'network',
};

export function familyForEdgeType(type) {
  return TYPE_TO_FAMILY[type] ?? 'structure';
}

export function colorForEdgeType(type) {
  return EDGE_FAMILIES[familyForEdgeType(type)].color;
}

export function alphaForEdgeType(type) {
  return EDGE_FAMILIES[familyForEdgeType(type)].alpha;
}

// --- Node labels (chrome only) -------------------------------------------
//
// Drawn from the same wheel so the sidebar dots share a vocabulary with the
// canvas instead of being a fourth, unrelated palette.

const LABEL_COLORS = {
  Project: FOLDER_HUES[1],
  Package: FOLDER_HUES[11],
  Module: FOLDER_HUES[11],
  Folder: FOLDER_HUES[8],
  File: FOLDER_HUES[6],
  Class: FOLDER_HUES[4],
  Interface: FOLDER_HUES[4],
  Function: FOLDER_HUES[0],
  Method: FOLDER_HUES[0],
  Route: FOLDER_HUES[3],
  Section: FOLDER_HUES[10],
  Field: FOLDER_HUES[2],
  Variable: '#7d8da1',
};

export const DEFAULT_LABEL_COLOR = '#94a3b8';

export function colorForLabel(label) {
  return LABEL_COLORS[label] ?? DEFAULT_LABEL_COLOR;
}
