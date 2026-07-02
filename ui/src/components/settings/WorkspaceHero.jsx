import React from 'react';
import { CARD, cn } from './settings-ui';

const GAUGE_RING_PX = 44;
const GAUGE_INNER_INSET_PX = 5;

function GaugeRing({ value, color, children }) {
    const pct = Math.max(0, Math.min(100, Number(value) || 0));
    const sweep = pct * 3.6;
    const track = 'rgb(235 238 245)';

    return (
        <div
            className="relative grid shrink-0 place-items-center rounded-full"
            style={{
                width: GAUGE_RING_PX,
                height: GAUGE_RING_PX,
                background: pct <= 0
                    ? track
                    : `conic-gradient(from -90deg, ${color} 0deg, ${color} ${sweep}deg, ${track} ${sweep}deg, ${track} 360deg)`,
            }}
        >
            <span
                className="absolute rounded-full bg-card"
                style={{ inset: GAUGE_INNER_INSET_PX }}
            />
            <span className="relative min-w-[1.1rem] text-center text-xs font-bold leading-none tabular-nums tracking-tight text-foreground">
                {children}
            </span>
        </div>
    );
}

function GaugeCard({ label, value, meta, ringValue, ringColor, ringLabel }) {
    return (
        <div className={cn(CARD, 'flex min-w-0 items-center gap-3 p-4 sm:p-5')}>
            <GaugeRing value={ringValue} color={ringColor}>
                {ringLabel}
            </GaugeRing>
            <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
                <div className="text-[10.5px] font-bold uppercase leading-none tracking-[0.07em] text-slate-400">
                    {label}
                </div>
                <div className="text-[15px] font-semibold leading-tight tracking-tight text-foreground">
                    {value}
                </div>
                <div className="truncate font-mono text-[11px] leading-snug text-slate-500">
                    {meta}
                </div>
            </div>
        </div>
    );
}

/** Live platform status gauges — sits under SettingsPageHead on the General tab. */
export default function WorkspaceHero({ gauges }) {
    return (
        <section
            aria-label="Platform status"
            className="mb-8 grid min-w-0 grid-cols-1 gap-3 @sm:grid-cols-2"
        >
            {gauges.map((g) => (
                <GaugeCard
                    key={g.label}
                    label={g.label}
                    value={g.value}
                    meta={g.meta}
                    ringValue={g.v}
                    ringColor={g.c}
                    ringLabel={g.num}
                />
            ))}
        </section>
    );
}
