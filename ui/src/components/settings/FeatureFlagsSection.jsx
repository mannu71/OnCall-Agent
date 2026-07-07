import React, { useMemo, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import { CARD, SField, SToggle, SInput, SButton } from './settings-ui';

/**
 * Runtime feature-flag editor. Renders the backend flag catalog grouped by area.
 * Booleans save immediately on toggle; numbers/text commit on blur or Enter.
 *
 * Props:
 *   flags:    array of { key, label, group, type, help, value, default }
 *   onChange: (key, value) => void   // value === null resets to default
 *   busy:     boolean                // disables controls while a save is in-flight
 */
export default function FeatureFlagsSection({ flags = [], onChange, busy = false }) {
    const groups = useMemo(() => {
        const byGroup = new Map();
        for (const f of flags) {
            if (!byGroup.has(f.group)) byGroup.set(f.group, []);
            byGroup.get(f.group).push(f);
        }
        return Array.from(byGroup.entries());
    }, [flags]);

    if (!flags.length) {
        return (
            <div className="py-12 text-center text-sm text-slate-400">
                No feature flags available.
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-6">
            {groups.map(([group, groupFlags]) => (
                <div key={group}>
                    <h3 className="mb-2 px-1 text-[11px] font-bold uppercase tracking-wider text-slate-500">
                        {group}
                    </h3>
                    <div className={CARD}>
                        {groupFlags.map((flag) => (
                            <FlagRow key={flag.key} flag={flag} onChange={onChange} busy={busy} />
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}

function FlagRow({ flag, onChange, busy }) {
    const { key, label, help, type, value, default: def } = flag;
    const isModified = String(value) !== String(def);

    return (
        <SField
            label={
                <span className="inline-flex items-center gap-2">
                    {label}
                    {isModified && (
                        <button
                            type="button"
                            title={`Reset to default (${String(def)})`}
                            className="inline-flex items-center gap-1 rounded px-1 text-[10px] font-medium text-slate-400 hover:text-slate-600"
                            onClick={() => onChange(key, null)}
                            disabled={busy}
                        >
                            <RotateCcw className="size-3" />
                            reset
                        </button>
                    )}
                </span>
            }
            help={help}
        >
            <div className="flex w-full items-center lg:justify-end">
                {type === 'bool' ? (
                    <SToggle
                        checked={Boolean(value)}
                        onChange={(next) => onChange(key, next)}
                        disabled={busy}
                    />
                ) : (
                    <ValueInput type={type} value={value} busy={busy}
                        onCommit={(next) => onChange(key, next)} />
                )}
            </div>
        </SField>
    );
}

function ValueInput({ type, value, onCommit, busy }) {
    const [draft, setDraft] = useState(String(value ?? ''));

    // Keep the local draft in sync when the server value changes underneath us.
    React.useEffect(() => {
        setDraft(String(value ?? ''));
    }, [value]);

    const commit = () => {
        if (String(draft) === String(value ?? '')) return;
        if (type === 'int' || type === 'float') {
            const n = type === 'int' ? parseInt(draft, 10) : parseFloat(draft);
            if (Number.isNaN(n)) {
                setDraft(String(value ?? ''));
                return;
            }
            onCommit(n);
        } else {
            onCommit(draft);
        }
    };

    return (
        <SInput
            className="max-w-[220px]"
            type={type === 'int' || type === 'float' ? 'number' : 'text'}
            step={type === 'float' ? '0.05' : undefined}
            value={draft}
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    e.currentTarget.blur();
                }
            }}
        />
    );
}
