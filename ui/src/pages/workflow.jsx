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
  CalendarClock, RefreshCw,
} from 'lucide-react';
import LangflowEditor from '../components/workflow/LangflowEditor.jsx';
import { validateWorkflow } from '../utils/workflowValidation.js';
import agentApiClient from '../services/agentApiClient.js';
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

// ─── WorkflowCard ────────────────────────────────────────────────────────────

function WorkflowCard({ w, isRunning, onEdit, onRun, onDelete }) {
  const typeMeta = TYPE_META[w.type] || TYPE_META.workflow;
  const scheduleLabel = formatScheduleLabel(w);
  const lastRun = relativeTime(w.updatedAt || w.updated_at);

  return (
    <div className="group relative flex flex-col bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-md hover:border-slate-300 transition-all duration-200 overflow-hidden">
      {/* accent bar */}
      <div className={`h-1 w-full ${w.enabled ? 'bg-emerald-400' : 'bg-slate-200'}`} />

      <div className="flex flex-col flex-1 p-5 gap-4">
        {/* header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className={`mt-0.5 flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center ${
              w.enabled ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-400'
            }`}>
              <Workflow className="w-[18px] h-[18px]" />
            </div>
            <div className="min-w-0">
              <h3 className="font-semibold text-slate-900 text-sm leading-snug truncate">{w.name}</h3>
              <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                <span className={`inline-flex items-center px-1.5 py-0.5 text-[10px] font-medium rounded border ${typeMeta.color}`}>
                  {typeMeta.label}
                </span>
                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded border ${
                  w.enabled
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'bg-slate-50 text-slate-500 border-slate-200'
                }`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${w.enabled ? 'bg-emerald-500' : 'bg-slate-400'}`} />
                  {w.enabled ? 'Active' : 'Disabled'}
                </span>
              </div>
            </div>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity w-7 h-7 rounded-md hover:bg-slate-100 flex items-center justify-center text-slate-500">
                <MoreHorizontal className="w-4 h-4" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuItem onClick={onEdit}>
                <Pencil className="w-3.5 h-3.5 mr-2" /> Edit
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onRun} disabled={isRunning}>
                <Play className="w-3.5 h-3.5 mr-2" /> Run Now
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={onDelete} className="text-red-600 focus:text-red-700 focus:bg-red-50">
                <Trash2 className="w-3.5 h-3.5 mr-2" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        {/* meta row */}
        <div className="flex items-center gap-4 text-[11px] text-slate-500">
          <div className="flex items-center gap-1.5">
            <CalendarClock className="w-3.5 h-3.5 text-slate-400" />
            <span>{scheduleLabel}</span>
          </div>
          {lastRun && (
            <div className="flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5 text-slate-400" />
              <span>{lastRun}</span>
            </div>
          )}
        </div>

        {/* actions */}
        <div className="flex items-center gap-2 mt-auto pt-1 border-t border-slate-100">
          <Button size="sm" variant="outline" className="h-8 text-xs flex-1 gap-1.5" onClick={onEdit}>
            <Pencil className="w-3 h-3" /> Edit
          </Button>
          <Button
            size="sm"
            className={`h-8 text-xs flex-1 gap-1.5 ${isRunning ? 'bg-amber-500 hover:bg-amber-600' : ''}`}
            onClick={onRun}
            disabled={isRunning}
          >
            {isRunning
              ? <><Loader2 className="w-3 h-3 animate-spin" /> Running…</>
              : <><Play className="w-3 h-3" /> Run</>}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ─── EmptyState ──────────────────────────────────────────────────────────────

function EmptyState({ onNew, filtered }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
      <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mb-5">
        <Workflow className="w-8 h-8 text-slate-400" />
      </div>
      <h3 className="text-base font-semibold text-slate-800 mb-1">
        {filtered ? 'No workflows match' : 'No workflows yet'}
      </h3>
      <p className="text-sm text-slate-500 max-w-xs mb-6">
        {filtered
          ? 'Try a different search term.'
          : 'Workflows orchestrate AI agents, schedules, and tools into repeatable automated processes.'}
      </p>
      {!filtered && (
        <Button size="sm" onClick={onNew} className="gap-2">
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
        <div key={m.id} className={`flex items-center gap-2.5 min-w-[260px] max-w-sm px-4 py-3 rounded-xl border text-sm font-medium shadow-sm ${styles[m.type] || styles.info}`}>
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
  const [selectedWorkflow, setSelectedWorkflow] = useState(null);
  const [deleteOpen, setDeleteOpen]             = useState(false);
  const [workflowName, setWorkflowName]         = useState('');
  const [workflowType, setWorkflowType]         = useState('workflow');
  const [currentWfData, setCurrentWfData]       = useState(null);
  const [editorKey, setEditorKey]               = useState(0);
  const [messages, setMessages]                 = useState([]);

  const editorRef = useRef(null);

  useEffect(() => { loadSchedules(); }, [loadSchedules]);

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
      setShowEditor(false);
      showMessage('Workflow saved', 'success');
    } catch (err) { showMessage(`Save failed: ${err.message}`, 'error'); }
  };

  const filtered = useMemo(() => {
    const t = searchTerm.toLowerCase();
    return workflows.filter(w => w.name.toLowerCase().includes(t));
  }, [workflows, searchTerm]);

  // ── Editor view ────────────────────────────────────────────────────────────
  if (showEditor) {
    return (
      <div className="h-screen flex flex-col bg-slate-50">
        <div className="flex-1 relative overflow-hidden">
          <LangflowEditor
            key={editorKey}
            ref={editorRef}
            workflowName={workflowName}
            initialNodes={currentWfData?.nodes || []}
            initialEdges={currentWfData?.edges || []}
            initialEnabled={currentWfData?.enabled ?? true}
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
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-7xl mx-auto px-6 py-8">

        {/* Page header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Workflows</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {workflows.length} workflow{workflows.length !== 1 ? 's' : ''} &middot; {workflows.filter(w => w.enabled).length} active
            </p>
          </div>
          <Button onClick={() => { setWorkflowName(''); setShowDialog(true); }} className="gap-2 self-start sm:self-auto">
            <Plus className="w-4 h-4" /> New Workflow
          </Button>
        </div>

        {/* Search */}
        <div className="relative mb-6 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <Input
            placeholder="Search workflows…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 h-9 bg-white"
          />
        </div>

        {/* Grid */}
        {filtered.length === 0 ? (
          <EmptyState onNew={() => { setWorkflowName(''); setShowDialog(true); }} filtered={searchTerm.length > 0} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {filtered.map(w => (
              <WorkflowCard
                key={w.name}
                w={w}
                isRunning={isWorkflowRunning(w.name)}
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
      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Create workflow</DialogTitle>
            <DialogDescription>Give your workflow a name and choose its type.</DialogDescription>
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
