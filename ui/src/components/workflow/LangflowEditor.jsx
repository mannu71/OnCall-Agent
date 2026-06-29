/**
 * LangflowEditor — Langflow-style workflow editor.
 * Ported directly from the oncall-agent-design-system mockup.
 *
 * Node data is static (SAMPLE_WORKFLOW).
 * Props: workflowName, initialNodes, initialEdges, onSave, onCancel
 * Ref:   getWorkflowData() → { nodes, edges }
 */
import React, {
  useState, useMemo, useRef, useEffect, useCallback, memo,
  forwardRef, useImperativeHandle,
} from 'react';
import { getDisplayTimezone } from '../../lib/formatTime.js';
import { getLLMs } from '../../services/llmService.js';
import { getMCPServers } from '../../services/mcpService.js';
import { getAppSettings } from '../../services/apiClient.js';
import { useWorkflowStatus } from '../../context/WorkflowStatusContext.jsx';
import { useWorkflowExecutionsQuery } from '../../hooks/queries/useExecutionsQuery.js';
import { validateConnection, getValidDropTargets, slotsForNode, modelNamesOf, reconcileModelEdges, mcpServerNamesOf, reconcileMcpEdges, workflowWarnings } from './portValidation.js';
import agentApiClient from '../../services/agentApiClient.js';
import { CAT_TINT, PORT_TYPE, NODE_TYPES, NODE_DEFAULTS } from './editor/constants/nodeDefinitions.js';

// Global application timezone (Settings → Timezone). Schedule nodes display it
// read-only; the editor refreshes it on mount via getAppSettings(). Module-level
// so the pure SlotRow renderer can read it without prop drilling.
let GLOBAL_TZ = 'UTC';
export function _setGlobalTzCache(tz) { if (tz) GLOBAL_TZ = String(tz); }

// Format an IANA zone like the Settings dropdown does: "Asia / Kolkata (GMT+5:30)".
function formatTz(tz) {
  if (!tz) return 'UTC';
  const pretty = String(tz).replace(/_/g, ' ').replace(/\//g, ' / ');
  try {
    const part = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'shortOffset' })
      .formatToParts(new Date()).find(p => p.type === 'timeZoneName');
    return part ? `${pretty} (${part.value})` : pretty;
  } catch { return pretty; }
}


// ─────────────────────────────────────────────────────────────────
// LEGACY FORMAT NORMALIZER
// Converts old ReactFlow nodes/edges (saved pre-rewrite) to the new schema.
// Old nodes: { id, type, position:{x,y}, data:{label,...}, x:null, y:null }
// New nodes: { id, type, x, y, name, status, params }
// ─────────────────────────────────────────────────────────────────

const OLD_HANDLE_TO_SLOT = {
  // outputs
  'scheduler-output': 'trigger',
  'llm-output':       'lm',
  'source-left':      'trigger',
  'output':           'response',
  'response':         'response',
  // inputs
  'agent-input':      'input',
  'model':            'lm',
  'memory':           'memory',
  'tool':             'tool',
  'tools':            'tools',
  'trigger':          'trigger',
  'input':            'input',
  'msg':              'msg',
  'body':             'body',
};

function _dataToParams(type, data) {
  // Map old `data` object fields to the new `params` keys (slot IDs)
  switch (type) {
    case 'agent':
      return {
        system:  data.instructions  || data.system  || '',
        maxIter: String(data.maxIterations || data.maxIter || '10'),
      };
    case 'anthropic_model':
      return {
        model:  data.model   || data.modelId || '',
        temp:   String(data.temperature ?? data.temp ?? '0.7'),
        system: data.system  || '',
      };
    case 'openai_model':
      return {
        model: data.model  || data.modelId || '',
        temp:  String(data.temperature ?? data.temp ?? '0.7'),
      };
    case 'schedule':
      return { cron: data.cron || '*/15 * * * *', tz: data.tz || 'UTC' };
    case 'vector_memory':
      return { collection: data.collection || '', topK: String(data.topK || '5') };
    case 'cloudwatch_tool': {
      const groups = Array.isArray(data.logGroups) ? data.logGroups.join(',')
                   : Array.isArray(data.groups)    ? data.groups.join(',')
                   : String(data.groups || data.logGroups || '');
      return {
        region:    data.awsRegion || data.region || 'us-east-1',
        profile:   data.awsProfile || data.profile || '',
        groups,
        analysis:  data.analysisType || data.analysis || 'error-patterns',
        analysis_depth: data.analysisDepth || data.analysis_depth || 'auto',
        range:     data.timeRange || data.range || '15m',
        threshold: String(data.errorThreshold ?? data.threshold ?? '10'),
        alerts:    String(data.enableAlerts ?? data.alerts ?? 'false'),
      };
    }
    case 'postgres_tool':
      return { connection: data.connection || data.connectionString || '' };
    case 'wiki':
      return {
        format:   data.format   || 'Summary',
        platform: data.platform || 'Azure DevOps Wiki',
        wikiUrl:  data.wikiUrl  || '',
        pagePath: data.pagePath || '',
        project:  data.project  || '',
        pat:      data.pat      || '',
        tokenVar: data.tokenVar || 'ADO_WIKI_PAT',
      };
    case 'if':
      return { condition: data.condition || '' };
    case 'orchestrator':
      return { sqlFile: data.sqlFile || data.fileName || '' };
    default:
      // Pass through all non-function data keys
      return Object.fromEntries(
        Object.entries(data).filter(([, v]) => typeof v !== 'function' && typeof v !== 'object')
      );
  }
}

function normalizeNode(n) {
  // Detect old ReactFlow format: position object present or x is null/undefined
  const isOld = n.position != null || n.x == null;
  if (!isOld) return n;
  return {
    ...n,
    x:      n.position?.x ?? 0,
    y:      n.position?.y ?? 0,
    name:   n.name   || n.data?.label || n.data?.name || n.type,
    status: n.status || 'idle',
    params: n.params || _dataToParams(n.type, n.data || {}),
  };
}

function normalizeEdge(e) {
  const sourceSlot = e.sourceSlot || OLD_HANDLE_TO_SLOT[e.sourceHandle] || e.sourceHandle || 'output';
  const targetSlot = e.targetSlot || OLD_HANDLE_TO_SLOT[e.targetHandle] || e.targetHandle || 'input';
  return { ...e, sourceSlot, targetSlot };
}

// ─────────────────────────────────────────────────────────────────
// 4. SAMPLE WORKFLOW  (from workflow-data.js)
// ─────────────────────────────────────────────────────────────────

const SAMPLE_WORKFLOW = {
  name:  'api-health-sweep',
  title: 'API Health Sweep',
  nodes: [
    { id: 'n_trigger', type: 'schedule',        x:   20, y:  40, name: 'Every 15 min',     status: 'success',
      params: { cron: '*/15 * * * *', tz: 'UTC' } },
    { id: 'n_model',   type: 'anthropic_model', x:  300, y:  40, name: 'Claude Haiku 4.5', status: 'idle',
      params: { model: 'claude-haiku-4-5', temp: '0.2',
                system: 'You triage SLO breaches. Return JSON with breaches[] and severity.' } },
    { id: 'n_mem',     type: 'vector_memory',   x:  300, y: 360, name: 'Past breaches',    status: 'idle',
      params: { collection: 'slo-history', topK: '6' } },
    { id: 'n_tool_cw', type: 'cloudwatch_tool', x:  300, y: 580, name: 'CloudWatch',       status: 'idle',
      params: { groups: '4', range: '15m' } },
    { id: 'n_agent',   type: 'agent',           x:  580, y: 100, name: 'Health Agent',     status: 'running',
      params: { system: 'Triage SLO breaches from 24 endpoints. Cite log evidence.', maxIter: '12' } },
    { id: 'n_wiki',    type: 'wiki',            x:  890, y: 100, name: 'Post summary',     status: 'idle',
      params: { format: 'Summary', platform: 'Azure DevOps Wiki', wikiUrl: '', pagePath: '', project: '', pat: '', tokenVar: 'ADO_WIKI_PAT' } },
  ],
  edges: [
    { id: 'e1', source: 'n_trigger', sourceSlot: 'trigger',  target: 'n_agent',  targetSlot: 'trigger'  },
    { id: 'e2', source: 'n_model',   sourceSlot: 'lm',       target: 'n_agent',  targetSlot: 'lm'       },
    { id: 'e3', source: 'n_mem',     sourceSlot: 'mem',      target: 'n_agent',  targetSlot: 'memory'   },
    { id: 'e4', source: 'n_tool_cw', sourceSlot: 'tool',     target: 'n_agent',  targetSlot: 'tools'    },
    { id: 'e5', source: 'n_agent',   sourceSlot: 'response', target: 'n_wiki',   targetSlot: 'msg'      },
  ],
};

// EXECUTIONS constant removed — ExecutionList now fetches live data.

// ─────────────────────────────────────────────────────────────────
// 4. GEOMETRY  (from wf-node.jsx)
// ─────────────────────────────────────────────────────────────────

const NODE_W        = 244;
const HEADER_H      = 32;
const TITLE_BLOCK_H = 40;
const ROW_H         = 30;
const FIELD_H       = 56;
const TEXTAREA_H    = 84;
const SELECT_H      = 50;
const BODY_PAD_TOP  = 6;
const BODY_PAD_BOT  = 10;
const FOOTER_H      = 28;

const WEEKDAY_H = 58;
const TOGGLE_H  = 38;
const CHIPS_H   = 62;
const SEGMENT_H = 56;
function rowHeight(slot) {
  if (slot.kind === 'field')                                   return FIELD_H;
  if (slot.kind === 'tz-info')                                 return FIELD_H;
  if (slot.kind === 'textarea')                                return TEXTAREA_H;
  if (slot.kind === 'select' || slot.kind === 'llm-select' ||
      slot.kind === 'db-select' || slot.kind === 'mcp-select' || slot.kind === 'repo-select') return SELECT_H;
  if (slot.kind === 'weekday-select')                          return WEEKDAY_H;
  if (slot.kind === 'file-select')                             return FIELD_H;
  if (slot.kind === 'toggle')                                  return TOGGLE_H;
  if (slot.kind === 'segment')                                 return SEGMENT_H;
  if (slot.kind === 'chips')                                   return CHIPS_H;
  return ROW_H;
}
function rowOffset(slots, idx) {
  let y = HEADER_H + TITLE_BLOCK_H + BODY_PAD_TOP;
  for (let i = 0; i < idx; i++) y += rowHeight(slots[i]);
  return y;
}
function totalHeight(slots) {
  return rowOffset(slots, slots.length) + BODY_PAD_BOT + FOOTER_H;
}
function handlePosition(node, slotId) {
  const def = NODE_TYPES[node.type];
  if (!def) return { x: node.x, y: node.y };
  const slots = slotsForNode(NODE_TYPES, node);
  const idx = slots.findIndex(s => s.id === slotId);
  if (idx < 0) return { x: node.x, y: node.y };
  const slot = slots[idx];
  const y = node.y + rowOffset(slots, idx) + ROW_H / 2;
  const w = node.width || def.width || NODE_W;
  if (slot.kind === 'port-in')  return { x: node.x,     y };
  if (slot.kind === 'port-out') return { x: node.x + w, y };
  return { x: node.x + w / 2, y };
}

// ─────────────────────────────────────────────────────────────────
// 5. ICONS  (from shared.jsx)
// ─────────────────────────────────────────────────────────────────

const ICONS = {
  play:         'M5 3l14 9-14 9V3z',
  plus:         'M12 5v14M5 12h14',
  search:       'M11 19a8 8 0 110-16 8 8 0 010 16zM21 21l-4.3-4.3',
  more:         'M12 6h.01M12 12h.01M12 18h.01',
  trash:        'M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6',
  clock:        'M12 6v6l4 2M12 22a10 10 0 110-20 10 10 0 010 20z',
  chevronRight: 'M9 6l6 6-6 6',
  chevronLeft:  'M15 6l-6 6 6 6',
  chevronDown:  'M6 9l6 6 6-6',
  x:            'M18 6L6 18M6 6l12 12',
  workflow:     'M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h6v6h-6zM9 6h6M9 18h6M6 9v6M18 9v6',
  settings:     'M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33h0a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51h0a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v0a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z',
  zoomIn:       'M11 19a8 8 0 110-16 8 8 0 010 16zM21 21l-4.3-4.3M11 8v6M8 11h6',
  zoomOut:      'M11 19a8 8 0 110-16 8 8 0 010 16zM21 21l-4.3-4.3M8 11h6',
  fit:          'M3 9V5a2 2 0 012-2h4M15 3h4a2 2 0 012 2v4M21 15v4a2 2 0 01-2 2h-4M9 21H5a2 2 0 01-2-2v-4',
  save:         'M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2zM17 21v-8H7v8M7 3v5h8',
  history:      'M3 12a9 9 0 109-9 9.75 9.75 0 00-6.74 2.74L3 8M3 3v5h5M12 7v5l4 2',
  sparkle:      'M12 3v18M3 12h18M5 5l14 14M19 5L5 19',
  db:           'M12 2C7 2 4 4 4 6v12c0 2 3 4 8 4s8-2 8-4V6c0-2-3-4-8-4zM4 6c0 2 3 4 8 4s8-2 8-4M4 12c0 2 3 4 8 4s8-2 8-4',
  cloud:        'M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z',
  server:       'M2 2h20v6H2zM2 14h20v6H2zM6 6h.01M6 18h.01',
  globe:        'M12 22a10 10 0 100-20 10 10 0 000 20zM2 12h20M12 2a15 15 0 010 20M12 2a15 15 0 000 20',
  webhook:      'M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.15c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.93-2.92-2.93z',
  funnel:       'M3 4h18l-7 8v6l-4 2v-8L3 4z',
  send:         'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
  bell:         'M18 8a6 6 0 00-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0',
  msg:          'M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z',
  mail:         'M4 4h16a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V6a2 2 0 012-2zM22 6L12 13 2 6',
  tool:         'M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.121 2.121 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z',
  wiki:         'M4 19.5A2.5 2.5 0 016.5 17H20M4 19.5A2.5 2.5 0 014 17V5a2.5 2.5 0 012.5-2.5H20v15H6.5z',
};

function Icon({ name, size = 16, color = 'currentColor', strokeWidth = 1.6, style }) {
  const d = ICONS[name];
  if (!d) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" style={{ display: 'block', flexShrink: 0, ...style }}>
      <path d={d} />
    </svg>
  );
}

// ─────────────────────────────────────────────────────────────────
// 6. NODE CARD  (from wf-node.jsx)
// ─────────────────────────────────────────────────────────────────

function RunningDot() {
  return (
    <span style={{ position: 'relative', display: 'inline-block', width: 7, height: 7 }}>
      <span style={{ position: 'absolute', inset: -3, borderRadius: 999, background: '#dc2626',
                     opacity: 0.4, animation: 'wfPulseDot 1.6s ease-out infinite' }} />
      <span style={{ position: 'relative', display: 'block', width: 7, height: 7, borderRadius: 999, background: '#dc2626' }} />
    </span>
  );
}

function PortChip({ color, label, multi, optional }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
                   padding: '2px 7px 2px 5px', borderRadius: 999,
                   background: `${color}10`, border: `1px solid ${color}33`,
                   fontSize: 10, fontWeight: 600, color, whiteSpace: 'nowrap' }}>
      <span style={{ width: 5, height: 5, borderRadius: 999, background: color }} />
      {label}{multi ? ' · list' : ''}{optional ? ' · opt' : ''}
    </span>
  );
}

function PortHandle({ side, y, color, onPointerDown, drawing }) {
  const sz = 12;
  const pos = side === 'left' ? { left: -sz / 2 } : { right: -sz / 2 };
  const [hov, setHov] = useState(false);
  return (
    <div
      onPointerDown={onPointerDown}
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        position: 'absolute', width: sz, height: sz,
        background: hov || drawing ? color : '#fff',
        border: `2px solid ${color}`,
        borderRadius: 999, boxSizing: 'border-box',
        boxShadow: hov || drawing ? `0 0 0 5px ${color}33` : `0 0 0 3px ${color}1a`,
        top: y, transform: 'translate(0,-50%)', zIndex: 4,
        transition: 'box-shadow 80ms, background 80ms',
        cursor: onPointerDown ? 'crosshair' : 'default',
        ...pos,
      }} />
  );
}

function SlotRow({ slot, value, gtz }) {
  if (slot.kind === 'port-in') {
    const tp = PORT_TYPE[slot.portType] || PORT_TYPE.message;
    return (
      <div style={{ height: ROW_H, padding: '0 12px 0 18px', display: 'flex',
                    alignItems: 'center', gap: 8, borderBottom: '1px dashed #f1f5f9' }}>
        <span style={{ fontSize: 11.5, color: '#334155', fontWeight: 600, flex: 1,
                       overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {slot.label}
        </span>
        <PortChip color={tp.color} label={tp.label} multi={slot.multi} optional={slot.optional} />
      </div>
    );
  }
  if (slot.kind === 'port-out') {
    const tp = PORT_TYPE[slot.portType] || PORT_TYPE.message;
    // Language Model outputs: the row label IS the model name, so the generic
    // "Language Model" chip would be redundant — show a slim colored dot instead.
    const isModelPort = slot.id === 'lm' || slot.id.startsWith('lm::') || slot.id.startsWith('mcp::');
    return (
      <div style={{ height: ROW_H, padding: '0 18px 0 12px', display: 'flex',
                    alignItems: 'center', gap: 8, justifyContent: 'flex-end',
                    borderTop: '1px dashed #f1f5f9' }}>
        {isModelPort
          ? <span style={{ width: 6, height: 6, borderRadius: 999, background: tp.color }} />
          : <PortChip color={tp.color} label={tp.label} />}
        <span style={{ fontSize: 11.5, color: '#334155', fontWeight: 600, textAlign: 'right',
                       overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {slot.label}
        </span>
      </div>
    );
  }
  if (slot.kind === 'field') {
    return (
      <div style={{ padding: '6px 12px 4px', height: FIELD_H, boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                         letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</span>
          {slot.suffix && <span style={{ fontSize: 9.5, color: '#cbd5e1',
                                         fontFamily: "'JetBrains Mono', monospace" }}>{slot.suffix}</span>}
        </div>
        <div style={{ marginTop: 3, height: 26, padding: '0 9px', display: 'flex', alignItems: 'center',
                      background: '#fafbfc', border: '1px solid #eef2f6', borderRadius: 6,
                      fontSize: 12, color: value != null ? '#0f172a' : '#94a3b8',
                      fontFamily: slot.mono ? "'JetBrains Mono', monospace" : 'inherit',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {value ?? '—'}
        </div>
      </div>
    );
  }
  if (slot.kind === 'tz-info') {
    return (
      <div style={{ padding: '6px 12px 4px', height: FIELD_H, boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                         letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</span>
          <span style={{ fontSize: 9, color: '#cbd5e1', fontWeight: 600,
                         letterSpacing: '0.02em', textTransform: 'uppercase' }}>from Settings</span>
        </div>
        <div style={{ marginTop: 3, height: 26, padding: '0 9px', display: 'flex', alignItems: 'center',
                      gap: 6, background: '#f8fafc', border: '1px dashed #e2e8f0', borderRadius: 6,
                      fontSize: 12, color: '#475569',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {formatTz(gtz || GLOBAL_TZ)}
        </div>
      </div>
    );
  }
  if (slot.kind === 'textarea') {
    return (
      <div style={{ padding: '6px 12px 4px', height: TEXTAREA_H, boxSizing: 'border-box' }}>
        <div style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                      letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</div>
        <div style={{ marginTop: 3, padding: '6px 9px', height: 56, boxSizing: 'border-box',
                      background: '#0f172a', border: '1px solid #1e293b', borderRadius: 6,
                      fontSize: 11, color: value ? '#cbd5e1' : '#475569', lineHeight: 1.4,
                      fontFamily: "'JetBrains Mono', monospace",
                      overflow: 'hidden', display: '-webkit-box',
                      WebkitLineClamp: 3, WebkitBoxOrient: 'vertical' }}>
          {value ?? '—'}
        </div>
      </div>
    );
  }
  if (slot.kind === 'select') {
    return (
      <div style={{ padding: '6px 12px 4px', height: SELECT_H, boxSizing: 'border-box' }}>
        <div style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                      letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</div>
        <div style={{ marginTop: 3, height: 26, padding: '0 8px 0 9px', display: 'flex',
                      alignItems: 'center', justifyContent: 'space-between',
                      background: '#fff', border: '1px solid #e2e8f0', borderRadius: 6,
                      fontSize: 12, color: value ? '#0f172a' : '#94a3b8' }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {value || '—'}
          </span>
          <Icon name="chevronDown" size={12} color="#94a3b8" />
        </div>
      </div>
    );
  }
  if (slot.kind === 'file-select') {
    return (
      <div style={{ padding: '6px 12px 4px', height: FIELD_H, boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                         letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</span>
        </div>
        <div style={{ marginTop: 3, height: 26, padding: '0 9px', display: 'flex', alignItems: 'center',
                      gap: 6, background: '#fafbfc', border: '1px solid #eef2f6', borderRadius: 6 }}>
          <Icon name="tool" size={11} color={value ? '#2563eb' : '#cbd5e1'} />
          <span style={{ fontSize: 11.5, color: value ? '#0f172a' : '#94a3b8', fontFamily: "'JetBrains Mono', monospace",
                         overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {value || '—'}
          </span>
        </div>
      </div>
    );
  }
  if (slot.kind === 'weekday-select') {
    const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    const active = value ? value.split(',').filter(Boolean) : [];
    return (
      <div style={{ padding: '6px 12px 4px', height: WEEKDAY_H, boxSizing: 'border-box' }}>
        <div style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                      letterSpacing: '0.02em', textTransform: 'uppercase', marginBottom: 5 }}>{slot.label}</div>
        <div style={{ display: 'flex', gap: 3 }}>
          {DAYS.map(d => (
            <span key={d} style={{ flex: 1, textAlign: 'center', fontSize: 9.5, fontWeight: 700,
                                   padding: '3px 0', borderRadius: 5,
                                   background: active.includes(d) ? '#dc2626' : '#f1f5f9',
                                   color: active.includes(d) ? '#fff' : '#94a3b8' }}>{d[0]}</span>
          ))}
        </div>
      </div>
    );
  }
  if (slot.kind === 'toggle') {
    const on = value === true || value === 'true';
    return (
      <div style={{ padding: '6px 12px', height: TOGGLE_H, boxSizing: 'border-box',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                       letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</span>
        <span style={{ width: 26, height: 14, borderRadius: 999, position: 'relative',
                       background: on ? '#2563eb' : '#cbd5e1', transition: 'background 120ms' }}>
          <span style={{ position: 'absolute', top: 1, left: on ? 13 : 1, width: 12, height: 12,
                         borderRadius: 999, background: '#fff', transition: 'left 120ms' }} />
        </span>
      </div>
    );
  }
  if (slot.kind === 'segment') {
    const opts = slot.options || [];
    const active = value != null && value !== '' ? value : (opts[0] && opts[0].value);
    return (
      <div style={{ padding: '6px 12px 4px', height: SEGMENT_H, boxSizing: 'border-box' }}>
        <div style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                      letterSpacing: '0.02em', textTransform: 'uppercase', marginBottom: 5 }}>{slot.label}</div>
        <div style={{ display: 'flex', gap: 2, padding: 2, borderRadius: 7,
                      background: '#f1f5f9', border: '1px solid #e2e8f0' }}>
          {opts.map(o => {
            const on = o.value === active;
            return (
              <span key={o.value} style={{ flex: 1, textAlign: 'center', fontSize: 11, fontWeight: 600,
                                           padding: '4px 0', borderRadius: 5, whiteSpace: 'nowrap',
                                           background: on ? '#fff' : 'transparent',
                                           color: on ? '#2563eb' : '#94a3b8',
                                           boxShadow: on ? '0 1px 2px rgba(15,23,42,0.08)' : 'none' }}>{o.label}</span>
            );
          })}
        </div>
      </div>
    );
  }
  if (slot.kind === 'chips') {
    const parts = value ? String(value).split(',').map(s => s.trim()).filter(Boolean) : [];
    return (
      <div style={{ padding: '6px 12px 4px', height: CHIPS_H, boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                         letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</span>
          <span style={{ fontSize: 9.5, color: '#cbd5e1', fontFamily: "'JetBrains Mono', monospace" }}>
            {parts.length ? `${parts.length}` : 'count'}
          </span>
        </div>
        <div style={{ marginTop: 3, height: 32, padding: '4px 6px', display: 'flex', gap: 4,
                      flexWrap: 'nowrap', overflow: 'hidden',
                      background: '#fafbfc', border: '1px solid #eef2f6', borderRadius: 6,
                      alignItems: 'center' }}>
          {parts.length === 0
            ? <span style={{ fontSize: 11, color: '#94a3b8' }}>—</span>
            : parts.slice(0, 3).map(p => (
                <span key={p} style={{ fontSize: 10, padding: '2px 6px', background: '#e0f2fe',
                                        color: '#0c4a6e', borderRadius: 4, whiteSpace: 'nowrap',
                                        overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 90,
                                        fontFamily: "'JetBrains Mono', monospace" }}>{p}</span>
              ))
          }
          {parts.length > 3 && (
            <span style={{ fontSize: 10, color: '#64748b' }}>+{parts.length - 3}</span>
          )}
        </div>
      </div>
    );
  }
  if (slot.kind === 'llm-select' || slot.kind === 'db-select' || slot.kind === 'mcp-select') {
    // Canvas card: show selected value(s) as plain text — actual picker is in properties panel
    const parts = value ? String(value).split(',').filter(Boolean) : [];
    const noun = slot.kind === 'llm-select' ? 'models' : 'servers';
    const display = parts.length === 0 ? '—'
      : parts.length === 1 ? parts[0]
      : `${parts.length} ${noun}`;
    return (
      <div style={{ padding: '6px 12px 4px', height: SELECT_H, boxSizing: 'border-box' }}>
        <div style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                      letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</div>
        <div style={{ marginTop: 3, height: 26, padding: '0 9px', display: 'flex', alignItems: 'center',
                      background: '#fafbfc', border: '1px solid #eef2f6', borderRadius: 6,
                      fontSize: 12, color: parts.length ? '#0f172a' : '#94a3b8',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {display}
        </div>
      </div>
    );
  }
  if (slot.kind === 'routes-editor') {
    const routesCount = value ? Object.keys(value).length : 0;
    return (
      <div style={{ padding: '6px 12px 4px', height: ROW_H, boxSizing: 'border-box' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600,
                         letterSpacing: '0.02em', textTransform: 'uppercase' }}>{slot.label}</span>
          <span style={{ fontSize: 10, color: '#94a3b8', background: '#f1f5f9', padding: '1px 5px', borderRadius: 4, fontWeight: 700 }}>
            {routesCount} active
          </span>
        </div>
      </div>
    );
  }
  return null;
}

function NodeFooter({ slots }) {
  const outs = slots.filter(s => s.kind === 'port-out');
  if (outs.length === 0) {
    return (
      <div style={{ height: FOOTER_H, padding: '0 12px', display: 'flex', alignItems: 'center',
                    justifyContent: 'flex-end', background: '#fafbfc', borderTop: '1px solid #f1f5f9',
                    borderBottomLeftRadius: 11, borderBottomRightRadius: 11,
                    fontSize: 10, color: '#94a3b8', letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        Terminal node
      </div>
    );
  }
  // Language Model node: every output is a model port and each row already shows
  // its model name, so collapse the repeated identical chips into one summary.
  const allModelOuts = outs.every(o => o.id === 'lm' || o.id.startsWith('lm::'));
  if (allModelOuts && outs.length > 1) {
    return (
      <div style={{ height: FOOTER_H, padding: '0 12px', display: 'flex', alignItems: 'center',
                    gap: 6, justifyContent: 'flex-end', background: '#fafbfc',
                    borderTop: '1px solid #f1f5f9', borderBottomLeftRadius: 11, borderBottomRightRadius: 11 }}>
        <span style={{ fontSize: 10, color: '#94a3b8', letterSpacing: '0.06em',
                       textTransform: 'uppercase', fontWeight: 600 }}>Outputs</span>
        <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600 }}>
          {outs.length} models · wire each
        </span>
      </div>
    );
  }
  // MCP node: same collapse pattern — one tool port per server.
  const allMcpOuts = outs.every(o => o.id.startsWith('mcp::'));
  if (allMcpOuts && outs.length > 1) {
    return (
      <div style={{ height: FOOTER_H, padding: '0 12px', display: 'flex', alignItems: 'center',
                    gap: 6, justifyContent: 'flex-end', background: '#fafbfc',
                    borderTop: '1px solid #f1f5f9', borderBottomLeftRadius: 11, borderBottomRightRadius: 11 }}>
        <span style={{ fontSize: 10, color: '#94a3b8', letterSpacing: '0.06em',
                       textTransform: 'uppercase', fontWeight: 600 }}>Outputs</span>
        <span style={{ fontSize: 10.5, color: '#64748b', fontWeight: 600 }}>
          {outs.length} servers · wire each
        </span>
      </div>
    );
  }
  return (
    <div style={{ height: FOOTER_H, padding: '0 12px', display: 'flex', alignItems: 'center',
                  gap: 6, justifyContent: 'flex-end', background: '#fafbfc',
                  borderTop: '1px solid #f1f5f9', borderBottomLeftRadius: 11, borderBottomRightRadius: 11 }}>
      <span style={{ fontSize: 10, color: '#94a3b8', letterSpacing: '0.06em',
                     textTransform: 'uppercase', fontWeight: 600 }}>Outputs</span>
      {outs.map(o => {
        const tp = PORT_TYPE[o.portType] || PORT_TYPE.message;
        return (
          <span key={o.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 4,
                                    padding: '2px 6px 2px 5px', borderRadius: 999,
                                    background: '#fff', border: `1px solid ${tp.color}33`,
                                    fontSize: 10, fontWeight: 600, color: tp.color }}>
            <span style={{ width: 5, height: 5, borderRadius: 999, background: tp.color }} />
            {tp.label}
          </span>
        );
      })}
    </div>
  );
}

const WfNode = memo(function WfNode({ node, selected, dragging, drawingSourceSlot, onClick, onPointerDown, onDelete, onPortPointerDown, liveStatus, gtz }) {
  const def = NODE_TYPES[node.type];
  if (!def) return null;
  const tint = CAT_TINT[def.category] || CAT_TINT.Tools;
  const w = def.width || NODE_W;
  const slots = slotsForNode(NODE_TYPES, node);
  const h = totalHeight(slots);
  // liveStatus: 'running' | 'success' | 'failed' | null (null = idle/unknown)
  const effectiveStatus = liveStatus ?? node.status ?? 'idle';
  const borderColor = selected ? '#dc2626' : effectiveStatus === 'failed' ? '#fca5a5' : '#e2e8f0';
  const borderWidth = selected ? 2 : 1;
  const shadow = selected
    ? '0 0 0 4px rgba(220,38,38,0.10), 0 8px 16px -6px rgba(15,23,42,0.10), 0 2px 4px rgba(15,23,42,0.06)'
    : '0 1px 3px rgba(15,23,42,0.07), 0 1px 2px -1px rgba(15,23,42,0.05)';

  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div onPointerDown={e => onPointerDown?.(node.id, e)}
      onClick={e => { e.stopPropagation(); onClick?.(node.id); setMenuOpen(false); }}
      style={{ position: 'absolute', left: node.x, top: node.y, width: w, height: h,
               background: '#fff', border: `${borderWidth}px solid ${borderColor}`,
               borderRadius: 12, boxShadow: shadow,
               cursor: dragging ? 'grabbing' : 'grab', userSelect: 'none',
               transition: dragging ? 'none' : 'border-color 120ms, box-shadow 120ms',
               zIndex: selected ? 5 : 2, overflow: 'visible' }}>

      {/* Header strip */}
      <div style={{ height: HEADER_H, display: 'flex', alignItems: 'center', gap: 7,
                    padding: '0 11px', background: tint.bg, color: tint.fg,
                    borderTopLeftRadius: 11, borderTopRightRadius: 11,
                    borderBottom: `1px solid ${tint.border}`,
                    fontSize: 10.5, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
        <span style={{ width: 18, height: 18, borderRadius: 5, background: '#fff',
                       border: `1px solid ${tint.border}`,
                       display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name={def.icon} size={11} color={tint.fg} strokeWidth={2.1} />
        </span>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {def.label}
        </span>
        {effectiveStatus === 'running' && <RunningDot />}
        {effectiveStatus === 'success' && <span style={{ width: 7, height: 7, borderRadius: 999,
                                                         background: '#059669', boxShadow: '0 0 0 2px #ecfdf5' }} />}
        {effectiveStatus === 'failed'  && <span style={{ width: 7, height: 7, borderRadius: 999,
                                                         background: '#dc2626', boxShadow: '0 0 0 2px #fef2f2' }} />}
        {/* ⋯ menu */}
        <span style={{ position: 'relative' }}>
          <span onClick={e => { e.stopPropagation(); setMenuOpen(m => !m); }}
            style={{ cursor: 'pointer', opacity: 0.55, display: 'flex', alignItems: 'center',
                     padding: '2px 4px', borderRadius: 4 }}>
            <Icon name="more" size={14} color={tint.fg} strokeWidth={2} />
          </span>
          {menuOpen && (
            <div onClick={e => e.stopPropagation()}
              style={{ position: 'absolute', right: 0, top: 20, zIndex: 99, minWidth: 130,
                       background: '#fff', border: '1px solid #e2e8f0', borderRadius: 8,
                       boxShadow: '0 8px 24px rgba(15,23,42,0.12)', overflow: 'hidden' }}>
              <button onClick={() => { setMenuOpen(false); onDelete?.(node.id); }}
                style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                         padding: '9px 14px', border: 'none', background: 'none',
                         cursor: 'pointer', fontSize: 12.5, color: '#dc2626', fontWeight: 500 }}
                onMouseEnter={e => e.currentTarget.style.background = '#fef2f2'}
                onMouseLeave={e => e.currentTarget.style.background = 'none'}>
                <Icon name="trash" size={13} color="#dc2626" /> Delete node
              </button>
            </div>
          )}
        </span>
      </div>

      {/* Title + desc */}
      <div style={{ padding: '8px 12px 6px', height: TITLE_BLOCK_H, boxSizing: 'border-box' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#0f172a',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </div>
        <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {def.desc}
        </div>
      </div>

      {/* Slot rows */}
      <div style={{ padding: `${BODY_PAD_TOP}px 0 ${BODY_PAD_BOT}px` }}>
        {slots.map(s => <SlotRow key={s.id} slot={s} value={node.params?.[s.id]} gtz={gtz} />)}
      </div>

      {/* Footer */}
      <NodeFooter slots={slots} />

      {/* Port handles */}
      {slots.map((s, i) => {
        if (s.kind !== 'port-in' && s.kind !== 'port-out') return null;
        const portY = rowOffset(slots, i) + ROW_H / 2;
        const tp = PORT_TYPE[s.portType] || PORT_TYPE.message;
        const side = s.kind === 'port-in' ? 'left' : 'right';
        const isDrawingThisPort = drawingSourceSlot === s.id && s.kind === 'port-out';
        return (
          <PortHandle key={s.id} side={side} y={portY} color={tp.color}
            drawing={isDrawingThisPort}
            onPointerDown={s.kind === 'port-out' && onPortPointerDown
              ? e => { e.stopPropagation(); onPortPointerDown(node, s, portY, e); }
              : undefined}
          />
        );
      })}
    </div>
  );
});

// ─────────────────────────────────────────────────────────────────
// 7. CANVAS  (from wf-canvas.jsx)
// ─────────────────────────────────────────────────────────────────

function muteColor(hex, k) {
  const p = s => parseInt(s, 16);
  const r = p(hex.slice(1,3)), g = p(hex.slice(3,5)), b = p(hex.slice(5,7));
  const mix = (a, n) => Math.round(a*k + n*(1-k));
  const h = n => n.toString(16).padStart(2,'0');
  return '#' + h(mix(r,148)) + h(mix(g,163)) + h(mix(b,184));
}

function EdgePath({ edge, selected, onSelect }) {
  const { from, to, portType } = edge;
  const tp  = PORT_TYPE[portType] || PORT_TYPE.message;
  const color = selected ? '#dc2626' : tp.color;
  const adx = Math.abs(to.x - from.x), ady = Math.abs(to.y - from.y);
  const ctrl = Math.max(40, Math.min(160, adx * 0.6 + ady * 0.2));
  const d = `M ${from.x} ${from.y} C ${from.x+ctrl} ${from.y}, ${to.x-ctrl} ${to.y}, ${to.x} ${to.y}`;
  return (
    <g style={{ cursor: 'pointer', pointerEvents: 'auto' }} onClick={e => { e.stopPropagation(); onSelect?.(edge.id); }}>
      {/* Fat transparent hit area */}
      <path d={d} stroke="transparent" strokeWidth={18} fill="none" />
      {/* Glow halo — always on, same as active edges in sample workflow */}
      <path d={d} stroke={selected ? '#dc262633' : `${tp.color}29`}
            strokeWidth={selected ? 10 : 9} fill="none" strokeLinecap="round" />
      {/* Solid colored stroke */}
      <path d={d} stroke={color} strokeWidth={2.4} fill="none" strokeLinecap="round" />
      {/* Animated white flow dash — always on */}
      <path d={d} stroke="#ffffff" strokeWidth={1.32} fill="none"
            strokeDasharray="4 10" strokeLinecap="round"
            style={{ animation: 'wfDashFlow 1.1s linear infinite', opacity: 0.95 }} />
    </g>
  );
}

function GhostEdge({ from, to }) {
  const adx = Math.abs(to.x - from.x), ady = Math.abs(to.y - from.y);
  const ctrl = Math.max(40, Math.min(160, adx*0.6 + ady*0.2));
  const d = `M ${from.x} ${from.y} C ${from.x+ctrl} ${from.y}, ${to.x-ctrl} ${to.y}, ${to.x} ${to.y}`;
  return (
    <g style={{ pointerEvents: 'none' }}>
      <path d={d} stroke="#94a3b8" strokeWidth={2} fill="none" strokeDasharray="6 5" strokeLinecap="round" opacity={0.7} />
    </g>
  );
}

function DotGrid({ view, gridRef }) {
  const sz = 24 * view.zoom;
  return (
    <div ref={gridRef} data-canvas-bg style={{ position: 'absolute', inset: 0,
      backgroundImage: 'radial-gradient(circle,#cbd5e1 1px,transparent 1px)',
      backgroundSize: `${sz}px ${sz}px`,
      backgroundPosition: `${view.x}px ${view.y}px`,
      opacity: 0.55, pointerEvents: 'none' }} />
  );
}

function PortTypeLegend() {
  const items = [['message','Message'],['model','Language Model'],['memory','Memory'],
                 ['tool','Tool'],['data','Data'],['trigger','Trigger']];
  return (
    <div style={{ position: 'absolute', right: 16, bottom: 16, display: 'flex', flexDirection: 'column',
                  gap: 6, padding: '10px 12px', background: '#fff', border: '1px solid #e2e8f0',
                  borderRadius: 10, boxShadow: '0 4px 12px rgba(15,23,42,0.06)' }}>
      <div style={{ fontSize: 9.5, fontWeight: 700, color: '#94a3b8',
                    letterSpacing: '0.08em', textTransform: 'uppercase' }}>Port types</div>
      {items.map(([k, lbl]) => {
        const c = (PORT_TYPE[k] || PORT_TYPE.message).color;
        return (
          <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <span style={{ width: 7, height: 7, borderRadius: 999, background: c }} />
            <span style={{ fontSize: 11, color: '#334155', fontWeight: 500 }}>{lbl}</span>
          </div>
        );
      })}
    </div>
  );
}

function CanvasBtn({ onClick, icon, title }) {
  const [h, setH] = useState(false);
  return (
    <button onClick={onClick} title={title}
      onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{ width: 30, height: 30, padding: 0, borderRadius: 7,
               background: h ? '#f1f5f9' : 'transparent', border: 'none',
               cursor: 'pointer', display: 'inline-flex', alignItems: 'center',
               justifyContent: 'center', color: '#475569' }}>
      <Icon name={icon} size={14} />
    </button>
  );
}

function WorkflowCanvas({ nodes, setNodes, edges, setEdges, selectedId, selectedEdgeId, onSelect, onSelectEdge, onDelete, workflowName, gtz, latestExecution }) {
  const ref = useRef(null);
  const [view, setView]     = useState({ x: 24, y: 24, zoom: 0.62 });
  const [panning, setPanning] = useState(false);
  const { isWorkflowRunning } = useWorkflowStatus();
  const isLive = workflowName ? isWorkflowRunning(workflowName) : false;
  const panRef  = useRef(null);
  const viewRef = useRef(view);
  const transformLayerRef = useRef(null);
  const dotGridRef = useRef(null);
  useEffect(() => { viewRef.current = view; }, [view]);

  const applyViewTransform = useCallback((v) => {
    if (transformLayerRef.current) {
      transformLayerRef.current.style.transform =
        `translate(${v.x}px,${v.y}px) scale(${v.zoom})`;
    }
    if (dotGridRef.current) {
      const sz = 24 * v.zoom;
      dotGridRef.current.style.backgroundSize = `${sz}px ${sz}px`;
      dotGridRef.current.style.backgroundPosition = `${v.x}px ${v.y}px`;
    }
  }, []);

  // ── Per-node status from latest execution ─────────────────────
  const nodeStatusMap = useMemo(() => {
    const map = {};
    if (latestExecution?.results) {
      for (const [nid, r] of Object.entries(latestExecution.results)) {
        map[nid] = r?.status ?? null;
      }
    }
    return map;
  }, [latestExecution]);

  // Track whether the last pointerdown turned into a drag — suppresses the
  // subsequent click event on the node so the panel doesn't open after a drag.
  const nodeDraggedRef = useRef(false);

  // ── Draw-edge state ────────────────────────────────────────────
  // drawEdge: { sourceNodeId, sourceSlotId, fx, fy } | null
  const [drawEdge, setDrawEdge] = useState(null);
  const [drawMouse, setDrawMouse] = useState({ x: 0, y: 0 }); // world coords
  const [validTargets, setValidTargets] = useState(null);    // Set<"nodeId|slotId"> while dragging
  const [dropError, setDropError] = useState(null);           // transient rejection message
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => {
    if (!dropError) return;
    const t = setTimeout(() => setDropError(null), 2500);
    return () => clearTimeout(t);
  }, [dropError]);

  // ── Delete key → remove selected node or edge ─────────────────
  useEffect(() => {
    const onKey = e => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return;
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (selectedEdgeId) { onSelectEdge(null); setEdges(es => es.filter(ex => ex.id !== selectedEdgeId)); }
      else if (selectedId) { onDelete?.(selectedId); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selectedId, selectedEdgeId, onDelete, onSelectEdge, setEdges]);

  // ── Port drag → draw new edge ─────────────────────────────────
  const startPortDrag = useCallback((srcNode, srcSlot, portY, e) => {
    e.stopPropagation();
    const v = viewRef.current;
    const rect = ref.current.getBoundingClientRect();
    const w = srcNode.width || (NODE_TYPES[srcNode.type]?.width) || NODE_W;
    const fx = srcNode.x + w;   // right edge of node (port-out is on the right)
    const fy = srcNode.y + portY;
    const mx = (e.clientX - rect.left - v.x) / v.zoom;
    const my = (e.clientY - rect.top  - v.y) / v.zoom;
    setDrawEdge({ sourceNodeId: srcNode.id, sourceSlotId: srcSlot.id, fx, fy });
    setDrawMouse({ x: mx, y: my });
    setValidTargets(getValidDropTargets({
      sourceNodeId: srcNode.id, sourceSlotId: srcSlot.id,
      nodes: nodesRef.current, edges: edgesRef.current, nodeTypes: NODE_TYPES,
    }));

    const onMove = ev => {
      const vv = viewRef.current;
      const rr = ref.current.getBoundingClientRect();
      setDrawMouse({
        x: (ev.clientX - rr.left - vv.x) / vv.zoom,
        y: (ev.clientY - rr.top  - vv.y) / vv.zoom,
      });
    };
    const onUp = ev => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      // Detect if we're over a port-in handle by checking data attributes
      const target = document.elementFromPoint(ev.clientX, ev.clientY);
      const portEl = target?.closest('[data-port-in]');
      if (portEl) {
        const tgtNodeId = portEl.dataset.nodeId;
        const tgtSlotId = portEl.dataset.slotId;
        const result = validateConnection({
          sourceNodeId: srcNode.id, sourceSlotId: srcSlot.id,
          targetNodeId: tgtNodeId,  targetSlotId: tgtSlotId,
          nodes: nodesRef.current, edges: edgesRef.current, nodeTypes: NODE_TYPES,
        });
        if (result.ok) {
          setEdges(es => [...es, {
            id: `e_${Date.now()}`,
            source: srcNode.id, sourceSlot: srcSlot.id,
            target: tgtNodeId,  targetSlot: tgtSlotId,
          }]);
        } else {
          setDropError(result.reason);
        }
      }
      setDrawEdge(null);
      setValidTargets(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [setEdges]);

  // ── Wheel zoom/scroll ──────────────────────────────────────────
  const onWheel = useCallback(e => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) > 40) {
      const rect = ref.current.getBoundingClientRect();
      const px = e.clientX - rect.left, py = e.clientY - rect.top;
      const dz = e.deltaY < 0 ? 1.08 : 1/1.08;
      setView(v => {
        const nz = Math.max(0.3, Math.min(1.8, v.zoom*dz));
        const r = nz/v.zoom;
        return { x: px-(px-v.x)*r, y: py-(py-v.y)*r, zoom: nz };
      });
    } else {
      setView(v => ({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY }));
    }
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [onWheel]);

  // ── Pan (click canvas background) ─────────────────────────────
  const isCanvasBg = e => e.target === ref.current || e.target.dataset?.canvasBg != null;
  const onPD = e => {
    if (!isCanvasBg(e)) return;
    setPanning(true);
    panRef.current = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y };
    e.currentTarget.setPointerCapture(e.pointerId);
    // Do NOT deselect here — deselect happens on click (tap without drag) only
  };
  const onPM = e => {
    if (panRef.current) {
      const p = panRef.current;
      const next = {
        ...viewRef.current,
        x: p.vx + (e.clientX - p.sx),
        y: p.vy + (e.clientY - p.sy),
      };
      viewRef.current = next;
      applyViewTransform(next);
    }
  };
  const onPU = () => {
    setPanning(false);
    panRef.current = null;
    setView({ ...viewRef.current });
  };

  // ── Node drag via window listeners ────────────────────────────
  const startDrag = useCallback((nodeId, e) => {
    const n = nodesRef.current.find(m => m.id === nodeId);
    if (!n) return;
    e.stopPropagation();
    nodeDraggedRef.current = false;
    const sx = e.clientX, sy = e.clientY;
    const ox = n.x, oy = n.y, id = n.id;
    let dragRaf = null;
    const onMove = ev => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!nodeDraggedRef.current && Math.sqrt(dx*dx + dy*dy) < 4) return;
      nodeDraggedRef.current = true;
      if (dragRaf) return;
      dragRaf = requestAnimationFrame(() => {
        dragRaf = null;
        const v = viewRef.current;
        setNodes(ns => ns.map(m =>
          m.id === id ? { ...m, x: ox + dx/v.zoom, y: oy + dy/v.zoom } : m
        ));
      });
    };
    const onUp = () => {
      if (dragRaf) cancelAnimationFrame(dragRaf);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [setNodes]);

  const handleNodeClick = useCallback((id) => {
    if (nodeDraggedRef.current) return;
    onSelect?.(id);
    onSelectEdge?.(null);
  }, [onSelect, onSelectEdge]);

  const handleNodePointerDown = useCallback((nodeId, e) => {
    startDrag(nodeId, e);
  }, [startDrag]);

  // ── Palette drag-and-drop ─────────────────────────────────────
  const onDragOver = e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; };
  const onDrop = useCallback(e => {
    e.preventDefault();
    const nodeType = e.dataTransfer.getData('nodeType');
    if (!nodeType || !NODE_TYPES[nodeType]) return;
    const nodeLabel  = e.dataTransfer.getData('nodeLabel');
    const nodeParams = e.dataTransfer.getData('nodeParams');
    const rect = ref.current.getBoundingClientRect();
    const wx = (e.clientX - rect.left - view.x) / view.zoom;
    const wy = (e.clientY - rect.top  - view.y) / view.zoom;
    const def = NODE_TYPES[nodeType];
    const id = `${nodeType}_${Date.now()}`;
    let params = { ...(NODE_DEFAULTS[nodeType] || {}) };
    // New schedule nodes default to the operator's global timezone (Settings),
    // not a hardcoded UTC — so scheduling reflects the app-wide timezone.
    if (nodeType === 'schedule' && (!params.tz || params.tz === 'UTC')) {
      params.tz = getDisplayTimezone();
    }
    try { if (nodeParams) Object.assign(params, JSON.parse(nodeParams)); } catch {}
    setNodes(ns => [...ns, {
      id, type: nodeType,
      x: Math.round(wx - (def.width || NODE_W) / 2),
      y: Math.round(wy - 40),
      name: nodeLabel || def.label, status: 'idle', params,
    }]);
    onSelect?.(id);
  }, [view, onSelect, setNodes]);

  const fitView   = () => setView({ x: 24, y: 24, zoom: 0.62 });
  const zoomIn    = () => setView(v => ({ ...v, zoom: Math.min(1.8, v.zoom*1.15) }));
  const zoomOut   = () => setView(v => ({ ...v, zoom: Math.max(0.3, v.zoom/1.15) }));
  const resetZoom = () => setView(v => ({ ...v, zoom: 1 }));

  const nodeById = useMemo(() => { const m = new Map(); nodes.forEach(n => m.set(n.id, n)); return m; }, [nodes]);
  const connectedNodeIds = useMemo(() => {
    const ids = new Set();
    for (const e of edges) {
      ids.add(e.source);
      ids.add(e.target);
    }
    return ids;
  }, [edges]);
  const edgePaths = useMemo(() => edges.map(e => {
    const src = nodeById.get(e.source), dst = nodeById.get(e.target);
    if (!src || !dst) return null;
    const from = handlePosition(src, e.sourceSlot), to = handlePosition(dst, e.targetSlot);
    const srcSlot = slotsForNode(NODE_TYPES, src).find(s => s.id === e.sourceSlot);
    return { ...e, from, to, portType: srcSlot?.portType || 'message', srcStatus: src.status, dstStatus: dst.status };
  }).filter(Boolean), [edges, nodeById]);

  // Ghost edge path while drawing
  const ghostPath = drawEdge
    ? { from: { x: drawEdge.fx, y: drawEdge.fy }, to: drawMouse }
    : null;

  return (
    <div ref={ref} data-canvas-bg
      onPointerDown={onPD} onPointerMove={onPM} onPointerUp={onPU} onPointerCancel={onPU}
      onDragOver={onDragOver} onDrop={onDrop}
      onClick={() => { onSelect?.(null); onSelectEdge?.(null); }}
      style={{ position: 'relative', width: '100%', height: '100%', background: '#f8fafc',
               overflow: 'hidden', cursor: drawEdge ? 'crosshair' : panning ? 'grabbing' : 'grab' }}>

      <DotGrid view={view} gridRef={dotGridRef} />

      {dropError && (
        <div style={{ position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
                       zIndex: 50, background: '#fef2f2', color: '#991b1b',
                       border: '1px solid #fecaca', borderRadius: 8,
                       padding: '6px 12px', fontSize: 12, fontWeight: 600,
                       boxShadow: '0 4px 12px rgba(0,0,0,0.08)', pointerEvents: 'none' }}>
          {dropError}
        </div>
      )}

      <div ref={transformLayerRef} data-canvas-bg style={{ position: 'absolute', left: 0, top: 0, width: '100%', height: '100%',
                                    transformOrigin: '0 0',
                                    transform: `translate(${view.x}px,${view.y}px) scale(${view.zoom})` }}>
        {/* SVG layer: real edges + ghost edge */}
        {/* SVG is always pointer-events:none so it never blocks canvas pan/drag.
            Individual edge <g> elements opt back in with pointerEvents:'auto'. */}
        <svg style={{ position: 'absolute', left: -2000, top: -2000, width: 6000, height: 4000,
                      pointerEvents: 'none', overflow: 'visible' }}>
          <g transform="translate(2000 2000)">
            {edgePaths.map(e => (
              <EdgePath key={e.id} edge={e}
                selected={selectedEdgeId === e.id}
                onSelect={onSelectEdge}
              />
            ))}
            {ghostPath && <GhostEdge from={ghostPath.from} to={ghostPath.to} />}
          </g>
        </svg>

        {/* Port-in hit targets (invisible, used for edge-drop detection) */}
        {nodes.map(n => {
          const def = NODE_TYPES[n.type];
          if (!def) return null;
          const slots = slotsForNode(NODE_TYPES, n);
          const w = n.width || def.width || NODE_W;
          return slots.filter(s => s.kind === 'port-in').map((s, i) => {
            const portY = rowOffset(slots, slots.indexOf(s)) + ROW_H / 2;
            const isValid = validTargets?.has(`${n.id}|${s.id}`);
            const isInvalid = !!drawEdge && validTargets && !isValid;
            const tp = PORT_TYPE[s.portType] || PORT_TYPE.message;
            return (
              <div key={`pi_${n.id}_${s.id}`}
                data-port-in="" data-node-id={n.id} data-slot-id={s.id} data-port-type={s.portType || 'message'}
                style={{ position: 'absolute', left: n.x - 10, top: n.y + portY - 10,
                         width: 20, height: 20, borderRadius: 999, zIndex: 5,
                         pointerEvents: drawEdge ? 'auto' : 'none',
                         cursor: drawEdge ? (isValid ? 'crosshair' : 'not-allowed') : 'crosshair',
                         background: isValid ? `${tp.color}26` : 'transparent',
                         boxShadow: isValid ? `0 0 0 2px ${tp.color}` : 'none',
                         opacity: isInvalid ? 0.25 : 1,
                         transition: 'background 120ms, box-shadow 120ms, opacity 120ms' }}
              />
            );
          });
        })}

        {nodes.map(n => (
          <WfNode key={n.id} node={n}
            selected={selectedId === n.id}
            dragging={false}
            drawingSourceSlot={drawEdge?.sourceNodeId === n.id ? drawEdge.sourceSlotId : null}
            onClick={handleNodeClick}
            onPointerDown={handleNodePointerDown}
            onDelete={onDelete}
            onPortPointerDown={startPortDrag}
            liveStatus={isLive ? 'running' : (nodeStatusMap[n.id] ?? (connectedNodeIds.has(n.id) ? 'success' : null))}
            gtz={gtz}
          />
        ))}
      </div>

      {/* Live execution chip — only shown when this workflow is actually running */}
      {isLive && (
        <div style={{ position: 'absolute', left: 16, top: 16, display: 'flex', alignItems: 'center',
                      gap: 8, padding: '6px 12px 6px 10px', background: '#fff',
                      border: '1px solid #fee2e2', borderRadius: 999,
                      boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
                      fontSize: 11.5, color: '#7f1d1d', fontWeight: 600 }}>
          <span style={{ position: 'relative', width: 7, height: 7, display: 'inline-block' }}>
            <span style={{ position: 'absolute', inset: -3, borderRadius: 999, background: '#dc2626',
                           opacity: 0.4, animation: 'wfPulseDot 1.6s ease-out infinite' }} />
            <span style={{ position: 'relative', display: 'block', width: 7, height: 7,
                           borderRadius: 999, background: '#dc2626' }} />
          </span>
          Live · Running
        </div>
      )}

      {/* Zoom toolbar */}
      <div style={{ position: 'absolute', left: 16, bottom: 16, display: 'flex', gap: 4,
                    padding: 4, background: '#fff', border: '1px solid #e2e8f0',
                    borderRadius: 10, boxShadow: '0 4px 12px rgba(15,23,42,0.06)' }}>
        <CanvasBtn onClick={zoomOut} icon="zoomOut" title="Zoom out" />
        <button onClick={resetZoom} style={{ padding: '0 10px', height: 30, background: 'transparent',
                                             border: 'none', fontSize: 12, color: '#475569', fontWeight: 600,
                                             fontFamily: "'JetBrains Mono', monospace", cursor: 'pointer' }}>
          {Math.round(view.zoom*100)}%
        </button>
        <CanvasBtn onClick={zoomIn}  icon="zoomIn"  title="Zoom in"  />
        <div style={{ width: 1, background: '#eef2f6', margin: '4px 2px' }} />
        <CanvasBtn onClick={fitView} icon="fit"     title="Fit view" />
      </div>

      <PortTypeLegend />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 8. NODE PALETTE  (from workflow-page.jsx)
// ─────────────────────────────────────────────────────────────────

const CAT_ICONS = { Inputs:'play', Models:'sparkle', Memory:'db',
                    Tools:'tool', Agents:'workflow', Logic:'funnel', Outputs:'send' };

function NodePalette({ collapsed, onToggle, search, setSearch }) {
  const categories = useMemo(() => {
    const g = {};
    Object.entries(NODE_TYPES).forEach(([k, t]) => {
      if (t.paletteHidden) return;
      (g[t.category] ??= []).push({ k, ...t });
    });
    return Object.entries(g).map(([cat, items]) => ({ cat, items }));
  }, []);

  const lc = search.toLowerCase();
  const filtered = categories.map(({ cat, items }) => ({
    cat, items: items.filter(i => !lc || i.label.toLowerCase().includes(lc) || i.desc.toLowerCase().includes(lc)),
  })).filter(c => c.items.length > 0);

  if (collapsed) {
    return (
      <aside style={{ background: '#fff', borderRight: '1px solid #eef2f6',
                      display: 'flex', flexDirection: 'column', alignItems: 'center',
                      padding: '12px 0', gap: 6, width: 52 }}>
        <button onClick={onToggle} style={{ width: 36, height: 36, borderRadius: 8,
                                            border: '1px solid #e2e8f0', background: '#fff',
                                            cursor: 'pointer', display: 'flex',
                                            alignItems: 'center', justifyContent: 'center' }}>
          <Icon name="chevronRight" size={14} color="#475569" />
        </button>
        {categories.map(({ cat }) => {
          const t = CAT_TINT[cat] || CAT_TINT.Tools;
          return (
            <div key={cat} title={cat} style={{ width: 36, height: 36, borderRadius: 8,
                                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                                background: t.bg, border: `1px solid ${t.border}`, marginTop: 4 }}>
              <Icon name={CAT_ICONS[cat] || 'workflow'} size={14} color={t.fg} strokeWidth={1.9} />
            </div>
          );
        })}
      </aside>
    );
  }

  return (
    <aside style={{ background: '#fff', borderRight: '1px solid #eef2f6',
                    display: 'flex', flexDirection: 'column', minHeight: 0, width: 244 }}>
      <div style={{ padding: '14px 14px 8px', display: 'flex',
                    alignItems: 'center', justifyContent: 'space-between', flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8',
                        letterSpacing: '0.06em', textTransform: 'uppercase' }}>Nodes</div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>Drag to canvas</div>
        </div>
        <button onClick={onToggle} style={{ width: 26, height: 26, borderRadius: 6,
                                            border: '1px solid #e2e8f0', background: '#fff',
                                            cursor: 'pointer', display: 'flex',
                                            alignItems: 'center', justifyContent: 'center' }}>
          <Icon name="chevronLeft" size={13} color="#475569" />
        </button>
      </div>
      <div style={{ padding: '0 14px 10px', flexShrink: 0 }}>
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center',
                      border: '1px solid #e2e8f0', borderRadius: 8, background: '#fafbfc', height: 32 }}>
          <Icon name="search" size={13} color="#94a3b8" style={{ marginLeft: 10 }} />
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search nodes…"
            style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent',
                     padding: '0 10px', fontSize: 12.5, color: '#0f172a' }} />
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 12px' }}>
        {filtered.map(({ cat, items }) => {
          const t = CAT_TINT[cat] || CAT_TINT.Tools;
          return (
            <div key={cat} style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '6px 8px 5px',
                            fontSize: 10.5, fontWeight: 700, color: t.fg,
                            letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                <span style={{ width: 18, height: 18, borderRadius: 5, background: t.bg,
                               border: `1px solid ${t.border}`, display: 'flex',
                               alignItems: 'center', justifyContent: 'center' }}>
                  <Icon name={CAT_ICONS[cat] || 'workflow'} size={11} color={t.fg} strokeWidth={2} />
                </span>
                {cat}
                <span style={{ marginLeft: 'auto', color: '#94a3b8', fontWeight: 600,
                               fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}>
                  {items.length}
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {items.map(it => <PaletteItem key={it.k} item={it} tint={t} />)}
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function PaletteItem({ item, tint }) {
  const [hover, setHover] = useState(false);
  return (
    <div draggable
      onDragStart={e => { e.dataTransfer.setData('nodeType', item.k); e.dataTransfer.effectAllowed = 'copy'; }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 8px',
               borderRadius: 7, background: hover ? '#fafbfc' : 'transparent',
               cursor: 'grab', userSelect: 'none' }}>
      <span style={{ width: 28, height: 28, borderRadius: 7, background: tint.bg,
                     border: `1px solid ${tint.border}`, display: 'flex',
                     alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <Icon name={item.icon} size={14} color={tint.fg} strokeWidth={1.9} />
      </span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, color: '#0f172a' }}>{item.label}</div>
        <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 1,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {item.desc}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// 9. RIGHT PANEL  (from workflow-page.jsx)
// ─────────────────────────────────────────────────────────────────

function ToggleSwitch({ on, onChange }) {
  return (
    <button onClick={() => onChange(!on)}
      style={{ width: 32, height: 18, borderRadius: 999, padding: 2, border: 'none',
               cursor: 'pointer', background: on ? '#059669' : '#cbd5e1',
               display: 'flex', alignItems: 'center', transition: 'background 150ms' }}>
      <span style={{ display: 'block', width: 14, height: 14, borderRadius: 999, background: '#fff',
                     transform: `translateX(${on ? 14 : 0}px)`,
                     transition: 'transform 150ms', boxShadow: '0 1px 2px rgba(15,23,42,0.18)' }} />
    </button>
  );
}

// Two-state segmented slider (e.g. code-search backend). options: [{label,value}].
function SegmentSwitch({ value, options, onChange }) {
  const opts = options || [];
  const active = value != null && value !== '' ? value : (opts[0] && opts[0].value);
  return (
    <div style={{ display: 'flex', gap: 3, padding: 3, borderRadius: 8,
                  background: '#f1f5f9', border: '1px solid #e2e8f0' }}>
      {opts.map(o => {
        const on = o.value === active;
        return (
          <button key={o.value} onClick={() => onChange?.(o.value)}
            style={{ flex: 1, padding: '6px 0', borderRadius: 6, border: 'none', cursor: 'pointer',
                     fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
                     background: on ? '#fff' : 'transparent',
                     color: on ? '#2563eb' : '#64748b',
                     boxShadow: on ? '0 1px 2px rgba(15,23,42,0.10)' : 'none',
                     transition: 'background 120ms, color 120ms' }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function RTab({ id, active, onClick, label, icon, badge }) {
  return (
    <button onClick={() => onClick(id)}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
               padding: '8px 12px 11px', borderRadius: '6px 6px 0 0',
               background: 'transparent', cursor: 'pointer', border: 'none',
               borderBottom: active ? '2px solid #dc2626' : '2px solid transparent',
               marginBottom: -1, color: active ? '#0f172a' : '#64748b',
               fontSize: 12.5, fontWeight: active ? 600 : 500 }}>
      <Icon name={icon} size={13} color={active ? '#0f172a' : '#94a3b8'} />
      {label}
      {badge != null && (
        <span style={{ fontSize: 10, fontWeight: 600, background: '#f1f5f9', color: '#64748b',
                       padding: '1px 5px', borderRadius: 4,
                       fontFamily: "'JetBrains Mono', monospace" }}>{badge}</span>
      )}
    </button>
  );
}

function SHdr({ children, style }) {
  return <div style={{ fontSize: 10, fontWeight: 700, color: '#94a3b8',
                       letterSpacing: '0.06em', textTransform: 'uppercase', ...style }}>{children}</div>;
}

function humanize(k) {
  if (k === 'tz') return 'Timezone';
  return k.charAt(0).toUpperCase() + k.slice(1).replace(/([A-Z])/g, ' $1');
}

// File picker — mirrors NodeConfigPanel.jsx orchestrator pattern (visible input, file.text())
function FileSelect({ value, onChange }) {
  const fileInputRef = useRef(null);

  const handleFile = async e => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const content = await file.text();
      onChange(file.name, content);
    } catch (err) {
      alert('Error reading file: ' + err.message);
    }
  };

  const handleClear = e => {
    e.stopPropagation();
    e.preventDefault();
    if (fileInputRef.current) fileInputRef.current.value = '';
    onChange('', '');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <input
        ref={fileInputRef}
        type="file"
        accept=".sql"
        onChange={handleFile}
        style={{ fontSize: 12, color: '#475569', cursor: 'pointer', width: '100%' }}
      />
      {value && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px',
                      background: '#f0f9ff', border: '1px solid #bae6fd',
                      borderRadius: 6, fontSize: 12 }}>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", color: '#0369a1',
                         flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            📄 {value}
          </span>
          <button onClick={handleClear}
            style={{ background: 'none', border: 'none', cursor: 'pointer',
                     padding: 2, display: 'flex', color: '#94a3b8' }}>
            <Icon name="x" size={12} color="#94a3b8" />
          </button>
        </div>
      )}
    </div>
  );
}

// Weekday pill toggler for schedule nodes
const WEEKDAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
function WeekdaySelect({ value, onChange }) {
  const active = useMemo(() => value ? value.split(',').filter(Boolean) : [], [value]);
  const toggle = d => {
    const next = active.includes(d) ? active.filter(x => x !== d) : [...active, d];
    // preserve Mon→Sun order
    onChange(WEEKDAYS.filter(x => next.includes(x)).join(','));
  };
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {WEEKDAYS.map(d => {
        const on = active.includes(d);
        return (
          <button key={d} onClick={() => toggle(d)}
            style={{ flex: 1, padding: '5px 0', borderRadius: 6, fontSize: 10.5, fontWeight: 700,
                     cursor: 'pointer', border: `1.5px solid ${on ? '#dc2626' : '#e2e8f0'}`,
                     background: on ? '#dc2626' : '#fff', color: on ? '#fff' : '#94a3b8',
                     transition: 'all 100ms' }}>
            {d[0]}
          </button>
        );
      })}
    </div>
  );
}

// Compact human-readable byte size for log-group hints (e.g. 1.4 MB).
function formatStoredBytes(bytes) {
  if (bytes == null || Number.isNaN(bytes)) return null;
  if (bytes === 0) return 'empty';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const v = bytes / Math.pow(1024, i);
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

// Chip editor component for logs/list configurations
function ChipEditor({ value, onChange, action, actionContext }) {
  const [inputValue, setInputValue] = React.useState('');
  const [discovering, setDiscovering] = React.useState(false);
  const [discovered, setDiscovered] = React.useState([]);
  const [discoverPrefix, setDiscoverPrefix] = React.useState('');
  const [showDiscover, setShowDiscover] = React.useState(false);
  const [discoverError, setDiscoverError] = React.useState(null);
  const [searched, setSearched] = React.useState(false);
  const [truncated, setTruncated] = React.useState(false);

  const chips = React.useMemo(() => {
    return value ? value.split(',').map(s => s.trim()).filter(Boolean) : [];
  }, [value]);

  const handleAdd = (val) => {
    const trimmed = val.trim();
    if (!trimmed) return;
    if (chips.includes(trimmed)) {
      setInputValue('');
      return;
    }
    const next = [...chips, trimmed];
    onChange(next.join(', '));
    setInputValue('');
  };

  const handleRemove = (chipToRemove) => {
    const next = chips.filter(c => c !== chipToRemove);
    onChange(next.join(', '));
  };

  const region = actionContext.awsRegion || actionContext.region || 'us-east-1';

  const handleDiscover = async () => {
    if (action !== 'discoverCloudWatchLogGroups') return;
    setDiscovering(true);
    setDiscoverError(null);
    setDiscovered([]);
    try {
      const profile = actionContext.awsProfile || actionContext.profile || undefined;
      const result = await agentApiClient.discoverCloudWatchLogGroups(
        discoverPrefix || undefined,
        region,
        200,
        profile
      );
      // Keep the full metadata (size, retention) so the picker can show it.
      setDiscovered((result?.log_groups || []).filter(g => g && g.name));
      setTruncated(Boolean(result?.truncated));
      setSearched(true);
    } catch (err) {
      console.error('Failed to discover log groups:', err);
      setDiscovered([]);
      setSearched(true);
      setDiscoverError(
        err?.response?.data?.detail
        || err?.message
        || 'Could not reach AWS. Check the region, profile, and credentials.'
      );
    } finally {
      setDiscovering(false);
    }
  };

  const handleAddAll = () => {
    const names = discovered.map(g => g.name).filter(Boolean);
    const merged = Array.from(new Set([...chips, ...names]));
    onChange(merged.join(', '));
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 4,
        padding: chips.length ? '4px 0' : '0',
        maxHeight: 160, overflowY: 'auto',
      }}>
        {chips.map(chip => (
          <div key={chip}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              width: '100%', boxSizing: 'border-box', minWidth: 0,
              padding: '5px 8px', background: '#eff6ff', color: '#1e40af',
              border: '1px solid #bfdbfe', borderRadius: 6, fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
            }}>
            <span
              title={chip}
              style={{
                flex: 1, minWidth: 0,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {chip}
            </span>
            <button
              type="button"
              onClick={() => handleRemove(chip)}
              aria-label={`Remove ${chip}`}
              style={{
                background: 'none', border: 'none', color: '#64748b',
                cursor: 'pointer', padding: 0, fontSize: 14, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
                flexShrink: 0, width: 16, height: 16, lineHeight: 1,
              }}
              onMouseEnter={e => { e.currentTarget.style.color = '#ef4444'; }}
              onMouseLeave={e => { e.currentTarget.style.color = '#64748b'; }}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <input
          type="text"
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              e.preventDefault();
              handleAdd(inputValue);
            }
          }}
          placeholder="Add log group…"
          style={{
            width: '100%', boxSizing: 'border-box',
            border: '1px solid #e2e8f0', borderRadius: 6,
            padding: '6px 10px', fontSize: 12, outline: 'none',
            background: '#fafbfc', fontFamily: "'JetBrains Mono', monospace",
          }}
        />
        {action === 'discoverCloudWatchLogGroups' && (
          <button
            type="button"
            onClick={() => setShowDiscover(!showDiscover)}
            style={{
              alignSelf: 'flex-start',
              padding: '6px 10px', background: '#f1f5f9', border: '1px solid #cbd5e1',
              borderRadius: 6, fontSize: 11, color: '#475569', cursor: 'pointer',
              fontWeight: 600, transition: 'all 120ms', whiteSpace: 'nowrap',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = '#e2e8f0'; }}
            onMouseLeave={e => { e.currentTarget.style.background = '#f1f5f9'; }}
          >
            {showDiscover ? 'Hide discovery' : '🔍 Discover from AWS'}
          </button>
        )}
      </div>

      {showDiscover && (
        <div style={{
          background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8,
          padding: 8, display: 'flex', flexDirection: 'column', gap: 6
        }}>
          <div style={{ display: 'flex', gap: 4, alignItems: 'stretch' }}>
            <input
              type="text"
              value={discoverPrefix}
              onChange={e => setDiscoverPrefix(e.target.value)}
              placeholder={`Filter in ${region} (blank = all)`}
              style={{
                flex: 1, minWidth: 0, boxSizing: 'border-box',
                border: '1px solid #cbd5e1', borderRadius: 6,
                padding: '4px 8px', fontSize: 11, outline: 'none',
                background: '#fff',
              }}
              onKeyDown={e => { if (e.key === 'Enter' && !discovering) handleDiscover(); }}
            />
            <button
              type="button"
              onClick={handleDiscover}
              disabled={discovering}
              style={{
                padding: '4px 12px', background: discovering ? '#93c5fd' : '#3b82f6',
                border: 'none', borderRadius: 6, fontSize: 11, color: '#fff',
                cursor: discovering ? 'wait' : 'pointer', fontWeight: 600,
                whiteSpace: 'nowrap', flexShrink: 0,
              }}
            >
              {discovering ? 'Scanning…' : 'Scan'}
            </button>
          </div>

          {/* Error */}
          {discoverError && (
            <div style={{
              padding: '6px 8px', fontSize: 11, color: '#b91c1c',
              background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 6
            }}>
              ⚠ {discoverError}
            </div>
          )}

          {/* Initial hint — before any scan */}
          {!searched && !discovering && !discoverError && (
            <div style={{ textAlign: 'center', padding: '8px 4px', fontSize: 11, color: '#94a3b8', lineHeight: 1.5 }}>
              Type part of a name (or leave blank) and click <strong>Scan</strong> to
              list CloudWatch log groups from AWS.
            </div>
          )}

          {/* Searched, nothing found */}
          {searched && !discovering && !discoverError && discovered.length === 0 && (
            <div style={{ textAlign: 'center', padding: '8px 4px', fontSize: 11, color: '#94a3b8' }}>
              No log groups found{discoverPrefix ? ` matching “${discoverPrefix}”` : ''} in {region}.
            </div>
          )}

          {/* Results */}
          {discovered.length > 0 && (
            <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, background: '#fff', overflow: 'hidden' }}>
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '4px 8px', fontSize: 10.5, color: '#64748b',
                background: '#f8fafc', borderBottom: '1px solid #f1f5f9'
              }}>
                <span>
                  {discovered.length}{truncated ? '+' : ''} found — click to add
                  {truncated && <em style={{ color: '#b45309', fontStyle: 'normal' }}> (refine to see more)</em>}
                </span>
                <button
                  type="button"
                  onClick={handleAddAll}
                  style={{
                    background: 'none', border: 'none', color: '#3b82f6',
                    cursor: 'pointer', fontSize: 10.5, fontWeight: 700, padding: 0
                  }}
                >
                  + Add all
                </button>
              </div>
              <div style={{ maxHeight: 160, overflowY: 'auto' }}>
                {discovered.map(group => {
                  const isAdded = chips.includes(group.name);
                  const size = formatStoredBytes(group.stored_bytes);
                  const retention = group.retention_days ? `${group.retention_days}d` : 'no expiry';
                  const meta = [size, retention].filter(Boolean).join(' · ');
                  return (
                    <div key={group.name}
                      onClick={() => !isAdded && handleAdd(group.name)}
                      title={isAdded ? 'Already added' : 'Click to add'}
                      style={{
                        padding: '5px 8px', fontSize: 11.5, cursor: isAdded ? 'default' : 'pointer',
                        borderBottom: '1px solid #f1f5f9', display: 'flex',
                        justifyContent: 'space-between', alignItems: 'center', gap: 8,
                        background: isAdded ? '#f8fafc' : 'transparent',
                        color: isAdded ? '#94a3b8' : '#334155'
                      }}
                      onMouseEnter={e => { if (!isAdded) e.currentTarget.style.background = '#eff6ff'; }}
                      onMouseLeave={e => { if (!isAdded) e.currentTarget.style.background = 'transparent'; }}
                    >
                      <span style={{ overflow: 'hidden', minWidth: 0 }}>
                        <span style={{
                          display: 'block', overflow: 'hidden', textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap', fontFamily: "'JetBrains Mono', monospace"
                        }}>{group.name}</span>
                        {meta && (
                          <span style={{ display: 'block', fontSize: 9.5, color: '#94a3b8' }}>{meta}</span>
                        )}
                      </span>
                      <span style={{
                        flexShrink: 0, fontSize: 9, fontWeight: 700,
                        color: isAdded ? '#10b981' : '#3b82f6'
                      }}>
                        {isAdded ? '✓ ADDED' : '+ ADD'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Multi-select dropdown for db-select slots
// Uses position:fixed so the dropdown escapes overflowY:auto panel clipping
function MultiDbSelect({ value, options, onChange }) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);
  const dropRef    = useRef(null);
  const [dropPos, setDropPos] = useState({ top: 0, left: 0, width: 0 });

  const selected = useMemo(() => value ? value.split(',').filter(Boolean) : [], [value]);

  const openDropdown = () => {
    if (!triggerRef.current) return;
    const r = triggerRef.current.getBoundingClientRect();
    setDropPos({ top: r.bottom + 6, left: r.left, width: r.width });
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const handler = e => {
      if (dropRef.current && !dropRef.current.contains(e.target) &&
          triggerRef.current && !triggerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = opt => {
    const next = selected.includes(opt)
      ? selected.filter(s => s !== opt)
      : [...selected, opt];
    onChange(next.join(','));
  };

  const label = selected.length === 0 ? '— select —'
    : selected.length === 1 ? selected[0]
    : `${selected.length} servers selected`;

  return (
    <div>
      {/* Trigger */}
      <div ref={triggerRef} onClick={() => open ? setOpen(false) : openDropdown()}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                 cursor: 'pointer', fontSize: 12, color: selected.length ? '#0f172a' : '#94a3b8',
                 userSelect: 'none' }}>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                       paddingRight: 6 }}>{label}</span>
        <Icon name="chevronDown" size={13} color="#94a3b8"
              style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 140ms', flexShrink: 0 }} />
      </div>

      {/* Dropdown — fixed so it escapes scroll containers */}
      {open && (
        <div ref={dropRef}
          style={{ position: 'fixed', top: dropPos.top, left: dropPos.left, width: dropPos.width,
                   zIndex: 9999, background: '#fff', border: '1px solid #e2e8f0', borderRadius: 8,
                   boxShadow: '0 8px 24px rgba(0,0,0,0.12)', overflow: 'hidden',
                   maxHeight: 260, overflowY: 'auto' }}>
          {options.length === 0 ? (
            <div style={{ padding: '10px 12px', fontSize: 12, color: '#94a3b8' }}>
              No MCP servers configured
            </div>
          ) : options.map(opt => {
            const checked = selected.includes(opt);
            return (
              <div key={opt} onClick={() => toggle(opt)}
                style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '8px 12px',
                         cursor: 'pointer', fontSize: 12, fontWeight: 500,
                         color: checked ? '#0f172a' : '#334155',
                         background: checked ? '#fef2f2' : 'transparent' }}
                onMouseEnter={e => { if (!checked) e.currentTarget.style.background = '#f8fafc'; }}
                onMouseLeave={e => { e.currentTarget.style.background = checked ? '#fef2f2' : 'transparent'; }}>
                <span style={{ width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                               display: 'flex', alignItems: 'center', justifyContent: 'center',
                               border: `1.5px solid ${checked ? '#dc2626' : '#cbd5e1'}`,
                               background: checked ? '#dc2626' : '#fff' }}>
                  {checked && (
                    <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
                      <path d="M2 6l3 3 5-5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </span>
                <Icon name="db" size={11} color={checked ? '#dc2626' : '#94a3b8'} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{opt}</span>
              </div>
            );
          })}
          {selected.length > 0 && (
            <div style={{ borderTop: '1px solid #f1f5f9', padding: '6px 12px' }}>
              <button onClick={() => { onChange(''); setOpen(false); }}
                style={{ fontSize: 11, color: '#94a3b8', background: 'none', border: 'none',
                         cursor: 'pointer', padding: 0 }}>
                Clear all
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Multi-select dropdown for repo-select slots.
// Loads available repos from GET /code-analyzer/repos (filesystem scan under
// REPOS_BASE_PATH) on first open — these repos are available before any
// indexing has happened.  Indexing happens automatically on first run.
function MultiRepoSelect({ value, onChange }) {
  const [open, setOpen]         = useState(false);
  // Each entry: { name, path, is_git, detected_languages, suggested_language, file_count_sample }
  const [repos, setRepos]       = useState([]);
  const [loading, setLoading]   = useState(false);
  const [loadErr, setLoadErr]   = useState(null);
  const triggerRef = useRef(null);
  const dropRef    = useRef(null);
  const [dropPos, setDropPos]   = useState({ top: 0, left: 0, width: 0 });

  const selected = useMemo(
    () => (value ? value.split(',').filter(Boolean) : []),
    [value],
  );

  const loadRepos = async () => {
    if (repos.length > 0) return; // already loaded this session
    setLoading(true);
    setLoadErr(null);
    try {
      // Use filesystem discovery — always shows repos even before first index run
      const data = await agentApiClient.listCodeAnalyzerRepos();
      setRepos(Array.isArray(data?.repos) ? data.repos : []);
    } catch (e) {
      setLoadErr(e?.response?.data?.detail || e?.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  const openDropdown = async () => {
    if (!triggerRef.current) return;
    const r = triggerRef.current.getBoundingClientRect();
    setDropPos({ top: r.bottom + 6, left: r.left, width: Math.max(r.width, 220) });
    setOpen(true);
    await loadRepos();
  };

  useEffect(() => {
    if (!open) return;
    const handler = e => {
      if (dropRef.current && !dropRef.current.contains(e.target) &&
          triggerRef.current && !triggerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const toggle = name => {
    const next = selected.includes(name)
      ? selected.filter(s => s !== name)
      : [...selected, name];
    onChange(next.join(','));
  };

  const label = selected.length === 0 ? '— select repositories —'
    : selected.length === 1 ? selected[0]
    : `${selected.length} repositories selected`;

  return (
    <div>
      {/* Trigger */}
      <div ref={triggerRef}
        onClick={() => open ? setOpen(false) : openDropdown()}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                 cursor: 'pointer', fontSize: 12,
                 color: selected.length ? '#0f172a' : '#94a3b8',
                 userSelect: 'none' }}>
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                       whiteSpace: 'nowrap', paddingRight: 6 }}>{label}</span>
        <Icon name="chevronDown" size={13} color="#94a3b8"
              style={{ transform: open ? 'rotate(180deg)' : 'none',
                       transition: 'transform 140ms', flexShrink: 0 }} />
      </div>

      {/* Dropdown — fixed so it escapes scroll containers */}
      {open && (
        <div ref={dropRef}
          style={{ position: 'fixed', top: dropPos.top, left: dropPos.left,
                   width: dropPos.width, zIndex: 9999,
                   background: '#fff', border: '1px solid #e2e8f0', borderRadius: 8,
                   boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                   maxHeight: 280, overflowY: 'auto' }}>
          {loading ? (
            <div style={{ padding: '10px 12px', fontSize: 12, color: '#94a3b8' }}>
              Loading repositories…
            </div>
          ) : loadErr ? (
            <div style={{ padding: '10px 12px', fontSize: 12, color: '#b91c1c' }}>
              {loadErr}
            </div>
          ) : repos.length === 0 ? (
            <div style={{ padding: '10px 12px', fontSize: 12, color: '#94a3b8' }}>
              No repositories found under REPOS_BASE_PATH. Check your docker-compose volume mount.
            </div>
          ) : (
            repos.map(r => {
              const checked = selected.includes(r.name);
              return (
                <div key={r.path} onClick={() => toggle(r.name)}
                  style={{ display: 'flex', alignItems: 'center', gap: 9,
                           padding: '8px 12px', cursor: 'pointer',
                           background: checked ? '#eff6ff' : 'transparent' }}
                  onMouseEnter={e => { if (!checked) e.currentTarget.style.background = '#f8fafc'; }}
                  onMouseLeave={e => { e.currentTarget.style.background = checked ? '#eff6ff' : 'transparent'; }}>
                  {/* Checkbox */}
                  <span style={{ width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                                 display: 'flex', alignItems: 'center', justifyContent: 'center',
                                 border: `1.5px solid ${checked ? '#2563eb' : '#cbd5e1'}`,
                                 background: checked ? '#2563eb' : '#fff' }}>
                    {checked && (
                      <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
                        <path d="M2 6l3 3 5-5" stroke="#fff" strokeWidth="1.8"
                              strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    )}
                  </span>
                  <Icon name="search" size={11} color={checked ? '#2563eb' : '#94a3b8'} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <span style={{ fontSize: 12, fontWeight: 600,
                                    color: checked ? '#1e40af' : '#334155',
                                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.name}
                      </span>
                      {r.is_git && (
                        <span style={{ fontSize: 9, padding: '1px 4px', borderRadius: 3,
                                       background: '#f0fdf4', color: '#166534',
                                       border: '1px solid #bbf7d0', flexShrink: 0 }}>git</span>
                      )}
                    </div>
                    {r.detected_languages?.length > 0 && (
                      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 1 }}>
                        {r.detected_languages.slice(0, 3).join(', ')}
                        {r.file_count_sample != null ? ` · ${r.file_count_sample}+ files` : ''}
                      </div>
                    )}
                  </div>
                </div>
              );
            })
          )}
          {selected.length > 0 && (
            <div style={{ borderTop: '1px solid #f1f5f9', padding: '6px 12px' }}>
              <button onClick={() => { onChange(''); setOpen(false); }}
                style={{ fontSize: 11, color: '#94a3b8', background: 'none', border: 'none',
                         cursor: 'pointer', padding: 0 }}>
                Clear all
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
            function ParamRow({ k, label, v, slotKind, onChange, selectOptions, slotAccept, onFileChange,
                    slotAction, allParams, nodes, updateMultipleParams, slotPlaceholder }) {
  const [customKeys, setCustomKeys] = React.useState(() => new Set());
  const isCode      = slotKind === 'textarea';
  const isWeekday   = slotKind === 'weekday-select';
  const isFilePick  = slotKind === 'file-select';
  // 'llm-select'/'mcp-select' are multi: selecting N items yields N output ports
  // that can each be wired to a different consumer.
  const isMultiDb   = slotKind === 'db-select' || slotKind === 'llm-select' || slotKind === 'mcp-select';
  const isRepoSel   = slotKind === 'repo-select';
  const isSelect    = slotKind === 'select';
  const isToggle    = slotKind === 'toggle';
  const isSegment   = slotKind === 'segment';
  const isChips     = slotKind === 'chips';
  const isMono      = isCode || k === 'cron' || k === 'url';
  const displayLabel = label || humanize(k);
  const baseStyle = {
    width: '100%', boxSizing: 'border-box', border: 'none', outline: 'none',
    background: 'transparent', fontSize: 12, lineHeight: 1.5, resize: 'none',
    fontFamily: isMono ? "'JetBrains Mono', monospace" : 'inherit',
  };
  return (
    <div>
      <div style={{ marginBottom: 5 }}>
        <span style={{ fontSize: 10.5, fontWeight: 700, color: '#64748b',
                       letterSpacing: '0.04em', textTransform: 'uppercase' }}>{displayLabel}</span>
      </div>
      <div style={{ padding: isCode ? '9px 11px'
                          : (isWeekday || isFilePick || isToggle || isChips || isSegment) ? '0'
                          : '7px 11px',
                    background: isCode ? '#0f172a'
                                : (isWeekday || isFilePick || isToggle || isChips || isSegment) ? 'transparent'
                                : '#fafbfc',
                    border: (isWeekday || isFilePick || isToggle || isChips || isSegment) ? 'none'
                            : `1px solid ${isCode ? '#1e293b' : '#e2e8f0'}`,
                    borderRadius: 7 }}>
        {slotKind === 'routes-editor'
          ? (() => {
              const routes = v || {};
              const routesDesc = allParams.routes_description || {};
              const agentNodes = (nodes || []).filter(n => n.type === 'agent');
              const PREDEFINED_CATEGORIES = ['database', 'aws', 'network', 'billing', 'security', 'general', 'availability', 'available'];

              const handleAddRoute = () => {
                let newKey = PREDEFINED_CATEGORIES.find(cat => routes[cat] === undefined) || 'general';
                let idx = 1;
                while (routes[newKey] !== undefined) {
                  newKey = `general_${idx}`;
                  idx++;
                }
                const defaultAgentId = agentNodes[0]?.id || '';
                
                const newRoutes = { ...routes, [newKey]: defaultAgentId };
                const newDesc = { ...routesDesc, [newKey]: `Route description for ${newKey}` };
                
                updateMultipleParams?.({ routes: newRoutes, routes_description: newDesc });
              };

              const handleRemoveRoute = (keyToRemove) => {
                const newRoutes = { ...routes };
                delete newRoutes[keyToRemove];
                
                const newDesc = { ...routesDesc };
                delete newDesc[keyToRemove];
                
                if (customKeys.has(keyToRemove)) {
                  setCustomKeys(prev => {
                    const next = new Set(prev);
                    next.delete(keyToRemove);
                    return next;
                  });
                }
                
                updateMultipleParams?.({ routes: newRoutes, routes_description: newDesc });
              };

              const handleUpdateRouteKey = (oldKey, newKey) => {
                const trimmed = newKey.trim();
                if (!trimmed || trimmed === oldKey || routes[trimmed] !== undefined) return;
                
                const newRoutes = {};
                const newDesc = {};
                
                Object.entries(routes).forEach(([k, val]) => {
                  if (k === oldKey) {
                    newRoutes[trimmed] = val;
                    newDesc[trimmed] = routesDesc[oldKey] || '';
                  } else {
                    newRoutes[k] = val;
                    newDesc[k] = routesDesc[k] || '';
                  }
                });
                
                if (customKeys.has(oldKey)) {
                  setCustomKeys(prev => {
                    const next = new Set(prev);
                    next.delete(oldKey);
                    next.add(trimmed);
                    return next;
                  });
                }
                
                updateMultipleParams?.({ routes: newRoutes, routes_description: newDesc });
              };

              const handleUpdateRouteAgent = (key, agentId) => {
                const newRoutes = { ...routes, [key]: agentId };
                updateMultipleParams?.({ routes: newRoutes });
              };

              const handleUpdateRouteDesc = (key, desc) => {
                const newDesc = { ...routesDesc, [key]: desc };
                updateMultipleParams?.({ routes_description: newDesc });
              };

              return (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, background: '#f8fafc', padding: 10, borderRadius: 8, border: '1px solid #e2e8f0' }}>
                  {agentNodes.length === 0 ? (
                    <div style={{ fontSize: 11, color: '#b45309', background: '#fffbeb', border: '1px solid #fef3c7', padding: '6px 8px', borderRadius: 6 }}>
                      ⚠️ No Agent nodes found in this workflow. Please add Agent nodes first.
                    </div>
                  ) : (
                    Object.entries(routes).map(([key, agentId]) => {
                      const isCustom = !PREDEFINED_CATEGORIES.includes(key) || customKeys.has(key);
                      const otherKeys = Object.keys(routes).filter(k => k !== key);
                      const availableCategories = PREDEFINED_CATEGORIES.filter(cat => !otherKeys.includes(cat));
                      return (
                        <div key={key} style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 6, padding: 8, display: 'flex', flexDirection: 'column', gap: 6, position: 'relative' }}>
                          <button
                            onClick={() => handleRemoveRoute(key)}
                            style={{
                              position: 'absolute', right: 4, top: 4, background: 'transparent', border: 'none',
                              color: '#94a3b8', cursor: 'pointer', fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
                              width: 20, height: 20, borderRadius: 4
                            }}
                            onMouseEnter={e => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.background = '#fee2e2'; }}
                            onMouseLeave={e => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.background = 'transparent'; }}
                            title="Remove route"
                          >
                            ×
                          </button>
                          
                          <div style={{ display: 'flex', gap: 6 }}>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: 9.5, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', marginBottom: 2 }}>Category</div>
                              {isCustom ? (
                                <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
                                  <input
                                    type="text"
                                    defaultValue={key}
                                    onBlur={e => handleUpdateRouteKey(key, e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && e.target.blur()}
                                    style={{
                                      flex: 1, border: '1px solid #e2e8f0', borderRadius: 4, padding: '3px 6px',
                                      fontSize: 11.5, color: '#0f172a', outline: 'none', background: '#fafbfc',
                                      fontFamily: "'JetBrains Mono', monospace"
                                    }}
                                    placeholder="Enter custom category"
                                  />
                                  {PREDEFINED_CATEGORIES.includes(key) && (
                                    <button
                                      type="button"
                                      onClick={() => setCustomKeys(prev => {
                                        const next = new Set(prev);
                                        next.delete(key);
                                        return next;
                                      })}
                                      style={{
                                        fontSize: 9, background: '#f1f5f9', border: '1px solid #cbd5e1', borderRadius: 4,
                                        cursor: 'pointer', padding: '4px 6px', color: '#475569', display: 'flex', alignItems: 'center'
                                      }}
                                      title="Switch to predefined dropdown"
                                    >
                                      ↩
                                    </button>
                                  )}
                                </div>
                              ) : (
                                <select
                                  value={key}
                                  onChange={e => {
                                    if (e.target.value === 'custom') {
                                      setCustomKeys(prev => {
                                        const next = new Set(prev);
                                        next.add(key);
                                        return next;
                                      });
                                    } else {
                                      handleUpdateRouteKey(key, e.target.value);
                                    }
                                  }}
                                  style={{
                                    width: '100%', border: '1px solid #e2e8f0', borderRadius: 4, padding: '3px 6px',
                                    fontSize: 11.5, color: '#0f172a', outline: 'none', background: '#fafbfc', cursor: 'pointer'
                                  }}
                                >
                                  {availableCategories.map(cat => (
                                    <option key={cat} value={cat}>
                                      {cat.charAt(0).toUpperCase() + cat.slice(1)}
                                    </option>
                                  ))}
                                  <option value="custom">— Custom Category —</option>
                                </select>
                              )}
                            </div>
                            
                            <div style={{ flex: 1.2, minWidth: 0 }}>
                              <div style={{ fontSize: 9.5, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', marginBottom: 2 }}>Target Agent</div>
                              <select
                                value={agentId}
                                onChange={e => handleUpdateRouteAgent(key, e.target.value)}
                                style={{
                                  width: '100%', border: '1px solid #e2e8f0', borderRadius: 4, padding: '3px 6px',
                                  fontSize: 11.5, color: '#0f172a', outline: 'none', background: '#fafbfc', cursor: 'pointer'
                                }}
                              >
                                {agentNodes.map(n => (
                                  <option key={n.id} value={n.id}>
                                    {n.name} ({n.id})
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>

                          <div>
                            <div style={{ fontSize: 9.5, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', marginBottom: 2 }}>Description</div>
                            <input
                              type="text"
                              value={routesDesc[key] || ''}
                              onChange={e => handleUpdateRouteDesc(key, e.target.value)}
                              placeholder="e.g. Database queries and connection issues"
                              style={{
                                width: '100%', border: '1px solid #e2e8f0', borderRadius: 4, padding: '3px 6px',
                                fontSize: 11.5, color: '#0f172a', outline: 'none', background: '#fafbfc'
                              }}
                            />
                          </div>
                        </div>
                      );
                    })
                  )}
                  
                  {agentNodes.length > 0 && (
                    <button
                      onClick={handleAddRoute}
                      style={{
                        width: '100%', padding: '6px 12px', background: '#2563eb', color: '#fff', border: 'none',
                        borderRadius: 6, fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4
                      }}
                      onMouseEnter={e => { e.currentTarget.style.background = '#1d4ed8'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = '#2563eb'; }}
                    >
                      <Icon name="plus" size={12} color="#fff" />
                      Add Route
                    </button>
                  )}
                </div>
              );
            })()
          : isToggle
          ? <ToggleSwitch on={v === true || v === 'true'}
              onChange={next => onChange?.(k, String(!!next))} />
          : isSegment
          ? <SegmentSwitch value={String(v ?? '')} options={selectOptions || []}
              onChange={val => onChange?.(k, val)} />
          : isChips
          ? <ChipEditor value={String(v ?? '')}
              onChange={val => onChange?.(k, val)}
              action={slotAction}
              actionContext={allParams || {}} />
          : isFilePick
          ? <FileSelect value={String(v ?? '')} accept={slotAccept}
              onChange={(name, content) => onFileChange?.(name, content)} />
          : isWeekday
          ? <WeekdaySelect value={String(v ?? '')} onChange={val => onChange?.(k, val)} />
          : isCode
          ? <textarea rows={4} value={String(v ?? '')} onChange={e => onChange?.(k, e.target.value)}
              style={{ ...baseStyle, color: '#a5f3fc' }} />
          : isRepoSel
          ? <MultiRepoSelect value={String(v ?? '')}
              onChange={val => onChange?.(k, val)} />
          : isMultiDb
          ? <MultiDbSelect value={String(v ?? '')} options={selectOptions || []}
              onChange={val => onChange?.(k, val)} />
          : isSelect && selectOptions
          ? <div style={{ position: 'relative' }}>
              <select value={String(v ?? '')} onChange={e => onChange?.(k, e.target.value)}
                style={{ ...baseStyle, color: v ? '#0f172a' : '#94a3b8', cursor: 'pointer',
                         appearance: 'none', WebkitAppearance: 'none', paddingRight: 24 }}>
                <option value="">— select —</option>
                {selectOptions.map(opt => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
              <span style={{ position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)',
                             pointerEvents: 'none', display: 'flex', alignItems: 'center' }}>
                <Icon name="chevronDown" size={13} color="#94a3b8" />
              </span>
            </div>
          : <input type={k === 'pat' || k === 'token' || slotKind === 'password' ? 'password' : 'text'}
              value={String(v ?? '')} onChange={e => onChange?.(k, e.target.value)}
              placeholder={slotPlaceholder || '—'}
              style={{ ...baseStyle, color: v ? '#0f172a' : '#94a3b8' }} />
        }
      </div>
    </div>
  );
}

function PortRowR({ slot }) {
  const tp = PORT_TYPE[slot.portType] || PORT_TYPE.message;
  const isIn = slot.kind === 'port-in';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 9.5, fontWeight: 700, color: '#94a3b8',
                     letterSpacing: '0.06em', textTransform: 'uppercase',
                     fontFamily: "'JetBrains Mono', monospace", width: 26 }}>
        {isIn ? 'IN' : 'OUT'}
      </span>
      <span style={{ fontSize: 11.5, color: '#0f172a', fontWeight: 600, flex: 1 }}>{slot.label}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5,
                     padding: '2px 7px', borderRadius: 999,
                     background: `${tp.color}10`, border: `1px solid ${tp.color}33`,
                     fontSize: 10, fontWeight: 600, color: tp.color }}>
        <span style={{ width: 5, height: 5, borderRadius: 999, background: tp.color }} />
        {tp.label}
      </span>
    </div>
  );
}

function NodeProperties({ node, onUpdateNode, onDelete, llms, dbServers, allMcpServers, workflowName, edges, nodes, latestExecution }) {
  const def = NODE_TYPES[node.type];
  if (!def) return null;
  const tint = CAT_TINT[def.category] || CAT_TINT.Tools;
  const [editingName, setEditingName] = useState(false);
  const [nameVal, setNameVal] = useState(node.name);
  const [lastExecStatus, setLastExecStatus] = useState(null);
  useEffect(() => { setNameVal(node.name); }, [node.id, node.name]);

  const { isWorkflowRunning } = useWorkflowStatus();
  const isRunning = workflowName ? isWorkflowRunning(workflowName) : false;

  useEffect(() => {
    if (latestExecution?.results) {
      const nodeResult = latestExecution.results[node.id];
      setLastExecStatus(nodeResult?.status ?? null);
    }
  }, [latestExecution, node.id]);

  // A node is "connected" when it has at least one edge in the graph
  const isConnected = (edges || []).some(e => e.source === node.id || e.target === node.id);

  // Derive display status: live running > last execution result > has edges → connected > idle
  const displayStatus = isRunning
    ? 'running'
    : (lastExecStatus || (isConnected ? 'success' : 'idle'));

  const commitName = () => {
    setEditingName(false);
    const trimmed = nameVal.trim() || node.name;
    setNameVal(trimmed);
    onUpdateNode?.(node.id, { name: trimmed });
  };

  const updateParam = (k, v) => {
    onUpdateNode?.(node.id, { params: { ...node.params, [k]: v } });
  };

  const updateMultipleParams = (patchParams) => {
    onUpdateNode?.(node.id, { params: { ...node.params, ...patchParams } });
  };

  const statusMap = {
    running: { bg:'#eff6ff', fg:'#1d4ed8', border:'#bfdbfe', dot:'#2563eb', label:'Running', pulse:true  },
    success: { bg:'#ecfdf5', fg:'#065f46', border:'#a7f3d0', dot:'#059669', label:'Connected', pulse:false },
    failed:  { bg:'#fef2f2', fg:'#7f1d1d', border:'#fecaca', dot:'#dc2626', label:'Failed',  pulse:false },
    pending: { bg:'#fefce8', fg:'#713f12', border:'#fde68a', dot:'#ca8a04', label:'Pending', pulse:true  },
    idle:    { bg:'#f8fafc', fg:'#475569', border:'#e2e8f0', dot:'#94a3b8', label:'Idle',    pulse:false },
  };
  const s = statusMap[displayStatus] || statusMap.idle;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {/* Header */}
      <div style={{ padding: '14px 16px 12px', borderBottom: '1px solid #eef2f6', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <span style={{ width: 36, height: 36, borderRadius: 8, flexShrink: 0,
                         background: tint.bg, border: `1px solid ${tint.border}`,
                         display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Icon name={def.icon} size={17} color={tint.fg} strokeWidth={1.8} />
          </span>
          <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'flex-start', gap: 6 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, color: tint.fg,
                          letterSpacing: '0.05em', textTransform: 'uppercase' }}>{def.label}</div>
            {editingName
              ? <input autoFocus value={nameVal}
                  onChange={e => setNameVal(e.target.value)}
                  onBlur={commitName}
                  onKeyDown={e => { if (e.key === 'Enter') commitName(); if (e.key === 'Escape') { setEditingName(false); setNameVal(node.name); } }}
                  style={{ fontSize: 14, fontWeight: 700, color: '#0f172a', letterSpacing: '-0.01em',
                           marginTop: 2, border: '1.5px solid #dc2626', borderRadius: 6,
                           padding: '2px 6px', outline: 'none', width: '100%', boxSizing: 'border-box' }} />
              : <div onClick={() => setEditingName(true)}
                  title="Click to rename"
                  style={{ fontSize: 14.5, fontWeight: 700, color: '#0f172a', letterSpacing: '-0.01em',
                           marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                           cursor: 'text', borderRadius: 4, padding: '2px 0' }}>
                  {node.name}
                </div>
            }
            <div style={{ fontSize: 11, color: '#94a3b8',
                          fontFamily: "'JetBrains Mono', monospace", marginTop: 2 }}>{node.id}</div>
          </div>
          {/* Delete button */}
          <button
            onClick={() => onDelete?.(node.id)}
            title="Delete node"
            style={{ flexShrink: 0, width: 28, height: 28, borderRadius: 6,
                     display: 'flex', alignItems: 'center', justifyContent: 'center',
                     border: '1px solid #fecaca', background: '#fff5f5',
                     cursor: 'pointer', transition: 'background 120ms, border-color 120ms' }}
            onMouseEnter={e => { e.currentTarget.style.background = '#fee2e2'; e.currentTarget.style.borderColor = '#fca5a5'; }}
            onMouseLeave={e => { e.currentTarget.style.background = '#fff5f5'; e.currentTarget.style.borderColor = '#fecaca'; }}>
            <Icon name="trash" size={13} color="#dc2626" strokeWidth={2} />
          </button>
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                         padding: '3px 9px 3px 7px', borderRadius: 999,
                         background: s.bg, border: `1px solid ${s.border}`,
                         fontSize: 11.5, fontWeight: 600, color: s.fg }}>
            <span style={{ position: 'relative', width: 7, height: 7, display: 'inline-block' }}>
              {s.pulse && <span style={{ position: 'absolute', inset: -2, borderRadius: 999,
                                         background: s.dot, opacity: 0.4,
                                         animation: 'wfPulseDot 1.6s ease-out infinite' }} />}
              <span style={{ position: 'relative', display: 'block', width: 7, height: 7,
                             borderRadius: 999, background: s.dot }} />
            </span>
            {s.label}
          </span>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '14px 16px 16px' }}>
        <SHdr>{def.desc}</SHdr>

        {(() => {
          const EDITABLE_KINDS = new Set(['field', 'select', 'segment', 'textarea', 'llm-select', 'db-select', 'mcp-select', 'repo-select', 'weekday-select', 'file-select', 'toggle', 'chips', 'routes-editor']);
          const currentParams = node.params || {};
          const editableSlots = (def.slots || []).filter(s => {
            if (!EDITABLE_KINDS.has(s.kind)) return false;
            // showWhen: { paramKey: expectedValue } — hide slot unless all conditions match
            if (s.showWhen) {
              return Object.entries(s.showWhen).every(([k, v]) => String(currentParams[k] ?? '') === String(v));
            }
            return true;
          });
          if (editableSlots.length === 0) return null;

          // Build option lists for dynamic selects
          const llmOptions = Object.keys(llms || {});
          const dbOptions  = Object.keys(dbServers || {});
          const mcpOptions = Object.keys(allMcpServers || {});

          return (
            <>
              <SHdr style={{ marginTop: 18 }}>Parameters</SHdr>
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 12 }}>
                {editableSlots.map(slot => {
                  const selectOptions =
                    slot.kind === 'llm-select'  ? llmOptions :
                    slot.kind === 'db-select'   ? dbOptions  :
                    slot.kind === 'mcp-select'  ? mcpOptions :
                    slot.kind === 'select'      ? (slot.options || []) :
                    slot.kind === 'segment'     ? (slot.options || []) :
                    null;
                  return (
                    <ParamRow key={slot.id} k={slot.id} label={slot.label}
                      v={(node.params || {})[slot.id] ?? ''}
                      slotKind={slot.kind} onChange={updateParam}
                      selectOptions={selectOptions}
                      slotAccept={slot.accept}
                      slotAction={slot.action}
                      allParams={node.params || {}}
                      nodes={nodes}
                      updateMultipleParams={updateMultipleParams}
                      slotPlaceholder={slot.placeholder}
                      onFileChange={slot.kind === 'file-select'
                        ? (name, content) => onUpdateNode?.(node.id, {
                            params: { ...(node.params || {}), [slot.id]: name, sqlContent: content },
                          })
                        : undefined} />
                  );
                })}
              </div>
            </>
          );
        })()}

        <SHdr style={{ marginTop: 20 }}>Ports</SHdr>
        <div style={{ marginTop: 8, background: '#fafbfc', border: '1px solid #eef2f6',
                      borderRadius: 8, padding: '10px 12px',
                      display: 'flex', flexDirection: 'column', gap: 6 }}>
          {slotsForNode(NODE_TYPES, node).filter(s => s.kind === 'port-in' || s.kind === 'port-out')
            .map(s => <PortRowR key={s.id} slot={s} />)}
        </div>
      </div>
    </div>
  );
}

function EmptyProps() {
  return (
    <div style={{ padding: 28, textAlign: 'center', color: '#94a3b8' }}>
      <Icon name="settings" size={28} color="#cbd5e1" strokeWidth={1.4} style={{ margin: '0 auto' }} />
      <div style={{ fontSize: 13, marginTop: 12, fontWeight: 500, color: '#64748b' }}>Select a node</div>
      <div style={{ fontSize: 11.5, marginTop: 4, lineHeight: 1.5 }}>
        Click any node on the canvas to view its properties and connections.
      </div>
    </div>
  );
}

function fmtDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`;
}
function relative(iso) {
  const m = Math.floor((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m/60)}h ago`;
}

function RunDot({ status }) {
  const c = status === 'success' ? '#22c55e' : status === 'failed' ? '#ef4444'
          : status === 'running' ? '#2563eb' : '#94a3b8';
  const pulse = status === 'running';
  return (
    <span style={{ position: 'relative', width: 10, height: 10, display: 'inline-block' }}>
      {pulse && <span style={{ position: 'absolute', inset: -2, borderRadius: 999, background: c,
                               opacity: 0.4, animation: 'wfPulseDot 1.6s ease-out infinite' }} />}
      <span style={{ position: 'relative', display: 'block', width: 10, height: 10,
                     borderRadius: 999, background: c }} />
    </span>
  );
}

function ExecutionList({ workflowName, onCount, executions = [], loading = false, error = null }) {
  useEffect(() => {
    onCount?.(executions.length);
  }, [executions.length, onCount]);

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#94a3b8', fontSize: 12 }}>
        Loading…
      </div>
    );
  }
  if (error) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#ef4444', fontSize: 12 }}>
        {error}
      </div>
    );
  }
  if (!executions.length) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: '#94a3b8', fontSize: 12 }}>
        No executions yet
      </div>
    );
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '12px 0' }}>
      {executions.map((ex, i) => {
        const durationMs = ex.duration_ms;
        const startedAt  = ex.started_at || ex.start_time;
        const exId       = ex.execution_id || String(ex.id);
        const status     = ex.status;
        const errMsg     = ex.error;
        return (
          <div key={exId} style={{ display: 'grid', gridTemplateColumns: '14px 1fr auto',
                                    gap: 10, alignItems: 'center', padding: '10px 16px',
                                    borderBottom: i < executions.length - 1 ? '1px solid #eef2f6' : 'none',
                                    cursor: 'pointer' }}>
            <RunDot status={status} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#0f172a' }}>
                {relative(startedAt)}
                <span style={{ marginLeft: 6, fontSize: 10.5, color: '#94a3b8',
                               fontFamily: "'JetBrains Mono', monospace", fontWeight: 500 }}>
                  #{exId}
                </span>
              </div>
              <div style={{ fontSize: 10.5, color: '#64748b', marginTop: 2 }}>
                {status}
                {errMsg && <span style={{ color: '#dc2626' }}> · {errMsg}</span>}
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontSize: 11.5, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace",
                            color: status === 'failed' ? '#dc2626' : status === 'running' ? '#2563eb' : '#0f172a' }}>
                {durationMs ? fmtDuration(durationMs) : status === 'running' ? '· · ·' : '—'}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RPanel({ tab, setTab, node, onUpdateNode, onDelete, llms, dbServers, allMcpServers, workflowName, edges, nodes, latestExecution, executions, execLoading, execError }) {
  const [execCount, setExecCount] = useState(null);

  return (
    <aside style={{ background: '#fff', display: 'flex', flexDirection: 'column',
                    minHeight: 0, borderLeft: '1px solid #eef2f6', width: 320 }}>
      <div style={{ display: 'flex', padding: '10px 12px 0', gap: 4,
                    borderBottom: '1px solid #eef2f6', flexShrink: 0 }}>
        <RTab id="node" active={tab === 'node'} onClick={setTab}
              label={node ? node.name : 'Properties'} icon="settings" />
        <RTab id="executions" active={tab === 'executions'} onClick={setTab}
              label="Executions" icon="history" badge={execCount ?? undefined} />
      </div>
      {tab === 'node' && (node
        ? <NodeProperties node={node} onUpdateNode={onUpdateNode} onDelete={onDelete} llms={llms} dbServers={dbServers} allMcpServers={allMcpServers} workflowName={workflowName} edges={edges} nodes={nodes} latestExecution={latestExecution} />
        : <EmptyProps />)}
      {tab === 'executions' && (
        <ExecutionList workflowName={workflowName} onCount={setExecCount} executions={executions} loading={execLoading} error={execError} />
      )}
    </aside>
  );
}

// ─────────────────────────────────────────────────────────────────
// 10. KEYFRAME INJECTION
// ─────────────────────────────────────────────────────────────────

function injectKF() {
  if (document.getElementById('wf-keyframes')) return;
  const el = document.createElement('style');
  el.id = 'wf-keyframes';
  el.textContent = `
    @keyframes wfPulseDot {
      0%   { transform: scale(1);   opacity: .4 }
      70%  { transform: scale(2.5); opacity:  0 }
      100% { transform: scale(2.5); opacity:  0 }
    }
    @keyframes wfDashFlow {
      to { stroke-dashoffset: -16; }
    }
  `;
  document.head.appendChild(el);
}

// ─────────────────────────────────────────────────────────────────
// 11. MAIN EXPORT
// ─────────────────────────────────────────────────────────────────

function WorkflowSwitcher({ title, workflows, onSwitchWorkflow, onClearCanvas }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handler = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const others = (workflows || []).filter(w => w.name !== title);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px 5px 7px',
                 borderRadius: 8, background: open ? '#f8fafc' : '#fff',
                 border: `1px solid ${open ? '#dc262640' : '#e2e8f0'}`,
                 fontSize: 13, color: '#0f172a', fontWeight: 600, cursor: 'pointer' }}>
        <span style={{ width: 22, height: 22, borderRadius: 6, background: '#fef2f2',
                       border: '1px solid #fee2e2', display: 'flex',
                       alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <Icon name="workflow" size={12} color="#dc2626" strokeWidth={2} />
        </span>
        {title}
        <Icon name="chevronDown" size={13} color="#94a3b8"
              style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 140ms' }} />
      </button>

      {open && (
        <div style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 999,
                      background: '#fff', border: '1px solid #e2e8f0', borderRadius: 10,
                      boxShadow: '0 8px 24px rgba(0,0,0,0.10)', minWidth: 200, overflow: 'hidden' }}>
          {/* Current */}
          <div style={{ padding: '8px 12px 6px',
                        borderBottom: others.length ? '1px solid #f1f5f9' : 'none' }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#94a3b8',
                          letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 4 }}>
              Current
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 8px',
                          borderRadius: 6, background: '#fef2f2',
                          fontSize: 13, fontWeight: 600, color: '#dc2626' }}>
              <Icon name="workflow" size={12} color="#dc2626" strokeWidth={2} />
              {title}
            </div>
          </div>

          {/* Other workflows */}
          {others.length > 0 ? (
            <div style={{ padding: '6px 8px 0' }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: '#94a3b8',
                            letterSpacing: '0.06em', textTransform: 'uppercase',
                            padding: '4px 4px 6px' }}>
                Switch to
              </div>
              {others.map(w => (
                <button key={w.name}
                  onClick={() => { setOpen(false); onSwitchWorkflow?.(w); }}
                  style={{ display: 'flex', alignItems: 'center', gap: 7, width: '100%',
                           padding: '7px 8px', borderRadius: 6, fontSize: 13,
                           fontWeight: 500, color: '#0f172a', background: 'transparent',
                           border: 'none', cursor: 'pointer', textAlign: 'left' }}
                  onMouseEnter={e => e.currentTarget.style.background = '#f8fafc'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <Icon name="workflow" size={12} color="#64748b" strokeWidth={2} />
                  {w.name}
                </button>
              ))}
            </div>
          ) : (
            <div style={{ padding: '8px 16px 4px', fontSize: 12, color: '#94a3b8', textAlign: 'center' }}>
              No other workflows
            </div>
          )}

          {/* Clear canvas */}
          {onClearCanvas && (
            <div style={{ borderTop: '1px solid #f1f5f9', padding: '6px 8px 8px' }}>
              <button
                onClick={() => { setOpen(false); onClearCanvas(); }}
                style={{ display: 'flex', alignItems: 'center', gap: 7, width: '100%',
                         padding: '7px 8px', borderRadius: 6, fontSize: 13,
                         fontWeight: 500, color: '#dc2626', background: 'transparent',
                         border: 'none', cursor: 'pointer', textAlign: 'left' }}
                onMouseEnter={e => e.currentTarget.style.background = '#fef2f2'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                <Icon name="trash" size={12} color="#dc2626" strokeWidth={2} />
                Clear canvas
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const LangflowEditor = forwardRef(function LangflowEditor(
  { workflowName, initialNodes, initialEdges, initialEnabled, onSave, onCancel, onRun, workflows, onSwitchWorkflow }, ref
) {
  // Inject keyframes on every mount (survives HMR / page reload)
  useEffect(() => {
    // Remove stale tag first so HMR picks up changes
    document.getElementById('wf-keyframes')?.remove();
    injectKF();
  }, []);

  const [nodes, setNodes] = useState(() =>
    initialNodes?.length > 0
      ? initialNodes.map(normalizeNode)
      : SAMPLE_WORKFLOW.nodes
  );
  const [edges, setEdges] = useState(() =>
    initialEdges?.length > 0
      ? initialEdges.map(normalizeEdge)
      : SAMPLE_WORKFLOW.edges
  );

  const [selectedId, setSelectedId]         = useState(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [paletteCollapsed, setPaletteCollapsed] = useState(false);
  const [paletteSearch, setPaletteSearch]   = useState('');
  const [rightTab, setRightTab]             = useState('node');
  const [active, setActive]                 = useState(initialEnabled ?? true);

  const { data: workflowExecutions = [], isLoading: execLoading, isError: execIsError } =
    useWorkflowExecutionsQuery(workflowName, 20, { enabled: !!workflowName });
  const latestExecution = workflowExecutions[0] ?? null;
  const execError = execIsError ? 'Failed to load executions' : null;

  // Load LLMs and MCP servers from settings
  const [llms, setLlms]               = useState({});
  const [dbServers, setDbServers]     = useState({});
  const [allMcpServers, setAllMcpServers] = useState({});
  const [gtz, setGtz]             = useState(GLOBAL_TZ);
  const loadMcpServers = useCallback(() => {
    getMCPServers().then(all => {
      if (!all || !Object.keys(all).length) return; // don't overwrite good data with empty
      setAllMcpServers(all);
      setDbServers(all); // legacy database node shows all servers
    }).catch(() => {});
  }, []);

  useEffect(() => {
    getLLMs().then(data => setLlms(data || {})).catch(() => {});
    loadMcpServers();
    // Global timezone (Settings) — shown read-only on Schedule nodes.
    getAppSettings().then(s => {
      const tz = s?.global_timezone;
      if (tz) { _setGlobalTzCache(tz); setGtz(tz); }
    }).catch(() => {});
  }, [loadMcpServers]);

  const selectedNode = useMemo(() => nodes.find(n => n.id === selectedId), [nodes, selectedId]);
  const lintWarnings = useMemo(() => workflowWarnings(nodes, edges), [nodes, edges]);

  // Re-fetch MCP servers when an mcp_server node is selected and data is stale.
  useEffect(() => {
    if (selectedNode?.type === 'mcp_server' && !Object.keys(allMcpServers).length) {
      loadMcpServers();
    }
  }, [selectedNode, allMcpServers, loadMcpServers]);

  // Update a node's fields (name, params, etc.)
  // Deep-merges `params` so two successive param updates don't overwrite each other.
  const handleUpdateNode = useCallback((id, patch) => {
    setNodes(ns => {
      const oldNode = ns.find(n => n.id === id);
      const next = ns.map(n => {
        if (n.id !== id) return n;
        const merged = { ...n, ...patch };
        if (patch.params) merged.params = { ...(n.params || {}), ...patch.params };
        return merged;
      });
      const newNode = next.find(n => n.id === id);
      if (oldNode?.type === 'language_model' && patch.params &&
          ('llm' in patch.params || 'models' in patch.params)) {
        setEdges(es => reconcileModelEdges(es, id, oldNode, newNode));
      }
      if (oldNode?.type === 'mcp_server' && patch.params && 'servers' in patch.params) {
        setEdges(es => reconcileMcpEdges(es, id, oldNode, newNode));
      }
      return next;
    });
  }, [setEdges]);

  // Delete a node and its connected edges
  const handleDeleteNode = useCallback((id) => {
    setNodes(ns => ns.filter(n => n.id !== id));
    setEdges(es => es.filter(e => e.source !== id && e.target !== id));
    setSelectedId(prev => prev === id ? null : prev);
  }, []);

  useImperativeHandle(ref, () => ({
    getWorkflowData: () => ({ nodes, edges, enabled: active }),
  }), [nodes, edges, active]);

  const title = workflowName || SAMPLE_WORKFLOW.title;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#f8fafc' }}>

      {/* Sub-header */}
      <div style={{ height: 60, flexShrink: 0, background: '#fff', borderBottom: '1px solid #eef2f6',
                    display: 'flex', alignItems: 'center', padding: '0 20px', gap: 14 }}>
        {onCancel && (
          <button onClick={onCancel}
            style={{ display: 'flex', alignItems: 'center', gap: 5, padding: '5px 10px',
                     borderRadius: 8, background: 'transparent', border: '1px solid transparent',
                     cursor: 'pointer', fontSize: 13, color: '#64748b', fontWeight: 500 }}
            onMouseEnter={e => { e.currentTarget.style.background = '#f8fafc'; e.currentTarget.style.color = '#0f172a'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#64748b'; }}>
            <Icon name="chevronLeft" size={14} color="currentColor" />
            Workflows
          </button>
        )}
        {onCancel && <div style={{ width: 1, height: 20, background: '#e2e8f0' }} />}

        {/* Workflow switcher chip */}
        <WorkflowSwitcher title={title} workflows={workflows} onSwitchWorkflow={onSwitchWorkflow}
                          onClearCanvas={() => { setNodes([]); setEdges([]); setSelectedId(null); }} />

        <div style={{ flex: 1 }} />

        {/* Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 12px 0 4px',
                        border: '1px solid #e2e8f0', borderRadius: 8, height: 34 }}>
            <ToggleSwitch on={active} onChange={setActive} />
            <span style={{ fontSize: 12, color: '#0f172a', fontWeight: 600 }}>
              {active ? 'Active' : 'Inactive'}
            </span>
          </div>
          {onRun && (
            <button onClick={onRun}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 34,
                       padding: '0 16px', fontSize: 13, fontWeight: 600, borderRadius: 8,
                       cursor: 'pointer', background: '#059669', color: '#fff',
                       border: '1px solid #059669', boxShadow: '0 1px 2px rgba(5,150,105,0.18)' }}
              onMouseEnter={e => e.currentTarget.style.background = '#047857'}
              onMouseLeave={e => e.currentTarget.style.background = '#059669'}>
              <Icon name="play" size={13} color="#fff" /> Run
            </button>
          )}
          <button onClick={onSave}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 34,
                     padding: '0 16px', fontSize: 13, fontWeight: 600, borderRadius: 8,
                     cursor: 'pointer', background: '#dc2626', color: '#fff',
                     border: '1px solid #dc2626', boxShadow: '0 1px 2px rgba(220,38,38,0.18)' }}
            onMouseEnter={e => e.currentTarget.style.background = '#b91c1c'}
            onMouseLeave={e => e.currentTarget.style.background = '#dc2626'}>
            <Icon name="save" size={13} color="#fff" /> Save
          </button>
        </div>
      </div>

      {/* Non-blocking workflow lint (e.g. auto-learn needs a Memory node) */}
      {lintWarnings.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4,
                      padding: '8px 16px', background: '#fffbeb',
                      borderBottom: '1px solid #fde68a' }}>
          {lintWarnings.map((w, i) => (
            <button key={i} onClick={() => setSelectedId(w.nodeId)}
              style={{ textAlign: 'left', background: 'none', border: 'none', cursor: 'pointer',
                       fontSize: 12, color: '#92400e', padding: 0 }}>
              ⚠️ {w.message}
            </button>
          ))}
        </div>
      )}

      {/* 3-pane */}
      <div style={{ flex: 1, minHeight: 0, display: 'grid',
                    gridTemplateColumns: `${paletteCollapsed ? '52px' : '244px'} 1fr ${selectedNode ? '320px' : '0px'}`,
                    transition: 'grid-template-columns 180ms ease' }}>
        <NodePalette
          collapsed={paletteCollapsed}
          onToggle={() => setPaletteCollapsed(c => !c)}
          search={paletteSearch}
          setSearch={setPaletteSearch}
          llms={llms}
          dbServers={dbServers}
        />
        <div style={{ position: 'relative', minWidth: 0, minHeight: 0,
                      borderLeft: '1px solid #eef2f6', borderRight: '1px solid #eef2f6' }}>
          <WorkflowCanvas
            nodes={nodes} setNodes={setNodes}
            edges={edges} setEdges={setEdges}
            selectedId={selectedId} onSelect={setSelectedId}
            selectedEdgeId={selectedEdgeId} onSelectEdge={setSelectedEdgeId}
            onDelete={handleDeleteNode}
            workflowName={workflowName}
            gtz={gtz}
            latestExecution={latestExecution}
          />
        </div>
        {selectedNode && (
          <RPanel tab={rightTab} setTab={setRightTab} node={selectedNode} onUpdateNode={handleUpdateNode}
                  onDelete={handleDeleteNode} llms={llms} dbServers={dbServers} allMcpServers={allMcpServers} workflowName={workflowName} edges={edges} nodes={nodes}
                  latestExecution={latestExecution} executions={workflowExecutions} execLoading={execLoading} execError={execError} />
        )}
      </div>
    </div>
  );
});

export default LangflowEditor;
