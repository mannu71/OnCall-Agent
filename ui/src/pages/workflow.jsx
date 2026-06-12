import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Plus, Search, Play, Pencil, Trash2, MoreHorizontal, Workflow,
  Clock, CheckCircle2, XCircle, Loader2, ChevronLeft, Save, X, Zap,
  CalendarClock, RefreshCw, Activity, Layers, Sparkles, Power, ListFilter,
} from 'lucide-react';
import LangflowEditor from '../components/workflow/LangflowEditor.jsx';
import TemplateGallery from '../components/workflow/TemplateGallery.jsx';
import { validateWorkflow } from '../utils/workflowValidation.js';
import agentApiClient from '../services/agentApiClient.js';
import { getIndexingStatus } from '../services/apiClient.js';
import { useScheduler } from '../context/SchedulerContext';
import { useWorkflowStatus } from '../context/WorkflowStatusContext';

// ─── helpers ────────────────────────────────────────────────────────────────

function formatScheduleLabel(w) {
  if (!w.schedule || w.schedule === 'Manual') return 'Manual';
  return w.schedule;
}

function relativeTime(iso) {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const TYPE_META = {
  workflow: { label: 'Workflow', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  agent:    { label: 'Agent',    color: 'bg-violet-50 text-violet-700 border-violet-200' },
};

// Derive every status-driven visual treatment for a workflow in one place so
// the card stays consistent (accent bar, icon wrap, pill colors, label).
function statusMeta(w) {
  const isIndexing = w.indexingStatus === 'indexing';
  const indexFailed = typeof w.indexingStatus === 'string' && w.indexingStatus.startsWith('indexing_failed');
  if (isIndexing) return {
    key: 'indexing', label: 'Indexing', spinner: true,
    bar: 'from-amber-400 to-orange-400',
    iconWrap: 'bg-gradient-to-br from-amber-50 to-orange-50 text-amber-600 ring-amber-100',
    pill: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-500',
  };
  if (indexFailed) return {
    key: 'failed', label: 'Index failed',
    bar: 'from-red-400 to-rose-500',
    iconWrap: 'bg-gradient-to-br from-red-50 to-rose-50 text-red-600 ring-red-100',
    pill: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-500',
  };
  if (w.enabled) return {
    key: 'active', label: 'Active',
    bar: 'from-emerald-400 to-teal-400',
    iconWrap: 'bg-gradient-to-br from-emerald-50 to-teal-50 text-emerald-600 ring-emerald-100',
    pill: 'bg-emerald-50 text-emerald-700 border-emerald-200', dot: 'bg-emerald-500',
  };
  return {
    key: 'disabled', label: 'Disabled',
    bar: 'from-slate-300 to-slate-300',
    iconWrap: 'bg-slate-100 text-slate-400 ring-slate-100',
    pill: 'bg-slate-50 text-slate-500 border-slate-200', dot: 'bg-slate-400',
  };
}

// ─── StatCard ─────────────────────────────────────────────────────────────────

function StatCard({ icon: Icon, label, value, accent, ring }) {
  return (
    <div className="group relative flex items-center gap-3 rounded-2xl border border-slate-200/80 bg-white/70 backdrop-blur px-4 py-3.5 shadow-sm transition-all hover:shadow-md hover:border-slate-300">
      <div className={`flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center ring-1 ${accent} ${ring}`}>
        <Icon className="w-[18px] h-[18px]" />
      </div>
      <div className="min-w-0">
        <div className="text-2xl font-bold text-slate-900 leading-none tabular-nums">{value}</div>
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400 mt-1">{label}</div>
      </div>
    </div>
  );
}

// ─── MetaItem ─────────────────────────────────────────────────────────────────

function MetaItem({ icon: Icon, children }) {
  return (
    <div className="flex items-center gap-1.5 min-w-0 text-[11px] text-slate-500">
      <Icon className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
      <span className="truncate font-mono">{children}</span>
    </div>
  );
}

// ─── WorkflowCard ────────────────────────────────────────────────────────────

function WorkflowCard({ w, isRunning, onEdit, onRun, onDelete, indexProgress }) {
  const typeMeta = TYPE_META[w.type] || TYPE_META.workflow;
  const s = statusMeta(w);
  const scheduleLabel = formatScheduleLabel(w);
  const lastRun = relativeTime(w.updatedAt || w.updated_at);
  const nodeCount = Array.isArray(w.nodes) ? w.nodes.length : 0;
  const isIndexing = s.key === 'indexing';
  // Live progress from the background-job store (GET /jobs/indexing/status).
  const indexLabel = (isIndexing && indexProgress && indexProgress.total > 0)
    ? `Indexing… (${indexProgress.progress}/${indexProgress.total})`
    : 'Indexing…';

  return (
    <div className="group relative flex flex-col rounded-2xl border border-slate-200 bg-white shadow-sm transition-all duration-300 hover:shadow-xl hover:shadow-slate-200/70 hover:border-slate-300 hover:-translate-y-1 overflow-hidden">
      {/* gradient accent bar */}
      <div className={`h-1.5 w-full bg-gradient-to-r ${s.bar}`} />

      {/* running highlight ring */}
      {isRunning && (
        <span className="pointer-events-none absolute inset-0 rounded-2xl ring-2 ring-amber-300/60" />
      )}

      <div className="flex flex-col flex-1 p-5 gap-4">
        {/* header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className={`relative mt-0.5 flex-shrink-0 w-11 h-11 rounded-xl flex items-center justify-center ring-1 ${s.iconWrap}`}>
              <Workflow className="w-5 h-5" />
              {isIndexing && (
                <span className="absolute -inset-0.5 rounded-xl border-2 border-amber-300/70 border-t-transparent animate-spin" />
              )}
            </div>
            <div className="min-w-0">
              <h3 className="font-semibold text-slate-900 text-sm leading-snug truncate">{w.name}</h3>
              <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                <span className={`inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded-md border ${typeMeta.color}`}>
                  {typeMeta.label}
                </span>
                {isRunning ? (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold rounded-md border bg-amber-50 text-amber-700 border-amber-200">
                    <span className="relative flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-500" />
                    </span>
                    Live
                  </span>
                ) : (
                  <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded-md border ${s.pill}`}>
                    {s.spinner
                      ? <Loader2 className="w-2.5 h-2.5 animate-spin" />
                      : <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />}
                    {s.label}
                  </span>
                )}
              </div>
            </div>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity w-7 h-7 rounded-lg hover:bg-slate-100 flex items-center justify-center text-slate-500">
                <MoreHorizontal className="w-4 h-4" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuItem onClick={onEdit}>
                <Pencil className="w-3.5 h-3.5 mr-2" /> Edit
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onRun} disabled={isRunning || isIndexing}>
                <Play className="w-3.5 h-3.5 mr-2" /> Run Now
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={onDelete} className="text-red-600 focus:text-red-700 focus:bg-red-50">
                <Trash2 className="w-3.5 h-3.5 mr-2" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* meta grid */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 rounded-xl bg-slate-50/70 border border-slate-100 px-3 py-2.5">
          <MetaItem icon={CalendarClock}>{scheduleLabel}</MetaItem>
          <MetaItem icon={Clock}>{lastRun || '—'}</MetaItem>
          <MetaItem icon={Layers}>{nodeCount} node{nodeCount !== 1 ? 's' : ''}</MetaItem>
          <MetaItem icon={Power}>{w.enabled ? 'Enabled' : 'Off'}</MetaItem>
        </div>

        {/* actions */}
        <div className="flex items-center gap-2 mt-auto pt-1">
          <Button size="sm" variant="outline" className="h-9 text-xs flex-1 gap-1.5 rounded-lg" onClick={onEdit}>
            <Pencil className="w-3.5 h-3.5" /> Edit
          </Button>
          <Button
            size="sm"
            className={`h-9 text-xs flex-1 gap-1.5 rounded-lg shadow-sm ${isRunning || isIndexing ? 'bg-amber-500 hover:bg-amber-600' : ''}`}
            onClick={onRun}
            disabled={isRunning || isIndexing}
          >
            {isIndexing
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> {indexLabel}</>
              : isRunning
                ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Running…</>
                : <><Play className="w-3.5 h-3.5" /> Run</>}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ─── EmptyState ──────────────────────────────────────────────────────────────

function EmptyState({ onNew, filtered }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 px-6 text-center rounded-3xl border border-dashed border-slate-200 bg-white/60">
      <div className="relative w-20 h-20 rounded-3xl bg-gradient-to-br from-slate-100 to-slate-50 flex items-center justify-center mb-5 ring-1 ring-slate-100">
        <Workflow className="w-9 h-9 text-slate-400" />
        <span className="absolute -top-1.5 -right-1.5 w-7 h-7 rounded-full bg-gradient-to-br from-rose-500 to-red-600 flex items-center justify-center shadow-lg shadow-rose-500/30">
          <Sparkles className="w-3.5 h-3.5 text-white" />
        </span>
      </div>
      <h3 className="text-lg font-semibold text-slate-800 mb-1.5">
        {filtered ? 'No workflows match' : 'No workflows yet'}
      </h3>
      <p className="text-sm text-slate-500 max-w-sm mb-6 leading-relaxed">
        {filtered
          ? 'Try a different search term or clear the active filter.'
          : 'Workflows orchestrate AI agents, schedules, and tools into repeatable automated processes.'}
      </p>
      {!filtered && (
        <Button onClick={onNew} className="gap-2 rounded-lg">
          <Plus className="w-4 h-4" /> Create your first workflow
        </Button>
      )}
    </div>
  );
}

// ─── Toast ──────────────────────────────────────────────────────────────────

function ToastStack({ messages }) {
  const styles = {
    error:   'bg-red-50 border-red-200 text-red-800',
    warning: 'bg-amber-50 border-amber-200 text-amber-800',
    success: 'bg-emerald-50 border-emerald-200 text-emerald-800',
    info:    'bg-blue-50 border-blue-200 text-blue-800',
  };
  const icons = {
    error:   <XCircle className="w-4 h-4 flex-shrink-0" />,
    success: <CheckCircle2 className="w-4 h-4 flex-shrink-0" />,
    warning: <Zap className="w-4 h-4 flex-shrink-0" />,
    info:    <RefreshCw className="w-4 h-4 flex-shrink-0" />,
  };
  return (
    <div className="fixed bottom-5 right-5 flex flex-col gap-2 z-[200]">
      {messages.map(m => (
        <div key={m.id} className={`flex items-center gap-2.5 min-w-[260px] max-w-sm px-4 py-3 rounded-xl border text-sm font-medium shadow-lg ${styles[m.type] || styles.info}`}>
          {icons[m.type]}
          <span>{m.msg}</span>
        </div>
      ))}
    </div>
  );
}

// ─── EditorHeader ────────────────────────────────────────────────────────────

function EditorHeader({ workflowName, setWorkflowName, onSave, onCancel }) {
  return (
    <div className="h-14 flex items-center gap-3 px-4 bg-white border-b border-slate-200 z-10 flex-shrink-0">
      <button
        onClick={onCancel}
        className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors mr-1"
      >
        <ChevronLeft className="w-4 h-4" />
        <span className="hidden sm:inline">Workflows</span>
      </button>
      <div className="w-px h-5 bg-slate-200" />
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <div className="w-6 h-6 rounded-md bg-blue-50 flex items-center justify-center flex-shrink-0">
          <Workflow className="w-3.5 h-3.5 text-blue-600" />
        </div>
        <Input
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          className="h-8 text-sm font-semibold border-0 shadow-none focus-visible:ring-0 focus-visible:ring-offset-0 bg-transparent px-1 text-slate-900 min-w-0 max-w-xs"
          placeholder="Workflow name…"
        />
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <Button variant="ghost" size="sm" className="h-8 gap-1.5 text-slate-600" onClick={onCancel}>
          <X className="w-3.5 h-3.5" /> Cancel
        </Button>
        <Button size="sm" className="h-8 gap-1.5" onClick={onSave}>
          <Save className="w-3.5 h-3.5" /> Save
        </Button>
      </div>
    </div>
  );
}

function WorkflowPage() {
  const { schedules: workflows, loadSchedules, deleteSchedule, triggerWorkflow, formatTime } = useScheduler();
  const { isWorkflowRunning, markWorkflowPending, clearWorkflowPending } = useWorkflowStatus();

  const [showDialog, setShowDialog]             = useState(false);
  const [showEditor, setShowEditor]             = useState(false);
  const [searchTerm, setSearchTerm]             = useState('');
  const [statusFilter, setStatusFilter]         = useState('all');
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [deleteOpen, setDeleteOpen]             = useState(false);
  const [workflowName, setWorkflowName]         = useState('');
  const [workflowType, setWorkflowType]         = useState('workflow');
  const [currentWfData, setCurrentWfData]       = useState(null);
  const [pendingTemplate, setPendingTemplate]   = useState(null);
  const [editorKey, setEditorKey]               = useState(0);
  const [messages, setMessages]                 = useState([]);

  const editorRef = useRef(null);

  // Live repo-indexing progress (name → {progress,total}) from the background
  // job store. Polls only while at least one workflow is indexing.
  const [indexProgress, setIndexProgress] = useState({});
  const anyIndexing = useMemo(
    () => (workflows || []).some(w => w.indexingStatus === 'indexing'),
    [workflows],
  );

  useEffect(() => { loadSchedules(); }, [loadSchedules]);

  useEffect(() => {
    if (!anyIndexing) { setIndexProgress({}); return; }
    let active = true;
    const poll = async () => {
      try {
        const res = await getIndexingStatus();
        if (!active) return;
        const map = {};
        for (const j of (res.jobs || [])) map[j.target] = { progress: j.progress, total: j.total };
        setIndexProgress(map);
        // When indexing finishes server-side, refresh the workflow list so the
        // card flips out of the Indexing state.
        if (!res.indexing) loadSchedules();
      } catch { /* transient — ignore */ }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { active = false; clearInterval(id); };
  }, [anyIndexing, loadSchedules]);

  const removeMessage = useCallback((id) => setMessages(prev => prev.filter(m => m.id !== id)), []);
  const showMessage   = useCallback((msg, type = 'info') => {
    const id = Math.random().toString(36).slice(2);
    setMessages(prev => [...prev, { id, msg, type }]);
    setTimeout(() => removeMessage(id), 4000);
  }, [removeMessage]);

  const handleRun = async (w) => {
    if (isWorkflowRunning(w.name)) { showMessage(`'${w.name}' is already running`, 'warning'); return; }
    // Immediately mark as pending so the Live indicator appears right away
    markWorkflowPending(w.name);
    try {
      await triggerWorkflow(w.name);
      showMessage(`'${w.name}' started`, 'success');
    } catch (err) {
      clearWorkflowPending(w.name);
      showMessage(`Failed: ${err.message}`, 'error');
    }
  };


  const confirmDelete = async () => {
    if (!selectedWorkflow) return;
    const wasEditing = showEditor && selectedWorkflow.name === workflowName;
    try {
      await deleteSchedule(selectedWorkflow.name);
      showMessage('Workflow deleted', 'success');
      if (wasEditing) setShowEditor(false);
    } catch { showMessage('Failed to delete', 'error'); }
    finally { setDeleteOpen(false); setSelectedWorkflow(null); }
  };

  const openEdit = async (w) => {
    try {
      const fresh = await agentApiClient.getWorkflow(w.name);
      setWorkflowName(fresh.name);
      setWorkflowType(fresh.type || 'workflow');
      setCurrentWfData(fresh);
      setEditorKey(k => k + 1);
      setShowEditor(true);
    } catch { showMessage('Failed to load workflow', 'error'); }
  };

  const handleCreate = () => {
    if (!workflowName.trim()) { showMessage('Enter a workflow name', 'error'); return; }
    setCurrentWfData(null);
    setEditorKey(k => k + 1);
    setShowDialog(false);
    setShowEditor(true);
  };

  const handleSave = async () => {
    if (!editorRef.current) return;
    const { nodes, edges, enabled } = editorRef.current.getWorkflowData();
    if (!nodes?.length) { showMessage('Add at least one node', 'error'); return; }
    // Skip strict type-specific validation for the Langflow visual editor —
    // its node types (schedule, anthropic_model, etc.) differ from the legacy
    // ReactFlow schema that validateWorkflow was written for.
    try {
      const { schedule, startTime, recurrence, createdAt, updatedAt, ...base } = currentWfData || {};
      const payload = { ...base, name: workflowName, type: workflowType, nodes, edges, enabled, updatedAt: new Date().toISOString() };
      if (currentWfData) await agentApiClient.updateWorkflow(currentWfData.name, payload);
      else await agentApiClient.createWorkflow(payload);
      await loadSchedules();
      setPendingTemplate(null);
      setShowEditor(false);
      showMessage('Workflow saved', 'success');
    } catch (err) { showMessage(`Save failed: ${err.message}`, 'error'); }
  };

  // ── Derived stats + filtering ───────────────────────────────────────────────
  const stats = useMemo(() => ({
    total:     workflows.length,
    active:    workflows.filter(w => w.enabled).length,
    indexing:  workflows.filter(w => w.indexingStatus === 'indexing').length,
    scheduled: workflows.filter(w => w.schedule && w.schedule !== 'Manual').length,
  }), [workflows]);

  const FILTERS = [
    { key: 'all',       label: 'All',       count: stats.total },
    { key: 'active',    label: 'Active',    count: stats.active },
    { key: 'scheduled', label: 'Scheduled', count: stats.scheduled },
    { key: 'indexing',  label: 'Indexing',  count: stats.indexing },
  ];

  const filtered = useMemo(() => {
    const t = searchTerm.toLowerCase();
    return workflows.filter(w => {
      if (t && !w.name.toLowerCase().includes(t)) return false;
      if (statusFilter === 'active')    return w.enabled;
      if (statusFilter === 'scheduled') return w.schedule && w.schedule !== 'Manual';
      if (statusFilter === 'indexing')  return w.indexingStatus === 'indexing';
      return true;
    });
  }, [workflows, searchTerm, statusFilter]);

  const openCreate = () => { setWorkflowName(''); setPendingTemplate(null); setShowDialog(true); };

  // ── Editor view ────────────────────────────────────────────────────────────
  if (showEditor) {
    return (
      <div className="h-screen flex flex-col bg-slate-50">
        <div className="flex-1 relative overflow-hidden">
          <LangflowEditor
            key={editorKey}
            ref={editorRef}
            workflowName={workflowName}
            initialNodes={currentWfData?.nodes || pendingTemplate?.nodes || []}
            initialEdges={currentWfData?.edges || pendingTemplate?.edges || []}
            initialEnabled={currentWfData?.enabled ?? false}
            onSave={handleSave}
            onCancel={() => setShowEditor(false)}
            onRun={() => currentWfData && handleRun(currentWfData)}
            workflows={workflows}
            onSwitchWorkflow={openEdit}
          />
        </div>
        <ToastStack messages={messages} />
      </div>
    );
  }

  // ── List view ──────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 via-slate-50 to-white">

      {/* Hero header */}
      <div className="relative overflow-hidden border-b border-slate-200/80">
        {/* decorative glows */}
        <div className="pointer-events-none absolute -top-24 -right-16 w-72 h-72 rounded-full bg-blue-200/30 blur-3xl" />
        <div className="pointer-events-none absolute -top-32 left-1/3 w-72 h-72 rounded-full bg-rose-200/20 blur-3xl" />

        <div className="relative max-w-7xl mx-auto px-6 pt-8 pb-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-rose-500 to-red-600 shadow-lg shadow-rose-500/25 flex items-center justify-center">
                <Workflow className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Workflows</h1>
                <p className="text-sm text-slate-500 mt-0.5">
                  Orchestrate agents, schedules, and tools into automated processes.
                </p>
              </div>
            </div>
            <Button onClick={openCreate} className="gap-2 self-start sm:self-auto rounded-lg shadow-sm shadow-rose-500/20">
              <Plus className="w-4 h-4" /> New Workflow
            </Button>
          </div>

          {/* Stat cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-6">
            <StatCard icon={Layers}       label="Total"     value={stats.total}     accent="bg-slate-50 text-slate-600"     ring="ring-slate-100" />
            <StatCard icon={Activity}     label="Active"    value={stats.active}    accent="bg-emerald-50 text-emerald-600" ring="ring-emerald-100" />
            <StatCard icon={CalendarClock} label="Scheduled" value={stats.scheduled} accent="bg-blue-50 text-blue-600"       ring="ring-blue-100" />
            <StatCard icon={RefreshCw}    label="Indexing"  value={stats.indexing}  accent="bg-amber-50 text-amber-600"     ring="ring-amber-100" />
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-7xl mx-auto px-6 py-6">

        {/* Toolbar: search + filter pills */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-6">
          <div className="relative w-full sm:max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
            <Input
              placeholder="Search workflows…"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9 h-10 bg-white rounded-lg shadow-sm"
            />
          </div>

          <div className="flex items-center gap-1 p-1 rounded-xl bg-slate-100/80 border border-slate-200/80 w-fit">
            {FILTERS.map(f => (
              <button
                key={f.key}
                onClick={() => setStatusFilter(f.key)}
                className={`inline-flex items-center gap-1.5 px-3 h-8 rounded-lg text-xs font-medium transition-all ${
                  statusFilter === f.key
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {f.label}
                <span className={`inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-md text-[10px] font-semibold ${
                  statusFilter === f.key ? 'bg-slate-100 text-slate-600' : 'bg-slate-200/70 text-slate-500'
                }`}>
                  {f.count}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Grid */}
        {filtered.length === 0 ? (
          <EmptyState onNew={openCreate} filtered={searchTerm.length > 0 || statusFilter !== 'all'} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map(w => (
              <WorkflowCard
                key={w.name}
                w={w}
                isRunning={isWorkflowRunning(w.name)}
                indexProgress={indexProgress[w.name]}
                onEdit={() => openEdit(w)}
                onRun={() => handleRun(w)}
                onDelete={() => { setSelectedWorkflow(w); setDeleteOpen(true); }}
              />
            ))}
          </div>
        )}
      </div>

      {/* Delete dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete workflow?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">"{selectedWorkflow?.name}"</span> will be permanently removed. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Create dialog */}
      <Dialog open={showDialog} onOpenChange={(open) => { setShowDialog(open); if (!open) setPendingTemplate(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create workflow</DialogTitle>
            <DialogDescription>Name it, choose a type, and optionally start from a template.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-slate-700">Name</label>
              <Input
                value={workflowName}
                onChange={(e) => setWorkflowName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
                placeholder="e.g. Daily Log Analyzer"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-slate-700">Type</label>
              <select
                value={workflowType}
                onChange={(e) => setWorkflowType(e.target.value)}
                className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="workflow">Standard Workflow</option>
                <option value="agent">Agentic Process</option>
              </select>
            </div>
            <TemplateGallery
              selectedId={pendingTemplate?.id ?? null}
              onSelect={setPendingTemplate}
            />
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setShowDialog(false)}>Cancel</Button>
            <Button onClick={handleCreate}>Create &amp; Design</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ToastStack messages={messages} />
    </div>
  );
}

export default WorkflowPage;
