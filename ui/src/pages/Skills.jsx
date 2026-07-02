import React, { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog';
import {
  Loader2, Plus, Trash2, RefreshCw, Wrench, FileText, Database, Eye, Pencil, Upload,
} from 'lucide-react';
import agentApiClient from '../services/agentApiClient.js';

const STATUS_VARIANT = { active: 'success', draft: 'warning', archived: 'muted' };

const EMPTY_FORM = { name: '', title: '', description: '', trigger_patterns: '', steps: '' };

const FS_SKILL_TEMPLATE = `---
name: my_new_skill
description: One paragraph describing when to use this skill.
---

## Protocol

1. Step one.
2. Step two.

## Rules

- Rule one.
`;

function skillToForm(s) {
  return {
    name: s.name || '',
    title: s.title || '',
    description: s.description || '',
    trigger_patterns: (s.trigger_patterns || []).join(', '),
    steps: s.steps?.length ? JSON.stringify(s.steps, null, 2) : '',
  };
}

export default function Skills() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dbSkills, setDbSkills] = useState([]);
  const [fsSkills, setFsSkills] = useState([]);

  // Add / Edit dialog
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null); // null = add mode, skill obj = edit mode
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);

  // View dialog
  const [viewSkill, setViewSkill] = useState(null);

  // Filesystem (markdown) skill add/edit/view dialogs
  const [fsEditOpen, setFsEditOpen] = useState(false);
  const [fsEditTarget, setFsEditTarget] = useState(null); // null = add mode, name string = edit mode
  const [fsSaving, setFsSaving] = useState(false);
  const [fsLoading, setFsLoading] = useState(false);
  const [fsForm, setFsForm] = useState({ name: '', content: FS_SKILL_TEMPLATE });
  const [fsViewSkill, setFsViewSkill] = useState(null); // { name, content, editable }
  const fsFileInputRef = React.useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await agentApiClient.listSkills();
      setDbSkills(data.db || []);
      setFsSkills(data.filesystem || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // ── Open add dialog ──────────────────────────────────────────────────────
  const openAdd = () => {
    setEditTarget(null);
    setForm(EMPTY_FORM);
    setAddOpen(true);
  };

  // ── Open edit dialog ─────────────────────────────────────────────────────
  const openEdit = (skill) => {
    setEditTarget(skill);
    setForm(skillToForm(skill));
    setAddOpen(true);
  };

  // ── Save (add or edit) ───────────────────────────────────────────────────
  const handleSave = async () => {
    setSaving(true);
    try {
      let steps = [];
      let triggers = [];
      if (form.steps.trim()) {
        try { steps = JSON.parse(form.steps); }
        catch { alert('Steps must be valid JSON (an array).'); setSaving(false); return; }
      }
      if (form.trigger_patterns.trim()) {
        triggers = form.trigger_patterns.split(',').map((s) => s.trim()).filter(Boolean);
      }
      const body = {
        name: editTarget ? editTarget.name : form.name,
        title: form.title,
        description: form.description,
        trigger_patterns: triggers,
        steps,
      };
      if (editTarget) {
        await agentApiClient.updateSkill(editTarget.name, body);
      } else {
        await agentApiClient.createSkill(body);
      }
      setAddOpen(false);
      setEditTarget(null);
      setForm(EMPTY_FORM);
      await load();
    } catch (e) {
      alert(e?.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name) => {
    if (!window.confirm(`Delete skill "${name}"? This cannot be undone.`)) return;
    try { await agentApiClient.deleteSkill(name); await load(); }
    catch (e) { alert(e?.response?.data?.detail || e.message); }
  };

  const handleDeleteFs = async (name) => {
    if (!window.confirm(`Delete filesystem skill "${name}"? This removes the file from disk and cannot be undone.`)) return;
    try { await agentApiClient.deleteFsSkill(name); await load(); }
    catch (e) { alert(e?.response?.data?.detail || e.message); }
  };

  // ── Filesystem (markdown) skill: view / add / edit ──────────────────────
  const openFsAdd = () => {
    setFsEditTarget(null);
    setFsForm({ name: '', content: FS_SKILL_TEMPLATE });
    setFsEditOpen(true);
  };

  const openFsView = async (name) => {
    setFsLoading(true);
    try {
      const data = await agentApiClient.getFsSkill(name);
      setFsViewSkill(data.skill);
    } catch (e) {
      alert(e?.response?.data?.detail || e.message);
    } finally {
      setFsLoading(false);
    }
  };

  const openFsEdit = async (name) => {
    setFsLoading(true);
    try {
      const data = await agentApiClient.getFsSkill(name);
      setFsEditTarget(name);
      setFsForm({ name, content: data.skill.content });
      setFsViewSkill(null);
      setFsEditOpen(true);
    } catch (e) {
      alert(e?.response?.data?.detail || e.message);
    } finally {
      setFsLoading(false);
    }
  };

  const handleFsSave = async () => {
    if (!fsEditTarget && !fsForm.name.trim()) {
      alert('Name is required.');
      return;
    }
    setFsSaving(true);
    try {
      if (fsEditTarget) {
        await agentApiClient.updateFsSkill(fsEditTarget, fsForm.content);
      } else {
        await agentApiClient.createFsSkill(fsForm.name, fsForm.content);
      }
      setFsEditOpen(false);
      setFsEditTarget(null);
      await load();
    } catch (e) {
      alert(e?.response?.data?.detail || e.message);
    } finally {
      setFsSaving(false);
    }
  };

  const handleFsFileUpload = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!/\.mdx?$/i.test(file.name)) {
      alert('Please select a .md or .mdx file.');
      return;
    }
    try {
      const content = await file.text();
      setFsForm((prev) => ({
        name: isFsEditMode ? prev.name : (prev.name || file.name.replace(/\.mdx?$/i, '')),
        content,
      }));
    } catch (e) {
      alert(e.message || 'Failed to read file');
    }
  };

  const isFsEditMode = fsEditTarget !== null;

  const handleDeleteAll = async () => {
    if (!window.confirm(`Delete ALL ${dbSkills.length} DB skills? This is irreversible.`)) return;
    try { await agentApiClient.deleteAllSkills(); await load(); }
    catch (e) { alert(e?.response?.data?.detail || e.message); }
  };

  const isEditMode = editTarget !== null;

  return (
    <div className="p-8 min-h-screen bg-background">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold flex items-center gap-2">
              <Wrench className="size-6 text-primary" /> Skills
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Executable skills (file-backed, callable via the agent's execute_skill tool) and read-only markdown guidance.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1.5">
              <RefreshCw className={loading ? 'size-3.5 animate-spin' : 'size-3.5'} /> Refresh
            </Button>
            <Button size="sm" className="gap-1.5" onClick={openFsAdd}>
              <Plus className="size-3.5" /> Add skill
            </Button>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {/* Add / Edit dialog */}
        <Dialog open={addOpen} onOpenChange={(o) => { if (!o) { setAddOpen(false); setEditTarget(null); } }}>
          <DialogContent className="sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>{isEditMode ? `Edit skill — ${editTarget.name}` : 'Add skill'}</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              {!isEditMode && (
                <div>
                  <label className="text-xs font-semibold text-muted-foreground">Name</label>
                  <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="restart_api_pods" />
                </div>
              )}
              <div>
                <label className="text-xs font-semibold text-muted-foreground">Title</label>
                <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Restart API pods" />
              </div>
              <div>
                <label className="text-xs font-semibold text-muted-foreground">Description</label>
                <Textarea rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </div>
              <div>
                <label className="text-xs font-semibold text-muted-foreground">Trigger patterns (comma-separated)</label>
                <Input value={form.trigger_patterns} onChange={(e) => setForm({ ...form, trigger_patterns: e.target.value })} placeholder="pods crashing, restart deployment" />
              </div>
              <div>
                <label className="text-xs font-semibold text-muted-foreground">Steps (JSON array)</label>
                <Textarea rows={5} className="font-mono text-xs" value={form.steps} onChange={(e) => setForm({ ...form, steps: e.target.value })} placeholder='[{"order":1,"description":"...","tool":"...","args_template":{}}]' />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => { setAddOpen(false); setEditTarget(null); }}>Cancel</Button>
              <Button onClick={handleSave} disabled={saving || (!isEditMode && !form.name.trim())}>
                {saving ? <Loader2 className="size-4 animate-spin" /> : isEditMode ? 'Update' : 'Save'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* View detail dialog */}
        <Dialog open={!!viewSkill} onOpenChange={(o) => { if (!o) setViewSkill(null); }}>
          <DialogContent className="sm:max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Eye className="size-4" />
                {viewSkill?.title || viewSkill?.name}
              </DialogTitle>
            </DialogHeader>
            {viewSkill && (
              <div className="space-y-4 text-sm">
                <div className="flex gap-2 flex-wrap">
                  <Badge variant={STATUS_VARIANT[viewSkill.status] || 'muted'}>{viewSkill.status}</Badge>
                  <Badge variant="outline">{viewSkill.source}</Badge>
                  {viewSkill.confidence != null && (
                    <Badge variant="outline">conf {Math.round(viewSkill.confidence * 100)}%</Badge>
                  )}
                </div>

                {viewSkill.description && (
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground mb-1">Description</p>
                    <p className="text-sm">{viewSkill.description}</p>
                  </div>
                )}

                {viewSkill.trigger_patterns?.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground mb-1">Trigger patterns</p>
                    <div className="flex flex-wrap gap-1">
                      {viewSkill.trigger_patterns.map((p) => (
                        <span key={p} className="inline-block bg-muted rounded px-2 py-0.5 text-xs">{p}</span>
                      ))}
                    </div>
                  </div>
                )}

                {viewSkill.steps?.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-muted-foreground mb-1">Steps ({viewSkill.steps.length})</p>
                    <pre className="bg-muted rounded p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap">
                      {JSON.stringify(viewSkill.steps, null, 2)}
                    </pre>
                  </div>
                )}

                <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground border-t pt-3">
                  <div><span className="font-semibold">Recall</span><br />{viewSkill.recall_count ?? '—'}</div>
                  <div><span className="font-semibold">Success</span><br />{viewSkill.success_count ?? '—'}</div>
                  <div><span className="font-semibold">Workflow</span><br />{viewSkill.workflow_name || '—'}</div>
                </div>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setViewSkill(null)}>Close</Button>
              <Button onClick={() => { setViewSkill(null); openEdit(viewSkill); }}>
                <Pencil className="size-3.5 mr-1.5" /> Edit
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* DB skills */}
        <div className="mb-8">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold flex items-center gap-1.5">
              <Database className="size-4 text-slate-500" /> Skills ({dbSkills.length})
            </h2>
            {dbSkills.length > 0 && (
              <Button variant="destructive" size="sm" onClick={handleDeleteAll} className="gap-1.5">
                <Trash2 className="size-3.5" /> Delete all
              </Button>
            )}
          </div>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground p-4"><Loader2 className="size-4 animate-spin" /> Loading…</div>
          ) : dbSkills.length === 0 ? (
            <p className="text-sm text-muted-foreground p-4 border rounded-lg">No skills yet. They are auto-distilled from runs (when auto-learn is on) or added here.</p>
          ) : (
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-xs text-muted-foreground">
                  <tr>
                    <th className="text-left px-3 py-2 font-semibold">Name</th>
                    <th className="text-left px-3 py-2 font-semibold">Status</th>
                    <th className="text-left px-3 py-2 font-semibold">Source</th>
                    <th className="text-right px-3 py-2 font-semibold">Conf</th>
                    <th className="text-right px-3 py-2 font-semibold">Recall</th>
                    <th className="text-right px-3 py-2 font-semibold">Success</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {dbSkills.map((s) => (
                    <tr key={s.id || s.name} className="border-t hover:bg-muted/20">
                      <td className="px-3 py-2">
                        <div className="font-mono text-xs font-semibold">{s.name}</div>
                        {s.title && <div className="text-[11px] text-muted-foreground">{s.title}</div>}
                      </td>
                      <td className="px-3 py-2"><Badge variant={STATUS_VARIANT[s.status] || 'muted'}>{s.status}</Badge></td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{s.source}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{s.confidence != null ? `${Math.round(s.confidence * 100)}%` : '—'}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{s.recall_count}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{s.success_count}</td>
                      <td className="px-3 py-2 text-right">
                        <div className="flex items-center justify-end gap-0.5">
                          <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-foreground" title="View details" onClick={() => setViewSkill(s)}>
                            <Eye className="size-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-foreground" title="Edit" onClick={() => openEdit(s)}>
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-red-600" title="Delete" onClick={() => handleDelete(s.name)}>
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Filesystem skills */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold flex items-center gap-1.5">
              <FileText className="size-4 text-slate-500" /> Guidance (markdown) ({fsSkills.length})
            </h2>
            <Button variant="outline" size="sm" className="gap-1.5" onClick={openFsAdd}>
              <Plus className="size-3.5" /> Add markdown skill
            </Button>
          </div>
          {fsSkills.length === 0 ? (
            <p className="text-sm text-muted-foreground p-4 border rounded-lg">No filesystem skills found.</p>
          ) : (
            <div className="grid sm:grid-cols-2 gap-2">
              {fsSkills.map((s) => (
                <div key={s.name} className="border rounded-lg p-3 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-mono text-xs font-semibold">{s.name}</div>
                    {s.description && <div className="text-[11px] text-muted-foreground mt-1 line-clamp-3">{s.description}</div>}
                  </div>
                  <div className="flex items-center gap-0.5 shrink-0">
                    <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-foreground" title="View source" disabled={fsLoading} onClick={() => openFsView(s.name)}>
                      <Eye className="size-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-foreground" title="Edit" disabled={fsLoading} onClick={() => openFsEdit(s.name)}>
                      <Pencil className="size-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-red-600" title="Delete filesystem skill" onClick={() => handleDeleteFs(s.name)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Filesystem skill view (raw source) dialog */}
        <Dialog open={!!fsViewSkill} onOpenChange={(o) => { if (!o) setFsViewSkill(null); }}>
          <DialogContent className="sm:max-w-2xl max-h-[80vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Eye className="size-4" /> {fsViewSkill?.name}
              </DialogTitle>
            </DialogHeader>
            {fsViewSkill && (
              <pre className="bg-muted rounded p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap">
                {fsViewSkill.content}
              </pre>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setFsViewSkill(null)}>Close</Button>
              {fsViewSkill?.editable !== false && (
                <Button onClick={() => { const name = fsViewSkill.name; setFsViewSkill(null); openFsEdit(name); }}>
                  <Pencil className="size-3.5 mr-1.5" /> Edit
                </Button>
              )}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Filesystem skill add/edit dialog */}
        <Dialog open={fsEditOpen} onOpenChange={(o) => { if (!o) { setFsEditOpen(false); setFsEditTarget(null); } }}>
          <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{isFsEditMode ? `Edit markdown skill — ${fsEditTarget}` : 'Add markdown skill'}</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              {!isFsEditMode && (
                <div>
                  <label className="text-xs font-semibold text-muted-foreground">Name</label>
                  <Input
                    value={fsForm.name}
                    onChange={(e) => setFsForm({ ...fsForm, name: e.target.value })}
                    placeholder="my_new_skill"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Used as the directory name; the frontmatter <code>name</code> field below is what agents match on.
                  </p>
                </div>
              )}
              <div>
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-muted-foreground">SKILL.md content</label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="gap-1.5 h-6 px-2 text-xs"
                    onClick={() => fsFileInputRef.current?.click()}
                  >
                    <Upload className="size-3" /> Upload .md file
                  </Button>
                  <input
                    ref={fsFileInputRef}
                    type="file"
                    accept=".md,.mdx,text/markdown"
                    className="hidden"
                    onChange={handleFsFileUpload}
                  />
                </div>
                <Textarea
                  rows={16}
                  className="font-mono text-xs"
                  value={fsForm.content}
                  onChange={(e) => setFsForm({ ...fsForm, content: e.target.value })}
                />
                <p className="text-[11px] text-muted-foreground mt-1">
                  Must include YAML frontmatter fenced by <code>---</code> with at least a <code>name</code> field.
                  You can paste content directly or upload a <code>.md</code> file.
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => { setFsEditOpen(false); setFsEditTarget(null); }}>Cancel</Button>
              <Button onClick={handleFsSave} disabled={fsSaving || (!isFsEditMode && !fsForm.name.trim())}>
                {fsSaving ? <Loader2 className="size-4 animate-spin" /> : isFsEditMode ? 'Update' : 'Save'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
