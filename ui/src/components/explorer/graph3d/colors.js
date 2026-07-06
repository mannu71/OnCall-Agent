// Node label -> color mapping for sidebar/tooltips (structural meaning)

const LABEL_COLORS = {
  Project: '#e11d48',
  Package: '#f97316',
  Module: '#f97316',
  Folder: '#22c55e',
  File: '#3b82f6',
  Class: '#a855f7',
  Interface: '#a855f7',
  Function: '#06b6d4',
  Method: '#06b6d4',
  Route: '#eab308',
  Variable: '#64748b',
};

const DEFAULT_COLOR = '#94a3b8';

export function colorForLabel(label) {
  return LABEL_COLORS[label] ?? DEFAULT_COLOR;
}
