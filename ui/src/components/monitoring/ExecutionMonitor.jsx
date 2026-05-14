import React, { useState, useMemo, useCallback } from 'react';
import PropTypes from 'prop-types';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Separator } from '@/components/ui/separator';
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { ChevronDown, ChevronUp, RefreshCw, Loader2, DollarSign, Wrench, Bot, Zap, Link2, GitBranch } from 'lucide-react';
import { useWorkflowStream } from '../../hooks/useWorkflowStream';
import StatusBadge from './StatusBadge';
import HITLPanel from '../workflow/HITLPanel';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

function fmtDuration(ms) {
    if (ms == null || ms < 0) return '';
    if (ms < 1000) return `${Math.round(ms)} ms`;
    return `${(ms / 1000).toFixed(2)} s`;
}

// LangChain emits internal run-IDs as span names, e.g.:
//   "llm-1777969787918-oekawqu33"  → "LLM Call"
//   "agent-1777961230648-k3b0e..."  → "Agent"
//   "orchestrator-1777961228280-37..." → "Orchestrator"
// Pattern: known-prefix + hyphen + long alphanumeric suffix
const SPAN_PREFIX_RE = /^(llm|agent|chain|retriever|tool|run|graph|react|orchestrator|router|executor|node|supervisor|planner|researcher|analyst)-([\w-]{6,})$/i;

const PREFIX_LABELS = {
    llm: 'LLM Call', agent: 'Agent', chain: 'Chain',
    retriever: 'Retriever', tool: 'Tool', graph: 'Graph',
    react: 'ReAct', orchestrator: 'Orchestrator', router: 'Router',
    executor: 'Executor', node: 'Node', supervisor: 'Supervisor',
    planner: 'Planner', researcher: 'Researcher', analyst: 'Analyst',
};

function cleanSpanLabel(raw) {
    if (!raw) return 'Span';
    const m = SPAN_PREFIX_RE.exec(raw);
    if (m) return PREFIX_LABELS[m[1].toLowerCase()] || m[1].charAt(0).toUpperCase() + m[1].slice(1);
    return raw.length > 28 ? `${raw.slice(0, 26)}…` : raw;
}

// Derive span kind from raw label for colour/icon selection
function deriveKind(rawLabel, hintKind) {
    if (hintKind && hintKind !== 'node') return hintKind;
    const lower = (rawLabel || '').toLowerCase();
    if (/^llm[-_]/.test(lower)   || lower === 'llm call')     return 'llm';
    if (/^agent[-_]/.test(lower) || lower === 'agent')        return 'agent';
    if (/^chain[-_]/.test(lower))                              return 'chain';
    if (/^graph[-_]/.test(lower) || lower === 'react')        return 'graph';
    if (/^tool[-_]/.test(lower))                               return 'tool';
    if (/^(orchestrator|router|supervisor|planner)[-_]/.test(lower)) return 'agent';
    return 'node';
}

// Span colour config
const KIND_STYLE = {
    node:    { bar: '#3b82f6', bg: '#eff6ff', text: '#1d4ed8', border: '#bfdbfe' },
    agent:   { bar: '#6366f1', bg: '#eef2ff', text: '#4338ca', border: '#c7d2fe' },
    llm:     { bar: '#8b5cf6', bg: '#f5f3ff', text: '#6d28d9', border: '#ddd6fe' },
    tool:    { bar: '#0d9488', bg: '#f0fdfa', text: '#0f766e', border: '#99f6e4' },
    chain:   { bar: '#0ea5e9', bg: '#f0f9ff', text: '#0369a1', border: '#bae6fd' },
    graph:   { bar: '#10b981', bg: '#ecfdf5', text: '#065f46', border: '#a7f3d0' },
    error:   { bar: '#ef4444', bg: '#fef2f2', text: '#b91c1c', border: '#fecaca' },
    default: { bar: '#94a3b8', bg: '#f8fafc', text: '#475569', border: '#e2e8f0' },
};

function getStyle(span) {
    if (span.failed) return KIND_STYLE.error;
    return KIND_STYLE[span.kind] || KIND_STYLE.default;
}

function KindIcon({ kind, sz = 12 }) {
    const props = { size: sz, className: 'shrink-0' };
    if (kind === 'tool')    return <Wrench {...props} />;
    if (kind === 'llm')     return <Zap    {...props} />;
    if (kind === 'agent')   return <Bot    {...props} />;
    if (kind === 'chain')   return <Link2  {...props} />;
    if (kind === 'graph')   return <GitBranch {...props} />;
    return <GitBranch {...props} />;
}

// ─────────────────────────────────────────────────────────────────────────────
// buildSpans
// ─────────────────────────────────────────────────────────────────────────────
function buildSpans(events, nowMs) {
    const spanMap  = new Map();
    const children = new Map();
    let toolSeq = 0;

    const upsert = (id, patch) =>
        spanMap.set(id, { ...(spanMap.get(id) || { id }), ...patch });

    const addChild = (parentId, childId) => {
        if (!children.has(parentId)) children.set(parentId, []);
        const list = children.get(parentId);
        if (!list.includes(childId)) list.push(childId);
    };

    for (const ev of events) {
        const ts   = ev.timestamp ? new Date(ev.timestamp).getTime() : null;
        const d    = ev.data || {};
        const type = ev.event_type || ev.type || '';

        if (type === 'node_started') {
            const id       = d.node_id   || d.node_name || 'unknown';
            const rawLabel = d.node_name || d.node_id   || 'unknown';
            const kind     = deriveKind(rawLabel, 'node');
            upsert(id, { rawLabel, label: cleanSpanLabel(rawLabel), kind, parentId: null, startMs: ts, endMs: null, failed: false });

        } else if (type === 'node_completed') {
            const id = d.node_id || d.node_name || 'unknown';
            upsert(id, { endMs: ts, failed: false });

        } else if (type === 'node_failed') {
            const id = d.node_id || d.node_name || 'unknown';
            upsert(id, { endMs: ts, failed: true });

        } else if (type === 'tool_call') {
            toolSeq++;
            const nodeId = d.node_id;
            // Prefer real tool name; fall back to tool_use_id, run_id, or seq counter
            const rawLabel = (d.tool && d.tool !== 'tool')
                ? d.tool
                : (d.tool_use_id || d.run_id || `Tool #${toolSeq}`);
            const toolId = `tool:${rawLabel}:${toolSeq}`;
            upsert(toolId, {
                rawLabel, label: cleanSpanLabel(rawLabel), kind: 'tool',
                parentId: nodeId || null, startMs: ts, endMs: null, failed: false,
                _toolName: d.tool, _nodeId: nodeId, _seq: toolSeq,
            });
            if (nodeId) addChild(nodeId, toolId);

        } else if (type === 'tool_result') {
            // Match by tool name + node + no endMs, prefer the latest open one
            let matched = null;
            for (const [id, span] of spanMap.entries()) {
                if (span.kind === 'tool' && span._toolName === d.tool &&
                    span._nodeId === d.node_id && span.endMs === null) {
                    if (!matched || span._seq > spanMap.get(matched)._seq) matched = id;
                }
            }
            if (matched) upsert(matched, { endMs: ts });
        }
    }

    if (spanMap.size === 0) return { spans: [], traceStart: 0, totalDuration: 1 };

    // ── Filter out unnamed/trivial root spans (grey dots) ──────────────────
    for (const [id, span] of spanMap.entries()) {
        if (!span.parentId && (!span.rawLabel || span.rawLabel === 'unknown')) {
            spanMap.delete(id);
        }
    }

    if (spanMap.size === 0) return { spans: [], traceStart: 0, totalDuration: 1 };

    let traceStart = Infinity;
    for (const s of spanMap.values()) {
        if (s.startMs && s.startMs < traceStart) traceStart = s.startMs;
    }
    if (!isFinite(traceStart)) return { spans: [], traceStart: 0, totalDuration: 1 };

    let traceEnd = traceStart;
    for (const s of spanMap.values()) {
        const end = s.endMs ?? nowMs;
        if (end > traceEnd) traceEnd = end;
    }
    const totalDuration = Math.max(traceEnd - traceStart, 1);

    const roots  = [...spanMap.values()].filter(s => !s.parentId);
    const flat   = [];

    const visit = (span, depth, parentStartMs) => {
        const startMs    = span.startMs ?? traceStart;
        const endMs      = span.endMs   ?? nowMs;
        const durationMs = endMs - startMs;
        // Clamp left offset within 0–100 and ensure minimum visible width
        const leftPct  = Math.max(0, Math.min(99, ((startMs - traceStart) / totalDuration) * 100));
        const widthPct = Math.max(0.4, Math.min(100 - leftPct, (durationMs / totalDuration) * 100));

        flat.push({ ...span, depth, durationMs, inProgress: span.endMs === null, leftPct, widthPct });

        for (const childId of (children.get(span.id) || [])) {
            const child = spanMap.get(childId);
            if (child) visit(child, depth + 1, startMs);
        }
    };

    for (const root of roots) visit(root, 0, traceStart);
    return { spans: flat, traceStart, totalDuration };
}

// ─────────────────────────────────────────────────────────────────────────────
// TimeRuler — tick marks across the Gantt header
// ─────────────────────────────────────────────────────────────────────────────
function TimeRuler({ totalDuration }) {
    // Pick a sensible tick interval
    const totalSec   = totalDuration / 1000;
    const rawInterval = totalSec / 5;
    const mag        = Math.pow(10, Math.floor(Math.log10(rawInterval || 1)));
    const nice       = [1, 2, 5, 10, 20, 30, 60].find(v => v * mag >= rawInterval) || 60;
    const intervalMs = nice * mag * 1000;

    const ticks = [];
    for (let t = 0; t <= totalDuration; t += intervalMs) {
        ticks.push(t);
    }

    return (
        <div className="relative w-full h-5">
            {ticks.map(t => {
                const pct = (t / totalDuration) * 100;
                if (pct > 100) return null;
                const label = t === 0 ? '0' : t < 1000 ? `${t}ms` : `${(t / 1000).toFixed(t % 1000 === 0 ? 0 : 1)}s`;
                return (
                    <span
                        key={t}
                        className="absolute text-[10px] text-slate-400 select-none"
                        style={{ left: `${pct}%`, transform: 'translateX(-50%)', top: 2 }}
                    >
                        {label}
                    </span>
                );
            })}
            {/* tick lines */}
            {ticks.map(t => {
                const pct = (t / totalDuration) * 100;
                if (pct > 100) return null;
                return (
                    <div
                        key={`line-${t}`}
                        className="absolute bottom-0 w-px bg-slate-200"
                        style={{ left: `${pct}%`, height: 4 }}
                    />
                );
            })}
        </div>
    );
}

TimeRuler.propTypes = { totalDuration: PropTypes.number.isRequired };

// ─────────────────────────────────────────────────────────────────────────────
// SpanRow
// ─────────────────────────────────────────────────────────────────────────────
const NAME_COL = 240;

function SpanRow({ span, collapsed, onToggle, hasChildren }) {
    const s = getStyle(span);
    const tooltip = `${span.rawLabel}  ·  ${span.inProgress ? 'in progress' : fmtDuration(span.durationMs)}  ·  ${span.kind}`;

    return (
        <div
            className="flex items-center border-b border-slate-100 last:border-0 hover:bg-slate-50/70 transition-colors"
            style={{ minHeight: 32 }}
            title={tooltip}
        >
            {/* ── Name column ──────────────────────────────────────── */}
            <div
                className="flex items-center gap-1 shrink-0 border-r border-slate-200 pr-1 overflow-hidden"
                style={{ width: NAME_COL, paddingLeft: 6 + span.depth * 14 }}
            >
                {/* chevron — only visible for parent spans */}
                {hasChildren ? (
                    <button
                        className="shrink-0 w-4 h-4 flex items-center justify-center text-slate-400 hover:text-slate-700 rounded"
                        onClick={(e) => { e.stopPropagation(); onToggle(); }}
                    >
                        {collapsed
                            ? <ChevronDown size={11} />
                            : <ChevronUp   size={11} />}
                    </button>
                ) : (
                    <span className="shrink-0 w-4" />
                )}

                {/* kind icon */}
                <span style={{ color: s.bar }} className="shrink-0">
                    <KindIcon kind={span.kind} sz={11} />
                </span>

                {/* label */}
                <span
                    className="text-[11px] font-medium truncate ml-1"
                    style={{ color: s.text }}
                >
                    {span.label}
                </span>

                {/* duration (right-aligned) */}
                <span className="ml-auto shrink-0 text-[10px] tabular-nums pr-2" style={{ color: s.text, opacity: 0.6 }}>
                    {span.inProgress
                        ? <Loader2 size={9} className="animate-spin" />
                        : span.durationMs > 0 ? fmtDuration(span.durationMs) : null}
                </span>
            </div>

            {/* ── Gantt column ─────────────────────────────────────── */}
            <div className="relative flex-1 overflow-hidden" style={{ height: 32 }}>
                {/* light track */}
                <div
                    className="absolute rounded"
                    style={{
                        left: `${span.leftPct}%`,
                        width: `${span.widthPct}%`,
                        height: 14, top: 9,
                        backgroundColor: s.bg,
                        border: `1px solid ${s.border}`,
                    }}
                />
                {/* filled bar */}
                <div
                    className={`absolute rounded ${span.inProgress ? 'animate-pulse' : ''}`}
                    style={{
                        left: `${span.leftPct}%`,
                        width: `${span.widthPct}%`,
                        height: 14, top: 9,
                        backgroundColor: s.bar,
                        opacity: span.inProgress ? 0.55 : 0.82,
                    }}
                />
            </div>
        </div>
    );
}

SpanRow.propTypes = {
    span:        PropTypes.object.isRequired,
    collapsed:   PropTypes.bool,
    onToggle:    PropTypes.func,
    hasChildren: PropTypes.bool,
};

// ─────────────────────────────────────────────────────────────────────────────
// SpanWaterfall
// ─────────────────────────────────────────────────────────────────────────────
function SpanWaterfall({ events, isRunning }) {
    const [collapsed, setCollapsed] = useState({});

    const { spans, totalDuration } = useMemo(
        () => buildSpans(events, Date.now()),
        [events], // eslint-disable-line react-hooks/exhaustive-deps
    );

    const toggle = useCallback((id) => {
        setCollapsed(prev => ({ ...prev, [id]: !prev[id] }));
    }, []);

    if (spans.length === 0) {
        return (
            <div className="flex items-center justify-center h-16 text-xs text-slate-400 italic rounded-lg border border-slate-200">
                Waiting for execution events…
            </div>
        );
    }

    // Build set of span IDs that have children
    const parentIds = new Set(spans.filter(s => s.depth > 0).map(s => s.parentId).filter(Boolean));

    // Filter spans to respect collapsed state
    const visibleSpans = [];
    const hiddenUnder  = new Set();

    for (const span of spans) {
        // Check if any ancestor is collapsed
        if (span.parentId && (hiddenUnder.has(span.parentId) || collapsed[span.parentId])) {
            hiddenUnder.add(span.id);
            continue;
        }
        visibleSpans.push(span);
    }

    // Compute total wall clock from root spans
    const rootTotalMs = spans
        .filter(s => s.depth === 0 && !s.inProgress)
        .reduce((acc, s) => acc + s.durationMs, 0);

    return (
        <div className="rounded-lg border border-slate-200 overflow-hidden bg-white text-sm select-none">
            {/* ── Header ───────────────────────────────────────────── */}
            <div className="flex items-stretch border-b border-slate-200 bg-slate-50">
                {/* name col header */}
                <div
                    className="shrink-0 border-r border-slate-200 flex items-center px-3 py-2"
                    style={{ width: NAME_COL }}
                >
                    <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Span</span>
                    {rootTotalMs > 0 && (
                        <span className="ml-auto text-[10px] text-slate-400">{fmtDuration(rootTotalMs)}</span>
                    )}
                </div>
                {/* time ruler */}
                <div className="flex-1 flex flex-col justify-end px-2 pb-1 pt-2">
                    <TimeRuler totalDuration={totalDuration} />
                </div>
            </div>

            {/* ── Rows ─────────────────────────────────────────────── */}
            <div>
                {visibleSpans.map(span => (
                    <SpanRow
                        key={span.id}
                        span={span}
                        collapsed={!!collapsed[span.id]}
                        onToggle={() => toggle(span.id)}
                        hasChildren={parentIds.has(span.id)}
                    />
                ))}
            </div>

            {/* ── Footer ───────────────────────────────────────────── */}
            {isRunning && (
                <div className="border-t border-slate-100 px-3 py-1.5 text-[10px] text-slate-400 flex items-center gap-1.5 bg-slate-50">
                    <span className="inline-block w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
                    Live — updating as events arrive
                </div>
            )}
        </div>
    );
}

SpanWaterfall.propTypes = {
    events:    PropTypes.array.isRequired,
    isRunning: PropTypes.bool,
};

// ─────────────────────────────────────────────────────────────────────────────
// ExecutionMonitor
// ─────────────────────────────────────────────────────────────────────────────
const ExecutionMonitor = ({ workflowName, executionId, autoStart = false, onClose }) => {
    const [expanded, setExpanded] = useState(true);
    const {
        status, events, error, isConnected, clearEvents,
        totalCost, agentCosts,
    } = useWorkflowStream(workflowName, autoStart);

    const connectionStatus = isConnected
        ? { text: 'Connected',     color: 'text-green-600' }
        : error
            ? { text: 'Error',     color: 'text-red-600' }
            : { text: 'Disconnected', color: 'text-gray-500' };

    const isRunning = status === 'running';

    return (
        <Card className="mb-4">
            <Collapsible open={expanded} onOpenChange={setExpanded}>

                {/* ── Header ─────────────────────────────────────────── */}
                <div
                    className="p-4 flex items-center justify-between cursor-pointer hover:bg-accent/50"
                    onClick={() => setExpanded(!expanded)}
                >
                    <div className="flex items-center gap-3 flex-1">
                        <h3 className="text-lg font-semibold">{workflowName}</h3>
                        <StatusBadge status={status} />
                        {isConnected && isRunning && (
                            <Loader2 className="w-5 h-5 animate-spin" />
                        )}
                    </div>

                    <div className="flex items-center gap-2">
                        {totalCost > 0 && (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground" title="Estimated LLM cost">
                                <DollarSign className="w-3 h-3" />
                                {totalCost < 0.001 ? '< $0.001' : `$${totalCost.toFixed(4)}`}
                            </span>
                        )}
                        {Object.entries(agentCosts).map(([nodeId, cost]) => (
                            <span key={nodeId} className="flex items-center gap-0.5 text-xs text-muted-foreground" title={`${nodeId} cost`}>
                                <DollarSign className="w-3 h-3" />
                                {nodeId}: {cost < 0.001 ? '< $0.001' : `$${cost.toFixed(4)}`}
                            </span>
                        ))}
                        <span className={`text-xs ${connectionStatus.color}`}>{connectionStatus.text}</span>
                        <Button size="icon" variant="ghost" onClick={(e) => { e.stopPropagation(); clearEvents(); }}>
                            <RefreshCw className="w-4 h-4" />
                        </Button>
                        <CollapsibleTrigger asChild>
                            <Button size="icon" variant="ghost">
                                {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                            </Button>
                        </CollapsibleTrigger>
                    </div>
                </div>

                {/* ── Body ───────────────────────────────────────────── */}
                <CollapsibleContent>
                    <Separator />
                    <CardContent className="pt-4 space-y-4">
                        {error && (
                            <Alert variant="destructive">
                                <AlertDescription>{error}</AlertDescription>
                            </Alert>
                        )}

                        {!isConnected && !error && (
                            <Alert>
                                <AlertDescription>Waiting for connection…</AlertDescription>
                            </Alert>
                        )}

                        {/* Trace waterfall */}
                        <SpanWaterfall events={events} isRunning={isRunning} />

                        {/* HITL approvals */}
                        <HITLPanel events={events} executionId={executionId} />
                    </CardContent>
                </CollapsibleContent>
            </Collapsible>
        </Card>
    );
};

ExecutionMonitor.propTypes = {
    workflowName: PropTypes.string.isRequired,
    executionId:  PropTypes.string,
    autoStart:    PropTypes.bool,
    onClose:      PropTypes.func,
};

export default ExecutionMonitor;
