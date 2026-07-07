import React, { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import {
  Loader2, Plus, Trash2, RefreshCw, Wrench, FileText, Eye, Pencil, Upload,
} from 'lucide-react';
import agentApiClient from '../services/agentApiClient.js';

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

export default function Skills() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [fsSkills, setFsSkills] = useState([]);

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
      setFsSkills(data.filesystem || []);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleDeleteFs = async (name) => {
    if (!window.confirm(`Delete skill "${name}"? This removes the file from disk and cannot be undone.`)) return;
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

  return (
    <div className="p-8 min-h-screen bg-background">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold flex items-center gap-2">
              <Wrench className="size-6 text-primary" /> Skills
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              File-based markdown (SKILL.md) skills — reusable guidance the agent auto-selects per query or runs by slash-command.
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

        {/* Filesystem skills */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold flex items-center gap-1.5">
              <FileText className="size-4 text-slate-500" /> Skills ({fsSkills.length})
            </h2>
          </div>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground p-4"><Loader2 className="size-4 animate-spin" /> Loading…</div>
          ) : fsSkills.length === 0 ? (
            <p className="text-sm text-muted-foreground p-4 border rounded-lg">No skills found. Add one with the button above.</p>
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
                    <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-red-600" title="Delete skill" onClick={() => handleDeleteFs(s.name)}>
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
              <DialogTitle>{isFsEditMode ? `Edit skill — ${fsEditTarget}` : 'Add skill'}</DialogTitle>
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
