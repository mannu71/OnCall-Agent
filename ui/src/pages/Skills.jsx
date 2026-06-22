import React, { useState, useEffect, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog';
import {
  Loader2, Plus, Trash2, RefreshCw, Wrench, FileText, Database,
} from 'lucide-react';
import agentApiClient from '../services/agentApiClient.js';

const STATUS_VARIANT = { active: 'success', draft: 'warning', archived: 'muted' };

export default function Skills() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dbSkills, setDbSkills] = useState([]);
  const [fsSkills, setFsSkills] = useState([]);
  const [addOpen, setAddOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ name: '', title: '', description: '', trigger_patterns: '', steps: '' });

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

  const handleDelete = async (name) => {
    if (!window.confirm(`Delete skill "${name}"? This cannot be undone.`)) return;
    try { await agentApiClient.deleteSkill(name); await load(); }
    catch (e) { alert(e?.response?.data?.detail || e.message); }
  };

  const handleDeleteAll = async () => {
    if (!window.confirm(`Delete ALL ${dbSkills.length} DB skills? This is irreversible.`)) return;
    try { await agentApiClient.deleteAllSkills(); await load(); }
    catch (e) { alert(e?.response?.data?.detail || e.message); }
  };

  const handleAdd = async () => {
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
      await agentApiClient.createSkill({
        name: form.name, title: form.title, description: form.description,
        trigger_patterns: triggers, steps,
      });
      setAddOpen(false);
      setForm({ name: '', title: '', description: '', trigger_patterns: '', steps: '' });
      await load();
    } catch (e) {
      alert(e?.response?.data?.detail || e.message);
    } finally {
      setSaving(false);
    }
  };

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
            <Dialog open={addOpen} onOpenChange={setAddOpen}>
              <DialogTrigger asChild>
                <Button size="sm" className="gap-1.5"><Plus className="size-3.5" /> Add skill</Button>
              </DialogTrigger>
              <DialogContent className="sm:max-w-lg">
                <DialogHeader><DialogTitle>Add skill</DialogTitle></DialogHeader>
                <div className="space-y-3">
                  <div>
                    <label className="text-xs font-semibold text-muted-foreground">Name</label>
                    <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="restart_api_pods" />
                  </div>
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
                    <Textarea rows={4} className="font-mono text-xs" value={form.steps} onChange={(e) => setForm({ ...form, steps: e.target.value })} placeholder='[{"order":1,"description":"...","tool":"...","args_template":{}}]' />
                  </div>
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setAddOpen(false)}>Cancel</Button>
                  <Button onClick={handleAdd} disabled={saving || !form.name.trim()}>
                    {saving ? <Loader2 className="size-4 animate-spin" /> : 'Save'}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

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
            <p className="text-sm text-muted-foreground p-4 border rounded-lg">No skills yet. They are auto-distilled from runs (when auto-learn is on) or added here. Stored as files, not in the database.</p>
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
                    <tr key={s.id || s.name} className="border-t">
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
                        <Button variant="ghost" size="icon" className="size-7 text-slate-400 hover:text-red-600" title="Delete" onClick={() => handleDelete(s.name)}>
                          <Trash2 className="size-3.5" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Filesystem skills (read-only) */}
        <div>
          <h2 className="text-sm font-semibold flex items-center gap-1.5 mb-2">
            <FileText className="size-4 text-slate-500" /> Guidance (markdown) ({fsSkills.length}) <span className="text-[11px] font-normal text-muted-foreground">— read-only</span>
          </h2>
          {fsSkills.length === 0 ? (
            <p className="text-sm text-muted-foreground p-4 border rounded-lg">No filesystem skills found.</p>
          ) : (
            <div className="grid sm:grid-cols-2 gap-2">
              {fsSkills.map((s) => (
                <div key={s.name} className="border rounded-lg p-3">
                  <div className="font-mono text-xs font-semibold">{s.name}</div>
                  {s.description && <div className="text-[11px] text-muted-foreground mt-1 line-clamp-3">{s.description}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
