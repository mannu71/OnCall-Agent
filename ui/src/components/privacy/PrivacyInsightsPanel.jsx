import React from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Badge } from '@/components/ui/badge';
import { ShieldCheck, Shield } from 'lucide-react';

/**
 * PrivacyInsightsPanel — auditability surface for the PII pseudonymization layer.
 *
 * Renders a shield badge showing how many PII entities were scrubbed before the
 * turn reached Bedrock; clicking it opens a Sheet with a per-entity table of
 * `type → placeholder → masked preview`. The previews are masked server-side —
 * this component never receives (and must never display) raw PII values.
 *
 * Props:
 *   redactions: Array<{ type, placeholder, preview }>  (UI-safe summary)
 */
export default function PrivacyInsightsPanel({ redactions }) {
  const rows = Array.isArray(redactions) ? redactions : [];
  if (rows.length === 0) return null;

  // Count per entity type for the at-a-glance summary chips.
  const byType = rows.reduce((acc, r) => {
    const t = r.type || 'PII';
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {});

  return (
    <Sheet>
      <SheetTrigger asChild>
        <button
          type="button"
          title={`${rows.length} PII ${rows.length === 1 ? 'entity' : 'entities'} pseudonymized before reaching the model`}
          className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 transition-colors dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300 dark:hover:bg-emerald-500/20"
        >
          <ShieldCheck className="h-3.5 w-3.5" />
          {rows.length} scrubbed
        </button>
      </SheetTrigger>
      <SheetContent className="w-full sm:max-w-md overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Shield className="h-4 w-4 text-emerald-600" />
            Privacy filter
          </SheetTitle>
        </SheetHeader>

        <p className="mt-2 text-xs text-slate-500">
          These values were detected and replaced with stable placeholders
          <span className="mx-1 font-mono text-slate-700">[TYPE_n]</span>
          before this turn was sent to the model. The model only ever saw the
          placeholders; the answer above was re-hydrated for you. Previews are
          masked — raw values never leave the server.
        </p>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {Object.entries(byType).map(([t, n]) => (
            <Badge key={t} variant="secondary" className="text-[11px]">
              {t} · {n}
            </Badge>
          ))}
        </div>

        <table className="mt-4 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-slate-200 dark:border-white/10 text-left text-xs text-slate-500 dark:text-slate-400">
              <th className="py-1.5 pr-2 font-medium">Type</th>
              <th className="py-1.5 pr-2 font-medium">Placeholder</th>
              <th className="py-1.5 font-medium">Preview</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.placeholder || i} className="border-b border-slate-100 dark:border-white/[0.06]">
                <td className="py-1.5 pr-2 text-slate-600 dark:text-slate-300">{r.type}</td>
                <td className="py-1.5 pr-2 font-mono text-xs text-indigo-700 dark:text-indigo-300">{r.placeholder}</td>
                <td className="py-1.5 font-mono text-xs text-slate-500 dark:text-slate-400">{r.preview}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </SheetContent>
    </Sheet>
  );
}
