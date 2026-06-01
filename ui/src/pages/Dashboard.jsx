import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  LayoutDashboard,
  Calendar,
  Clock,
  AlertTriangle,
  Zap,
  RefreshCw,
  Trash2,
  ChevronLeft,
  ChevronRight
} from 'lucide-react';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';
import { useScheduler } from '../context/SchedulerContext';
import agentApiClient from '../services/agentApiClient';
import { parseDate } from '../utils/workflowUtils';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const DAY_MS = 86400000;

/**
 * Strip Python-repr content-block artefact from LLM output stored before the
 * LangChain fix. Old records look like:
 *   [{'type': 'text', 'text': "actual answer here...", 'index': 0}]
 * The keys are single-quoted but the text VALUE may be double-quoted, so a
 * blanket single→double quote replacement breaks the JSON. We use targeted
 * regexes instead.
 * @param {string} text
 * @returns {string}
 */
function cleanLlmText(text) {
  if (!text || typeof text !== 'string') return text || '';
  const trimmed = text.trimStart();
  if (!trimmed.startsWith("[{")) return text;

  // Strategy 1: 'text': "double-quoted value"
  // Match everything between 'text': " and the last " that precedes ', or "}
  const dq = [...text.matchAll(/'text':\s*"([\s\S]*?)"\s*[,}]/g)];
  if (dq.length) return dq.map(m => m[1]).join('').replace(/\\n/g, '\n').replace(/\\'/g, "'").replace(/\\"/g, '"');

  // Strategy 2: 'text': 'single-quoted value'
  const sq = [...text.matchAll(/'text':\s*'([\s\S]*?)'\s*[,}]/g)];
  if (sq.length) return sq.map(m => m[1]).join('').replace(/\\n/g, '\n');

  // Strategy 3: after converting to JSON (works when text value has no quotes)
  try {
    const asJson = text
      .replace(/'/g, '"')
      .replace(/\bNone\b/g, 'null')
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false');
    const blocks = JSON.parse(asJson);
    if (Array.isArray(blocks)) {
      const parts = blocks.filter(b => b.type === 'text').map(b => b.text || '');
      if (parts.length) return parts.join('');
    }
  } catch { /* ignore */ }

  return text;
}

/**
 * Shared react-markdown component overrides — Tailwind-styled, no prose plugin required.
 * Used in both the CloudWatch Analysis and ReAct Final Answer panels.
 */
const mdComponents = {
  // eslint-disable-next-line no-unused-vars
  h1: ({node, ...p}) => <h1 className="text-xl font-bold text-slate-900 mt-4 mb-2" {...p} />,
  // eslint-disable-next-line no-unused-vars
  h2: ({node, ...p}) => <h2 className="text-lg font-bold text-slate-800 mt-4 mb-2 border-b border-slate-200 pb-1" {...p} />,
  // eslint-disable-next-line no-unused-vars
  h3: ({node, ...p}) => <h3 className="text-base font-semibold text-slate-800 mt-3 mb-1" {...p} />,
  // eslint-disable-next-line no-unused-vars
  p: ({node, ...p}) => <p className="text-sm text-slate-900 mb-3 leading-relaxed" {...p} />,
  // eslint-disable-next-line no-unused-vars
  strong: ({node, ...p}) => <strong className="font-semibold text-slate-900" {...p} />,
  // eslint-disable-next-line no-unused-vars
  em: ({node, ...p}) => <em className="italic text-slate-700" {...p} />,
  // eslint-disable-next-line no-unused-vars
  ul: ({node, ...p}) => <ul className="list-disc list-inside mb-3 space-y-1 text-sm text-slate-900" {...p} />,
  // eslint-disable-next-line no-unused-vars
  ol: ({node, ...p}) => <ol className="list-decimal list-inside mb-3 space-y-1 text-sm text-slate-900" {...p} />,
  // eslint-disable-next-line no-unused-vars
  li: ({node, ...p}) => <li className="text-sm text-slate-900 ml-2" {...p} />,
  // eslint-disable-next-line no-unused-vars
  code: ({node, inline, className, ...p}) => inline
    ? <code className="bg-slate-100 text-slate-800 px-1 py-0.5 rounded text-xs font-mono" {...p} />
    : <code className="block bg-slate-50 border border-slate-200 rounded p-3 text-xs font-mono overflow-auto whitespace-pre-wrap" {...p} />,
  // eslint-disable-next-line no-unused-vars
  pre: ({node, ...p}) => <pre className="mb-3" {...p} />,
  // eslint-disable-next-line no-unused-vars
  table: ({node, ...p}) => <div className="overflow-x-auto mb-4"><table className="min-w-full text-sm border-collapse border border-slate-200" {...p} /></div>,
  // eslint-disable-next-line no-unused-vars
  thead: ({node, ...p}) => <thead className="bg-slate-100" {...p} />,
  // eslint-disable-next-line no-unused-vars
  th: ({node, ...p}) => <th className="px-3 py-2 text-left font-semibold text-slate-700 border border-slate-200 text-xs" {...p} />,
  // eslint-disable-next-line no-unused-vars
  td: ({node, ...p}) => <td className="px-3 py-2 text-slate-900 border border-slate-200 text-xs" {...p} />,
  // eslint-disable-next-line no-unused-vars
  tr: ({node, ...p}) => <tr className="even:bg-slate-50" {...p} />,
  // eslint-disable-next-line no-unused-vars
  blockquote: ({node, ...p}) => <blockquote className="border-l-4 border-blue-300 pl-4 italic text-slate-600 mb-3 text-sm" {...p} />,
  // eslint-disable-next-line no-unused-vars
  hr: ({node, ...p}) => <hr className="border-slate-200 my-4" {...p} />,
};

/** @param {string} [s] "HH:mm" */
function parseHmToMinutes(s) {
  if (!s || typeof s !== 'string') return null;
  const [h, m] = s.split(':').map((x) => Number.parseInt(x, 10));
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
  return h * 60 + m;
}

/** Next local wall-clock occurrence of HH:mm (rolls to next day if passed). */
function nextScheduledLocalDate(startTimeStr) {
  const mins = parseHmToMinutes(startTimeStr?.trim?.() ?? '');
  if (mins == null) return null;
  const now = new Date();
  let t = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    Math.floor(mins / 60),
    mins % 60,
    0,
    0
  );
  if (t.getTime() <= now.getTime()) {
    t = new Date(t.getTime() + DAY_MS);
  }
  return t;
}

/** Enabled schedules sorted by upcoming next local occurrence. */
function sortSchedulesByNextRun(enabled) {
  return [...enabled].sort((a, b) => {
    const da = nextScheduledLocalDate(a.startTime);
    const db = nextScheduledLocalDate(b.startTime);
    if (!da && !db) {
      return String(a.startTime || '').localeCompare(String(b.startTime || ''));
    }
    if (!da) return 1;
    if (!db) return -1;
    return da.getTime() - db.getTime();
  });
}

/** Lines like mock: `tomorrow · in 5h 42m` */
function formatScheduledRelative(target) {
  if (!(target instanceof Date) || Number.isNaN(target.getTime())) return '';
  const now = new Date();
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  let dayLabel;
  if (sameDay(target, now)) dayLabel = 'today';
  else if (sameDay(target, tomorrow)) dayLabel = 'tomorrow';
  else {
    dayLabel = target.toLocaleDateString(undefined, { weekday: 'long' });
  }
  let ms = Math.max(0, target.getTime() - now.getTime());
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const inPart = h > 0 ? `in ${h}h ${m.toString().padStart(2, '0')}m` : `in ${m}m`;
  return `${dayLabel} · ${inPart}`;
}

/** Recent runs "Started" column — matches reference: Today · HH:mm */
function formatRunStartedCompact(iso) {
  const d = parseDate(iso);
  if (!d) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const now = new Date();
  const sameCal = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (sameCal(d, now)) return `Today · ${t}`;
  if (sameCal(d, y)) return `Yesterday · ${t}`;
  const datePart = d.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric'
  });
  return `${datePart} · ${t}`;
}

/** Tool / log-query count for Recent runs grid */
function getRunQueriesExecuted(run) {
  const output = run?.output || {};
  if (output.analysis_type) {
    const n = output.log_groups_analyzed?.length;
    return typeof n === 'number' ? n : 0;
  }
  const orchKey = Object.keys(output).find((k) => k.startsWith('orchestrator'));
  const orch = orchKey ? output[orchKey] : null;
  return orch?.queries_executed ?? output.queries_executed ?? 0;
}

/** 24-hour clock HH:mm — matches NEXT SCHEDULED mock prominence */
function formatTime24Hm(timeString) {
  const mins = parseHmToMinutes(timeString?.trim?.() ?? '');
  if (mins == null) return '—';
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(h)}:${pad(m)}`;
}

function buildScheduleInsightTags(schedule) {
  const nodes = schedule?.nodes || [];
  /** @type {{ key: string, label: string, cls: string }[]} */
  const tags = [];
  if (nodes.some((n) => n.type === 'database')) {
    tags.push({ key: 'db', label: 'postgres', cls: 'bg-sky-50 text-sky-700 border border-sky-200/70' });
  }
  const agent = nodes.find((n) => n.type === 'agent');
  const orch = nodes.find((n) => n.type === 'orchestrator');
  const llmNode = nodes.find((n) => n.type === 'llm');
  let model =
    orch?.data?.model ||
    orch?.data?.llm_model ||
    agent?.data?.model ||
    agent?.data?.llm_model ||
    llmNode?.data?.model ||
    llmNode?.data?.providerModel ||
    '';
  if (typeof model !== 'string') model = String(model || '').trim();
  else model = model.trim();
  if (model) {
    const slug = model.includes('/') ? model.split('/').pop() : model;
    tags.push({
      key: 'model',
      label: slug.length > 24 ? `${slug.slice(0, 22)}…` : slug,
      cls: 'bg-violet-50 text-violet-800 border border-violet-200/70',
    });
  }
  const toolCount = nodes.filter((n) => n.type === 'tool').length;
  if (toolCount > 0) {
    tags.push({
      key: 'tools',
      label: `${toolCount} tools`,
      cls: 'bg-white text-slate-700 border border-slate-300 shadow-sm',
    });
  }
  return tags;
}

function executionsInRollingWindow(list, newerThanMsAgo, olderThanMsAgo = 0) {
  const now = Date.now();
  return list.filter((e) => {
    const d = parseDate(e.start_time);
    if (!d) return false;
    const age = now - d.getTime();
    return age >= olderThanMsAgo && age < newerThanMsAgo;
  });
}

/** One bucket per calendar day starting (days−1) days ago through today. */
function dailyTokenTotalsLastNDays(list, days = 7) {
  /** @type {number[]} */
  const totals = Array.from({ length: days }, () => 0);
  const todayMid = new Date();
  todayMid.setHours(0, 0, 0, 0);
  const startDay = new Date(todayMid);
  startDay.setDate(startDay.getDate() - (days - 1));
  list.forEach((e) => {
    const d = parseDate(e.start_time);
    if (!d) return;
    const dayMid = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const ix = Math.round((dayMid.getTime() - startDay.getTime()) / DAY_MS);
    if (ix >= 0 && ix < days) totals[ix] += e.total_tokens || 0;
  });
  return totals;
}

function formatTokK(num) {
  if (num <= 0) return '—';
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
  return String(num.toLocaleString());
}

/** Reference sparkline — path stroke, configurable size */
function DashboardSparkline({
  data,
  color = '#dc2626',
  width = 96,
  height = 28
}) {
  const series =
    Array.isArray(data) && data.length > 0 ? data : Array.from({ length: 7 }, () => 0);
  const max = Math.max(...series.map((v) => v || 0), 1);
  const minRaw = Math.min(...series.map((v) => v || 0), 0);
  const range = Math.max(max - minRaw, 1);
  const step = series.length <= 1 ? 0 : width / (series.length - 1);
  const pathD = series
    .map((vRaw, i) => {
      const v = vRaw || 0;
      const x = i * step;
      const y = height - ((v - minRaw) / range) * (height - 4) - 2;
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
  return (
    <svg
      width={width}
      height={height}
      className="shrink-0 overflow-visible text-red-600"
      aria-hidden
    >
      <path
        d={pathD}
        stroke={color}
        strokeWidth={1.6}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DashboardIconChip({ tint, children }) {
  const tintCls =
    tint === 'emerald'
      ? 'bg-emerald-50 text-emerald-600'
      : tint === 'amber'
        ? 'bg-amber-50 text-amber-600'
        : tint === 'slate'
          ? 'bg-slate-100 text-slate-600'
          : 'bg-red-50 text-red-600';
  return (
    <div
      className={cn(
        'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg',
        tintCls
      )}
    >
      {children}
    </div>
  );
}

function DashboardStatCard({ eyebrow, value, hint, tint = 'red', Icon: IconComp, trend }) {
  const showTrend = typeof trend === 'number' && Number.isFinite(trend);
  return (
    <Card className="border-slate-200/80 shadow-sm transition-shadow hover:shadow-md">
      <CardContent className="p-5">
        <div className="mb-3.5 flex items-start justify-between gap-3">
          <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">
            {eyebrow}
          </span>
          <DashboardIconChip tint={tint}>
            <IconComp size={16} strokeWidth={2} />
          </DashboardIconChip>
        </div>
        <div className="text-4xl font-bold tracking-tight text-slate-900 tabular-nums">
          {value}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {showTrend && (
            <span
              className={cn(
                'text-[11px] font-semibold tabular-nums',
                trend >= 0 ? 'text-emerald-600' : 'text-red-600'
              )}
            >
              {trend >= 0 ? '▲' : '▼'} {Math.abs(Math.round(trend))}%
            </span>
          )}
          <span className="text-[11px] font-medium leading-snug text-slate-400">{hint}</span>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Dashboard() {
  const { schedules } = useScheduler();
  const { runningWorkflows, count: inProgressCount } = useWorkflowStatus();

  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [page, setPage] = useState(0);
  const [rowsPerPage] = useState(5);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);

  const loadExecutions = useCallback(async () => {
    try {
      const history = await agentApiClient.listAllExecutions(100);
      setExecutions(history);
    } catch (error) {
      console.error('Error loading executions:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleClearAll = useCallback(async () => {
    setClearing(true);
    try {
      await agentApiClient.deleteAllExecutions();
      setExecutions([]);
      setPage(0);
      setConfirmClear(false);
    } catch (error) {
      console.error('Error clearing executions:', error);
      alert('Failed to clear executions. Please try again.');
    } finally {
      setClearing(false);
    }
  }, []);

  useEffect(() => {
    loadExecutions();

    const interval = setInterval(() => {
      if (!document.hidden) loadExecutions();
    }, 30000);

    const handleVisibility = () => {
      if (!document.hidden) loadExecutions();
    };
    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [loadExecutions]);

  const stats = useMemo(() => {
    const totalSchedules = schedules.length;
    const enabledSchedules = schedules.filter(s => s.enabled).length;
    const disabledSchedules = totalSchedules - enabledSchedules;

    // Calculate failures in last 24h (any non-success status)
    const failedLast24h = executions.filter(e => {
      if (e.status === 'success') return false;
      const date = parseDate(e.start_time);
      if (!date) return false;
      return (Date.now() - date.getTime()) < 24 * 60 * 60 * 1000;
    }).length;

    // Calculate total executions in last 24h
    const executionsLast24h = executions.filter(e => {
      const date = parseDate(e.start_time);
      if (!date) return false;
      return (Date.now() - date.getTime()) < 24 * 60 * 60 * 1000;
    }).length;

    // Calculate success rate
    const successRate = executionsLast24h > 0
      ? Math.round(((executionsLast24h - failedLast24h) / executionsLast24h) * 100)
      : 100;

    // Calculate workflows from yesterday
    const yesterday = Date.now() - 24 * 60 * 60 * 1000;
    const twoDaysAgo = Date.now() - 48 * 60 * 60 * 1000;

    const executionsYesterday = executions.filter(e => {
      const date = parseDate(e.start_time);
      if (!date) return false;
      const time = date.getTime();
      return time >= twoDaysAgo && time < yesterday;
    }).length;

    const DAY_MS_RUNTIME = DAY_MS;

    const failedPrior24To48 = executions.filter((e) => {
      if (e.status === 'success') return false;
      const date = parseDate(e.start_time);
      if (!date) return false;
      const age = Date.now() - date.getTime();
      return age >= DAY_MS_RUNTIME && age < 2 * DAY_MS_RUNTIME;
    }).length;

    let failuresTrendPct = null;
    if (failedPrior24To48 > 0) {
      failuresTrendPct = Math.round(
        ((failedLast24h - failedPrior24To48) / failedPrior24To48) * 100
      );
    } else if (failedLast24h > 0) {
      failuresTrendPct = 100;
    }

    let runsTrendPct = null;
    if (executionsYesterday > 0) {
      runsTrendPct = Math.round(
        ((executionsLast24h - executionsYesterday) / executionsYesterday) * 100
      );
    } else if (executionsLast24h > 0) {
      runsTrendPct = 100;
    }

    return [
      {
        eyebrow: 'Active workflows',
        value: enabledSchedules,
        icon: LayoutDashboard,
        tint: 'emerald',
        hint: disabledSchedules > 0 ? `${disabledSchedules} disabled` : 'All enabled',
        trend: null
      },
      {
        eyebrow: 'Total workflows',
        value: totalSchedules,
        icon: Calendar,
        tint: 'slate',
        hint: `${Math.round((enabledSchedules / Math.max(totalSchedules, 1)) * 100)}% active`,
        trend: null
      },
      {
        eyebrow: 'Failures (24h)',
        value: failedLast24h,
        icon: AlertTriangle,
        tint: 'red',
        hint: `${successRate}% success rate`,
        trend: failuresTrendPct
      },
      {
        eyebrow: 'Running now',
        value: inProgressCount,
        icon: Zap,
        tint: 'amber',
        hint: executionsLast24h > 0 ? `${executionsLast24h} runs today` : 'No runs today',
        trend: runsTrendPct
      }
    ];
  }, [schedules, executions, inProgressCount]);

  const paginatedRuns = useMemo(() =>
    executions.slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage),
    [executions, page, rowsPerPage]);

  const totalPages = Math.ceil(executions.length / rowsPerPage);

  /** `HH:mm` (24h) — aligns with dashboard activity mock timeline */
  const formatActivityClock = useCallback((d) => {
    if (!(d instanceof Date) || Number.isNaN(d.getTime())) return '—';
    const h = d.getHours();
    const m = d.getMinutes();
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(h)}:${pad(m)}`;
  }, []);

  const activityMetaLine = useCallback((e) => {
    const st = (e.status || '').toLowerCase();
    const isRunning =
      st === 'in_progress' || st === 'running';
    const isFailed =
      st === 'failed' || st === 'failure' || st === 'error';

    let errMsg = '';
    if (typeof e.error === 'string' && e.error.trim()) errMsg = e.error.trim();
    else if (e.error && typeof e.error === 'object' && typeof e.error.message === 'string') {
      errMsg = e.error.message.trim();
    } else if (e.output?.error != null && String(e.output.error).trim()) {
      errMsg = String(e.output.error).trim();
    }

    if (errMsg.length > 90) errMsg = `${errMsg.slice(0, 87)}…`;

    const tok =
      typeof e.total_tokens === 'number' && e.total_tokens > 0
        ? `${e.total_tokens.toLocaleString()} tok`
        : '';
    let dur =
      typeof e.duration === 'number'
        ? `${e.duration.toFixed(1)}s`
        : null;
    if (isRunning && !dur && e.start_time) {
      const sd = parseDate(e.start_time);
      if (sd) {
        dur = `${Math.max(0.1, (Date.now() - sd.getTime()) / 1000).toFixed(1)}s`;
      }
    }

    if (isFailed) {
      if (errMsg) return errMsg;
      const parts = [];
      if (dur) parts.push(dur);
      parts.push('failed');
      return parts.join(' · ');
    }
    if (isRunning) {
      const parts = [];
      if (dur) parts.push(dur);
      parts.push('streaming…');
      if (tok) parts.push(tok);
      return parts.join(' · ');
    }
    const parts = [];
    if (dur) parts.push(dur);
    if (tok) parts.push(tok);
    return parts.length ? parts.join(' · ') : '—';
  }, []);

  /**
   * DB rows appear only after a workflow finishes. Active runs live in-memory
   * (`/executions/active` → WorkflowStatus). Prepend those so Running badge/meta show.
   */
  const liveActivityFeed = useMemo(() => {
    const max = 5;
    const active = [...new Set((runningWorkflows || []).filter(Boolean))];
    // Avoid duplicating workflows that are already present in 'executions' as running/pending
    const filteredActive = active.filter((name) => {
      return !executions.some((e) => {
        if (e.workflow_name !== name) return false;
        const st = (e.status || '').toLowerCase();
        return st === 'in_progress' || st === 'running' || st === 'pending';
      });
    });
    const synthetic = filteredActive.slice(0, max).map((name) => ({
      execution_id: `__running__:${encodeURIComponent(name)}`,
      workflow_name: name,
      status: 'running',
      start_time: new Date().toISOString(),
      total_tokens: 0,
    }));
    const merged = [...synthetic];
    for (const row of executions) {
      if (merged.length >= max) break;
      merged.push(row);
    }
    return merged;
  }, [executions, runningWorkflows]);

  const tokenSevenDaySlice = useMemo(() => {
    const curList = executionsInRollingWindow(executions, 7 * DAY_MS, 0);
    const prevList = executionsInRollingWindow(executions, 14 * DAY_MS, 7 * DAY_MS);

    const fold = (arr) => ({
      total: arr.reduce((s, e) => s + (e.total_tokens || 0), 0),
      input: arr.reduce((s, e) => s + (e.input_tokens || 0), 0),
      output: arr.reduce((s, e) => s + (e.output_tokens || 0), 0),
    });

    const cur = fold(curList);
    const prev = fold(prevList);

    let pct = null;
    if (prev.total > 0) {
      pct = Math.round(((cur.total - prev.total) / prev.total) * 100);
    } else if (cur.total > 0) {
      pct = 100;
    }

    const avg = curList.length > 0 ? Math.round(cur.total / curList.length) : null;
    const series = dailyTokenTotalsLastNDays(executions, 7);

    return { cur, prev, pct, avg, series, runCount: curList.length };
  }, [executions]);

  return (
    <div className="flex min-h-screen flex-col gap-6 p-4 md:p-8">
      {/* Title */}
      <div>
        <h2 className="text-2xl md:text-3xl font-bold text-slate-900 tracking-tight">System Dashboard</h2>
        <p className="mt-1 text-sm text-slate-500 md:text-base">Real-time monitoring and node activity tracking</p>
      </div>

      {/* Stat cards */}
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <DashboardStatCard
              key={stat.eyebrow}
              eyebrow={stat.eyebrow}
              value={stat.value}
              hint={stat.hint}
              tint={stat.tint}
              Icon={Icon}
              trend={stat.trend}
            />
          );
        })}
      </section>

      <div className="flex flex-col gap-3">
      {/* Activity rail + Next run + Tokens (reference layout) */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-stretch">
        <Card className="flex min-h-0 flex-col overflow-hidden border border-slate-200/80 shadow-sm lg:h-full">
          <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 pb-3 pt-[14px]">
            <div className="flex items-center gap-2.5">
              <span className="h-2 w-2 shrink-0 rounded-full bg-red-600" aria-hidden />
              <span className="text-[13px] font-bold leading-none text-slate-900">
                Live activity
              </span>
            </div>
            <span className="text-[11px] font-medium leading-none text-slate-400">
              auto-refresh · 30s
            </span>
          </div>
          <div
            className={cn(
              'relative px-5 pb-4 pt-2',
              liveActivityFeed.length > 0 && 'flex min-h-0 flex-1 flex-col'
            )}
          >
            {liveActivityFeed.length > 1 && (
              <span
                className="pointer-events-none absolute bottom-[18px] left-[calc(1.25rem+2.25rem+0.75rem+4.5px)] top-[18px] z-0 w-px bg-slate-200"
                aria-hidden
              />
            )}
            {liveActivityFeed.length === 0 ? (
              <p className="py-8 pl-10 text-sm text-slate-400">No recent activity</p>
            ) : (
              <ul className="m-0 flex min-h-0 list-none flex-col p-0 pr-3 lg:pr-8">
                {liveActivityFeed.map((e) => {
                  const st = (e.status || '').toLowerCase();
                  const isRunning = st === 'in_progress' || st === 'running';
                  const isFailed = st === 'failed' || st === 'failure' || st === 'error';
                  const startDate = parseDate(e.start_time);
                  const timeLabel = formatActivityClock(startDate);
                  const rowKey = String(
                    e.execution_id ?? e.id ?? `${e.workflow_name}-${e.start_time}`
                  );
                  const dotBg = isRunning ? '#f59e0b' : isFailed ? '#dc2626' : '#10b981';
                  const titleSlug = String(e.workflow_name || 'workflow')
                    .trim()
                    .replace(/\s+/g, '-')
                    .toLowerCase();

                  return (
                    <li
                      key={rowKey}
                      className="relative z-[1] flex items-center gap-3 py-2.5"
                    >
                      <span className="w-9 shrink-0 text-right font-mono text-[11px] font-medium tabular-nums text-slate-400">
                        {timeLabel}
                      </span>
                      <span className="relative flex h-[9px] w-[9px] shrink-0 items-center justify-center">
                        {isRunning ? (
                          <>
                            <span
                              className="absolute inline-flex h-3 w-3 animate-ping rounded-full bg-amber-400 opacity-65"
                              aria-hidden
                            />
                            <span
                              className="relative box-border h-[9px] w-[9px] rounded-full border-2 border-white"
                              style={{
                                background: dotBg,
                                boxShadow: `0 0 0 1.5px ${dotBg}33`,
                              }}
                              aria-hidden
                            />
                          </>
                        ) : (
                          <span
                            className="box-border h-[9px] w-[9px] rounded-full border-2 border-white"
                            style={{
                              background: dotBg,
                              boxShadow: `0 0 0 1.5px ${dotBg}33`,
                            }}
                            aria-hidden
                          />
                        )}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13px] font-semibold leading-snug text-slate-900">
                          {titleSlug}
                        </div>
                        <div className="mt-px text-[11px] font-normal leading-snug text-slate-600">
                          {activityMetaLine(e)}
                        </div>
                      </div>
                      <div className="flex shrink-0 justify-end gap-2">
                        {isRunning ? (
                          <Badge className="shrink-0 rounded-full bg-amber-100 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-900 hover:bg-amber-100 border-amber-300/70">
                            <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-amber-800" />
                            Running
                          </Badge>
                        ) : null}
                        {isFailed ? (
                          <Badge
                            variant="destructive"
                            className="shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                          >
                            <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-white opacity-95" />
                            Failure
                          </Badge>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </Card>

        <div className="flex min-h-0 flex-col gap-4 min-w-0 lg:h-full lg:max-w-[320px]">
          <Card className="border border-slate-200/80 p-5 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">
                Next scheduled
              </span>
              <DashboardIconChip tint="slate">
                <Clock size={16} strokeWidth={2} />
              </DashboardIconChip>
            </div>
            {(() => {
              const enabled = schedules.filter((s) => s.enabled && s.startTime);
              if (enabled.length === 0) {
                return <p className="text-sm text-slate-400">No schedules enabled</p>;
              }
              const sorted = sortSchedulesByNextRun(enabled);
              const next = sorted[0];
              const when = nextScheduledLocalDate(next.startTime);
              const relative = formatScheduledRelative(when);
              const slug = String(next.name || 'workflow')
                .replace(/\s+/g, '-')
                .toLowerCase();
              const tags = buildScheduleInsightTags(next);

              return (
                <>
                  <div className="truncate text-[22px] font-bold tracking-tight text-slate-900">
                    {slug}
                  </div>
                  <div className="mt-1 flex flex-wrap items-baseline gap-2">
                    <span className="text-[28px] font-bold leading-none tracking-tight text-red-600 tabular-nums">
                      {formatTime24Hm(next.startTime)}
                    </span>
                    {relative ? (
                      <span className="text-[12px] font-medium text-slate-400">{relative}</span>
                    ) : null}
                  </div>
                  {tags.length > 0 ? (
                    <div className="mt-3.5 flex flex-wrap gap-1.5">
                      {tags.map((t) => (
                        <span
                          key={t.key}
                          className={cn(
                            'inline-flex rounded-full px-2.5 py-0.5 text-[11px] font-medium',
                            t.cls
                          )}
                        >
                          {t.label}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </>
              );
            })()}
          </Card>

          <Card className="border border-slate-200/80 p-5 shadow-sm">
            <div className="mb-3.5 flex items-center justify-between gap-3">
              <span className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-500">
                Token usage · 7d
              </span>
              <DashboardSparkline data={tokenSevenDaySlice.series} width={96} height={28} />
            </div>
            <div className="mb-5 flex flex-wrap items-baseline gap-2">
              <span
                className={cn(
                  'text-[28px] font-bold tracking-tight tabular-nums',
                  tokenSevenDaySlice.cur.total <= 0 ? 'text-slate-400' : 'text-slate-900'
                )}
              >
                {tokenSevenDaySlice.cur.total <= 0
                  ? '0'
                  : formatTokK(tokenSevenDaySlice.cur.total).replace('—', '0')}
              </span>
              {tokenSevenDaySlice.pct != null && tokenSevenDaySlice.cur.total > 0 && (
                <span
                  className={cn(
                    'text-xs font-semibold tabular-nums',
                    tokenSevenDaySlice.pct >= 0 ? 'text-emerald-600' : 'text-red-600'
                  )}
                >
                  {tokenSevenDaySlice.pct >= 0 ? '▲' : '▼'}{' '}
                  {Math.abs(tokenSevenDaySlice.pct)}%
                </span>
              )}
            </div>
            {tokenSevenDaySlice.cur.total === 0 ? (
              <p className="mb-5 text-xs leading-snug text-slate-400">
                No runs with recorded token telemetry in the last 7 days.
              </p>
            ) : null}
            <div className="flex justify-between gap-6 border-t border-slate-100 pt-3 text-[11px]">
              <div>
                <div className="text-slate-400">Input</div>
                <div className="mt-px text-[13px] font-semibold tabular-nums text-blue-700">
                  {formatTokK(tokenSevenDaySlice.cur.input)}
                </div>
              </div>
              <div>
                <div className="text-slate-400">Output</div>
                <div className="mt-px text-[13px] font-semibold tabular-nums text-emerald-600">
                  {formatTokK(tokenSevenDaySlice.cur.output)}
                </div>
              </div>
              <div>
                <div className="text-slate-400">Avg/run</div>
                <div className="mt-px text-[13px] font-semibold tabular-nums text-slate-900">
                  {tokenSevenDaySlice.avg != null ? tokenSevenDaySlice.avg.toLocaleString() : '—'}
                </div>
              </div>
            </div>
          </Card>
        </div>
      </section>

      {/* Recent runs */}
      <Card className="overflow-hidden border border-slate-200/80 shadow-sm">
        <CardHeader className="flex shrink-0 flex-row items-center justify-between space-y-0 border-b border-slate-100 px-5 pb-3 pt-3.5">
          <CardTitle className="text-[13px] font-bold text-slate-900">Recent runs</CardTitle>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={loadExecutions}
              className="h-8 text-red-600 hover:bg-red-50 hover:text-red-700"
            >
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
              Refresh
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setConfirmClear(true)}
              disabled={executions.length === 0}
              className="h-8 border-slate-200"
            >
              <Trash2 className="mr-1 h-3.5 w-3.5" />
              Clear
            </Button>
          </div>
        </CardHeader>

        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="border-0 bg-slate-50 hover:bg-slate-50">
                  <TableHead className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    Workflow
                  </TableHead>
                  <TableHead className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    Started
                  </TableHead>
                  <TableHead className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    Duration
                  </TableHead>
                  <TableHead className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    Queries
                  </TableHead>
                  <TableHead className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    Status
                  </TableHead>
                  <TableHead className="px-4 py-2.5 text-left text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    Tokens
                  </TableHead>
                  <TableHead className="px-4 py-2.5 text-right text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
                    {/* row open */}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className="text-sm">
                {loading && executions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="px-4 py-8 text-center text-slate-500">
                      Loading…
                    </TableCell>
                  </TableRow>
                ) : executions.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="px-4 py-8 text-center text-slate-500">
                      No recent runs
                    </TableCell>
                  </TableRow>
                ) : (
                  paginatedRuns.map((run) => {
                    const runSt = (run.status || '').toLowerCase();
                    const isSuccess = runSt === 'success';
                    const isRunningRow =
                      runSt === 'in_progress' || runSt === 'running';
                    const isFail = !(isSuccess || isRunningRow);
                    const qs = getRunQueriesExecuted(run);
                    const wfSlug = String(run.workflow_name || '')
                      .trim()
                      .replace(/\s+/g, '-')
                      .toLowerCase();

                    return (
                      <TableRow
                        key={run.execution_id || run.id}
                        className="border-t border-slate-100 hover:bg-slate-50/50"
                      >
                        <TableCell className="px-4 py-3 text-[13px] font-semibold text-slate-900">
                          {wfSlug || '—'}
                        </TableCell>
                        <TableCell className="px-4 py-3 text-[13px] text-slate-500">
                          {formatRunStartedCompact(run.start_time)}
                        </TableCell>
                        <TableCell className="px-4 py-3 font-mono text-[12px] tabular-nums text-slate-600">
                          {run.duration ? `${run.duration.toFixed(1)}s` : '—'}
                        </TableCell>
                        <TableCell
                          className={cn(
                            'px-4 py-3 text-[13px] font-semibold',
                            qs > 0 ? 'text-red-600' : 'text-slate-400'
                          )}
                        >
                          {qs > 0 ? qs : '—'}
                        </TableCell>
                        <TableCell className="px-4 py-3">
                          {isSuccess ? (
                            <Badge variant="success" className="text-[11px]">
                              Success
                            </Badge>
                          ) : isRunningRow ? (
                            <Badge className="rounded-full border-amber-300/70 bg-amber-100 px-2.5 py-0.5 text-[11px] font-semibold text-amber-900">
                              <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-amber-700" />
                              Running
                            </Badge>
                          ) : (
                            <Badge variant="destructive" className="text-[11px]">
                              Failure
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="px-4 py-3 font-mono text-[12px] tabular-nums text-slate-500">
                          {(run.total_tokens ?? 0) > 0
                            ? Number(run.total_tokens).toLocaleString()
                            : '—'}
                        </TableCell>
                        <TableCell className="px-4 py-3 text-right">
                          <button
                            type="button"
                            onClick={() => setSelectedRun(run)}
                            className="inline-flex rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
                          >
                            <ChevronRight className="h-4 w-4" />
                          </button>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>

          <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/90 px-5 py-3">
            <p className="text-xs italic text-slate-400">
              {executions.length === 0
                ? 'No runs yet.'
                : `Showing ${page * rowsPerPage + 1}–${Math.min((page + 1) * rowsPerPage, executions.length)} of ${executions.length} runs`}
            </p>
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-slate-500">
                Page {executions.length === 0 ? 0 : page + 1} of {totalPages || 1}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-400 transition-colors hover:text-slate-700 disabled:pointer-events-none disabled:opacity-30"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1 || executions.length === 0}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 text-slate-600 transition-colors hover:bg-slate-100 disabled:pointer-events-none disabled:opacity-30"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </CardContent>
      </Card>
      </div>

      {/* Confirmation Dialog for Clear All */}
      <Dialog open={confirmClear} onOpenChange={setConfirmClear}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Clear All Executions?</DialogTitle>
            <DialogDescription>
              This will permanently delete all {executions.length} execution{executions.length === 1 ? '' : 's'} from the history. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => setConfirmClear(false)}
              disabled={clearing}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleClearAll}
              disabled={clearing}
            >
              {clearing ? 'Clearing...' : 'Clear All'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Details Dialog */}
      <Dialog open={!!selectedRun} onOpenChange={(open) => !open && setSelectedRun(null)}>
        <DialogContent className="sm:max-w-4xl w-full max-h-[90vh] overflow-y-auto bg-white border border-slate-200 shadow-xl p-0 gap-0">
          {/* Header Section */}
          <div className="bg-white border-b border-slate-200 px-6 py-5">
            <DialogHeader>
              <DialogTitle className="text-xl font-semibold text-slate-900">
                Execution Details: {selectedRun?.workflow_name?.replaceAll('-', ' ')}
              </DialogTitle>
            </DialogHeader>
          </div>

          <div className="p-8 space-y-8 bg-slate-50 overflow-y-auto custom-scrollbar">
            {/* Execution Metrics Grid */}
            {(() => {
              const resultData = selectedRun?.result || {};
              const orchestratorKey = Object.keys(resultData).find(k => k.startsWith('orchestrator'));
              const orchestrator = orchestratorKey ? resultData[orchestratorKey] : null;

              // Detect CW node and agent node (same logic as Results Section below)
              const _cwKey = Object.keys(resultData).find(k => resultData[k]?.analysis_type);
              const _cwNodeResult = _cwKey ? resultData[_cwKey] : null;
              const _agentKey = Object.keys(resultData).find(k => k.startsWith('agent') || resultData[k]?.final_answer != null);
              const _agentNodeResult = _agentKey ? resultData[_agentKey] : null;
              const _cwSource = _cwNodeResult || selectedRun?.output || {};

              const isCloudWatch = !!_cwNodeResult || selectedRun?.output?.analysis_type;
              const isReact = !!_agentNodeResult || selectedRun?.output?.type === 'react';

              const queriesExecuted = orchestrator?.queries_executed || selectedRun?.output?.queries_executed || 0;
              const failures = orchestrator?.failures || selectedRun?.output?.failures || 0;
              const duration = selectedRun?.duration ? selectedRun.duration.toFixed(2) : 'N/A';
              // cwSource: cloudwatch_tool returns log_groups; legacy cloudwatchAnalyzer returns log_groups_analyzed
              // Also check raw selectedRun.output for legacy flat-output runs (pre node-keying)
              const logGroupsAnalyzed =
                (_cwSource?.log_groups_analyzed?.length) ||
                (_cwSource?.log_groups?.length) ||
                (selectedRun?.output?.log_groups_analyzed?.length) ||
                (selectedRun?.output?.log_groups?.length) ||
                0;
              const alertsCount =
                _cwSource?.alerts?.length ||
                selectedRun?.output?.alerts?.length ||
                0;

              if (isCloudWatch) {
                return (
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Execution ID</p>
                      <p className="mt-2 text-4xl font-bold text-slate-900">{selectedRun?.execution_id || selectedRun?.id}</p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Duration</p>
                      <p className="mt-2 text-4xl font-bold text-slate-900">{duration}<span className="text-xl ml-1 text-slate-400">s</span></p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Log Groups</p>
                      <p className="mt-2 text-4xl font-bold text-blue-700">{logGroupsAnalyzed}</p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Alerts</p>
                      <p className="mt-2 text-4xl font-bold text-emerald-600">{alertsCount}</p>
                    </div>
                  </div>
                );
              }

              if (isReact) {
                const msgCount = _agentNodeResult?.message_count || selectedRun?.output?.message_count || 0;
                const toolCalls = (_agentNodeResult?.tool_calls || selectedRun?.output?.tool_calls || []).length;
                return (
                  <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Execution ID</p>
                      <p className="mt-2 text-4xl font-bold text-slate-900">{selectedRun?.execution_id || selectedRun?.id}</p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Duration</p>
                      <p className="mt-2 text-4xl font-bold text-slate-900">{duration}<span className="text-xl ml-1 text-slate-400">s</span></p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Messages</p>
                      <p className="mt-2 text-4xl font-bold text-blue-700">{msgCount}</p>
                    </div>
                    <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                      <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Tool Calls</p>
                      <p className="mt-2 text-4xl font-bold text-emerald-600">{toolCalls}</p>
                    </div>
                  </div>
                );
              }

              return (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Execution ID</p>
                    <p className="mt-2 text-4xl font-bold text-slate-900">{selectedRun?.execution_id || selectedRun?.id}</p>
                  </div>
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Duration</p>
                    <p className="mt-2 text-4xl font-bold text-slate-900">{duration}<span className="text-xl ml-1 text-slate-400">s</span></p>
                  </div>
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Queries</p>
                    <p className="mt-2 text-4xl font-bold text-blue-700">{queriesExecuted}</p>
                  </div>
                  <div className="bg-slate-50 p-6 rounded-lg border border-slate-200 hover:shadow-md transition-shadow">
                    <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider">Failures</p>
                    <p className="mt-2 text-4xl font-bold text-emerald-600">{failures}</p>
                  </div>
                </div>
              );
            })()}

            {/* Token Usage — shown whenever token data is available (any execution type) */}
            {(() => {
              // DB top-level columns (new runs after fix) → node-level agent result (old runs) → 0
              const resultData2 = selectedRun?.result || {};
              const _agentTok = (() => {
                const agk = Object.keys(resultData2).find(k => k.startsWith('agent') || resultData2[k]?.input_tokens > 0);
                return agk ? resultData2[agk] : null;
              })();
              const inputTok  = selectedRun?.input_tokens  || _agentTok?.input_tokens  || 0;
              const outputTok = selectedRun?.output_tokens || _agentTok?.output_tokens || 0;
              const totalTok  = selectedRun?.total_tokens  || _agentTok?.total_tokens  || (inputTok + outputTok) || 0;
              if (!totalTok) return null;
              return (
                <div className="bg-white rounded-lg border border-slate-200 p-6">
                  <p className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-4">⚡ Token Usage</p>
                  <div className="grid grid-cols-3 gap-4 text-center">
                    <div className="bg-blue-50 rounded-lg p-4">
                      <p className="text-xs font-medium text-slate-500 mb-1">Input</p>
                      <p className="text-2xl font-bold text-blue-700">{inputTok.toLocaleString()}</p>
                    </div>
                    <div className="bg-emerald-50 rounded-lg p-4">
                      <p className="text-xs font-medium text-slate-500 mb-1">Output</p>
                      <p className="text-2xl font-bold text-emerald-600">{outputTok.toLocaleString()}</p>
                    </div>
                    <div className="bg-slate-100 rounded-lg p-4">
                      <p className="text-xs font-medium text-slate-500 mb-1">Total</p>
                      <p className="text-2xl font-bold text-slate-900">{totalTok.toLocaleString()}</p>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Results Section */}
            {(() => {
              // resultData is the sanitized node-results dict: { node_id: { status, output, ... } }
              const resultData = selectedRun?.result || {};
              const orchestratorKey = Object.keys(resultData).find(k => k.startsWith('orchestrator'));
              const orchestrator = orchestratorKey ? resultData[orchestratorKey] : null;

              // Detect agent (ReAct) node: key starts with 'agent' or node has final_answer
              const agentKey = Object.keys(resultData).find(
                k => k.startsWith('agent') || resultData[k]?.final_answer != null
              );
              const agentNodeResult = agentKey ? resultData[agentKey] : null;

              // Detect CloudWatch node: any node result with analysis_type
              const cwKey = Object.keys(resultData).find(k => resultData[k]?.analysis_type);
              const cwNodeResult = cwKey ? resultData[cwKey] : null;

              // Legacy flat-output compat (old cloudwatchAnalyzer path stored output at top level)
              const isCloudWatch = cwNodeResult || selectedRun?.output?.analysis_type;
              const isReact = !!agentNodeResult || selectedRun?.output?.type === 'react';

              const results = orchestrator?.results || selectedRun?.output?.results || [];

              // Resolve the CW source (node-level preferred, flat-output fallback)
              const cwSource = cwNodeResult || selectedRun?.output || {};

              if (isCloudWatch) {
                const cwResults = cwSource?.results || {};
                const cwAlerts = cwSource?.alerts || [];
                const analysisType = cwSource.analysis_type;
                const logGroupsAnalyzed = cwSource.log_groups_analyzed || [];
                const timeRange = cwSource.time_range || '—';
                // For tool-provider CW workflows: agent's final answer is the real output.
                // For legacy cloudwatchAnalyzer: cwSource.output holds the pre-computed analysis.
                const llmOutput = cleanLlmText(agentNodeResult?.final_answer || agentNodeResult?.output || cwSource.output);
                const modelUsed = agentNodeResult?.model || cwSource.model;

                return (
                  <div>
                    <div className="flex items-center justify-between mb-6">
                      <h3 className="text-xl font-bold text-slate-900">CloudWatch Analysis</h3>
                      <div className="flex items-center gap-2">
                        <span className="px-3 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-medium uppercase">{analysisType}</span>
                        {modelUsed && (
                          <span className="px-3 py-1 bg-violet-100 text-violet-700 rounded-full text-xs font-medium">{modelUsed}</span>
                        )}
                      </div>
                    </div>

                    {/* LLM Analysis (primary output) */}
                    {llmOutput && (
                      <div className="bg-white rounded-lg border border-slate-200 overflow-hidden mb-6">
                        <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
                          <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">Analysis</p>
                        </div>
                        <div className="px-4 py-4">
                          <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{llmOutput}</ReactMarkdown>
                        </div>
                      </div>
                    )}

                    {/* Metadata table */}
                    <div className="border border-slate-200 rounded-lg overflow-hidden shadow-sm">
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50 border-b border-slate-200">
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Property</TableHead>
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Value</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                            <TableCell className="px-6 py-4 text-slate-600 font-medium">Analysis Type</TableCell>
                            <TableCell className="px-6 py-4">
                              <span className="px-2 py-1 bg-slate-100 rounded text-xs font-medium">{analysisType}</span>
                            </TableCell>
                          </TableRow>
                          <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                            <TableCell className="px-6 py-4 text-slate-600 font-medium">Time Range</TableCell>
                            <TableCell className="px-6 py-4 font-semibold text-slate-900">{timeRange}</TableCell>
                          </TableRow>
                          <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                            <TableCell className="px-6 py-4 text-slate-600 font-medium">Log Groups</TableCell>
                            <TableCell className="px-6 py-4">
                              <div className="flex flex-wrap gap-1">
                                {logGroupsAnalyzed.map((lg, i) => (
                                  <code key={i} className="px-2 py-0.5 bg-slate-100 rounded text-xs font-mono text-slate-800 border border-slate-200">{lg}</code>
                                ))}
                              </div>
                            </TableCell>
                          </TableRow>
                          {cwAlerts.length > 0 && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Alerts</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="space-y-2">
                                  {cwAlerts.map((alert, i) => (
                                    <div key={i} className={`px-3 py-2 rounded text-xs font-medium ${alert.severity === 'high' ? 'bg-red-50 border border-red-200 text-red-800' : alert.severity === 'medium' ? 'bg-amber-50 border border-amber-200 text-amber-800' : 'bg-slate-50 border border-slate-200 text-slate-800'}`}>
                                      <span className="font-bold uppercase">{alert.severity}</span>: {alert.message}
                                    </div>
                                  ))}
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {/* Show raw data when no LLM analysis is available */}
                          {!llmOutput && cwResults.patterns && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Patterns</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.patterns, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {!llmOutput && cwResults.anomalies && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Anomalies</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.anomalies, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {!llmOutput && cwResults.timeline && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Timeline</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.timeline, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {!llmOutput && cwResults.log_groups && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Log Group Details</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.log_groups, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                          {!llmOutput && cwResults.summary && (
                            <TableRow className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                              <TableCell className="px-6 py-4 text-slate-600 font-medium">Summary</TableCell>
                              <TableCell className="px-6 py-4">
                                <div className="bg-slate-50 rounded p-3 text-xs font-mono overflow-auto max-h-48">
                                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(cwResults.summary, null, 2)}</pre>
                                </div>
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                );
              }

              if (Array.isArray(results) && results.length > 0) {
                return (
                  <div>
                    <div className="flex items-center justify-between mb-6">
                      <h3 className="text-xl font-bold text-slate-900">Query Results</h3>
                      <span className="px-3 py-1 bg-slate-100 text-slate-600 rounded-full text-xs font-medium uppercase">Detailed Metrics</span>
                    </div>
                    <div className="border border-slate-200 rounded-lg overflow-hidden shadow-sm">
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50 border-b border-slate-200">
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Label</TableHead>
                            <TableHead className="px-6 py-4 text-sm font-bold text-slate-700 uppercase">Result / Error</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {results.map((result, idx) => {
                            const label = result.label || result.query_id || `Query ${idx + 1}`;
                            const hasError = !result.success || result.error;
                            let resultValue = hasError ? result.error : result.result;

                            // Extract value from array if it's a single-element array
                            if (Array.isArray(resultValue) && resultValue.length === 1) {
                              resultValue = resultValue[0];
                            }

                            // Extract scalar value if result is an object with a single key-value pair
                            let displayValue = resultValue;
                            if (typeof resultValue === 'object' && resultValue !== null && !Array.isArray(resultValue)) {
                              const keys = Object.keys(resultValue);
                              if (keys.length === 1) {
                                displayValue = resultValue[keys[0]];
                              }
                            }

                            const uniqueKey = result.query_id || `${label}-${idx}`;

                            // Check if value looks like a UUID
                            const isUUID = typeof displayValue === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(displayValue);

                            return (
                              <TableRow key={uniqueKey} className="hover:bg-slate-50 transition-colors border-b border-slate-200">
                                <TableCell className="px-6 py-4 text-slate-600 font-medium">{label}</TableCell>
                                <TableCell className="px-6 py-4">
                                  {hasError ? (
                                    <span className="text-red-600 font-semibold">{String(displayValue)}</span>
                                  ) : typeof displayValue === 'boolean' ? (
                                    displayValue ? (
                                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-green-100 text-emerald-600 uppercase">
                                        <span className="w-2 h-2 mr-1.5 rounded-full bg-emerald-600"></span>
                                        TRUE
                                      </span>
                                    ) : (
                                      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-600 uppercase">
                                        <span className="w-2 h-2 mr-1.5 rounded-full bg-red-600"></span>
                                        FALSE
                                      </span>
                                    )
                                  ) : isUUID ? (
                                    <code className="px-2 py-1 bg-slate-100 rounded text-xs font-mono text-slate-800 border border-slate-200">
                                      {String(displayValue)}
                                    </code>
                                  ) : typeof displayValue === 'object' && displayValue !== null ? (
                                    <div className="bg-slate-50 rounded p-2 text-xs font-mono overflow-auto max-h-32">
                                      <pre className="whitespace-pre-wrap break-words">{JSON.stringify(displayValue, null, 2)}</pre>
                                    </div>
                                  ) : (
                                    <span className="font-semibold text-slate-900">{String(displayValue)}</span>
                                  )}
                                </TableCell>
                              </TableRow>
                            );
                          })}
                        </TableBody>
                      </Table>
                    </div>
                  </div>
                );
              }

              if (isReact) {
                // Prefer node-level result; fall back to legacy flat output
                const reactSource = agentNodeResult || selectedRun?.output || {};
                const finalAnswer = cleanLlmText(reactSource?.final_answer || reactSource?.output || '');
                const userQuery = reactSource?.user_query || '';
                const model = reactSource?.model || '';
                const provider = reactSource?.provider || '';
                const toolCalls = reactSource?.tool_calls || [];
                return (
                  <div>
                    <div className="flex items-center justify-between mb-6">
                      <h3 className="text-xl font-bold text-slate-900">Agent Response</h3>
                      <span className="px-3 py-1 bg-violet-100 text-violet-700 rounded-full text-xs font-medium uppercase">{provider} / {model}</span>
                    </div>
                    <div className="space-y-4">
                      {userQuery && (
                        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
                          <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
                            <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">Query</p>
                          </div>
                          <div className="px-4 py-3">
                            <p className="text-sm text-slate-700">{userQuery}</p>
                          </div>
                        </div>
                      )}
                      <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
                        <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
                          <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">Final Answer</p>
                        </div>
                        <div className="px-4 py-4">
                          {finalAnswer ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{finalAnswer}</ReactMarkdown>
                          ) : (
                            <p className="text-sm text-slate-400 italic">(no answer)</p>
                          )}
                        </div>
                      </div>
                      {toolCalls.length > 0 && (
                        <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
                          <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
                            <p className="text-xs font-bold text-slate-500 uppercase tracking-wider">Tool Calls ({toolCalls.length})</p>
                          </div>
                          <div className="px-4 py-3 space-y-2">
                            {toolCalls.map((tc, i) => (
                              <div key={i} className="flex items-center gap-2 text-xs">
                                <span className="px-2 py-0.5 bg-slate-100 rounded font-mono text-slate-700 border border-slate-200">{tc.name || tc.tool || `Tool ${i + 1}`}</span>
                                {tc.error && <span className="text-red-600">Error: {tc.error}</span>}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              }

              return null;
            })()}

            {/* Error Information */}
            {selectedRun?.error && (
              <div className="bg-white rounded-lg border border-red-200 overflow-hidden">
                <div className="px-6 py-4 border-b border-red-200 bg-red-50 flex items-center gap-2">
                  <AlertTriangle className="w-4 h-4 text-red-600" />
                  <h3 className="text-base font-semibold text-red-900">Error Details</h3>
                </div>
                <div className="p-6">
                  <pre className="text-sm font-mono text-red-800 whitespace-pre-wrap break-words">
                    {typeof selectedRun.error === 'string' ? selectedRun.error : JSON.stringify(selectedRun.error, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="bg-white border-t border-slate-200 px-6 py-4 flex justify-end">
            <Button onClick={() => setSelectedRun(null)} variant="outline" className="px-6">CLOSE</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
