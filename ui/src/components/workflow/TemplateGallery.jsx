import React from 'react';
import { FilePlus2, Cloud, Search, CalendarClock, Layers, Route } from 'lucide-react';
import { WORKFLOW_TEMPLATES } from '../../data/workflowTemplates.js';

const ICONS = {
  cloud: Cloud,
  search: Search,
  calendar: CalendarClock,
  layers: Layers,
  route: Route,
};

// Card list shown inside the "New Workflow" dialog. A "Blank" option plus one
// card per prebuilt template. Selecting a card calls onSelect(template) — or
// onSelect(null) for Blank. `selectedId` (null = blank) drives the highlight.
export default function TemplateGallery({ selectedId = null, onSelect }) {
  const options = [
    { id: null, label: 'Blank', description: 'Start from an empty canvas.', icon: FilePlus2, template: null },
    ...WORKFLOW_TEMPLATES.map((t) => ({
      id: t.id,
      label: t.label,
      description: t.description,
      icon: ICONS[t.icon] || FilePlus2,
      template: t,
    })),
  ];

  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium text-slate-700">Start from</label>
      <div className="grid grid-cols-1 gap-2 max-h-64 overflow-y-auto pr-1">
        {options.map((opt) => {
          const Icon = opt.icon;
          const active = opt.id === selectedId;
          return (
            <button
              key={opt.id ?? 'blank'}
              type="button"
              onClick={() => onSelect?.(opt.template)}
              className={[
                'flex items-start gap-3 rounded-lg border p-3 text-left transition-colors',
                active
                  ? 'border-slate-900 bg-slate-50 ring-1 ring-slate-900'
                  : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50',
              ].join(' ')}
            >
              <span
                className={[
                  'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
                  active ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600',
                ].join(' ')}
              >
                <Icon className="h-4 w-4" />
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-slate-900">{opt.label}</span>
                <span className="block text-xs text-slate-500 leading-snug">{opt.description}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
