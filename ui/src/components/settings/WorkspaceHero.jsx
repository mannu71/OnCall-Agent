import React from 'react';
import { Globe, RefreshCw } from 'lucide-react';
import { SButton, cn } from './settings-ui';

const GAUGE_RING_PX = 46;
const GAUGE_INNER_INSET_PX = 5;

function PulseDot({ live }) {
    return (
        <span className="relative inline-flex size-2 shrink-0">
            <span
                className={cn(
                    'absolute inset-0 animate-ping rounded-full opacity-75',
                    live ? 'bg-emerald-400' : 'bg-red-400',
                )}
            />
            <span
                className={cn(
                    'relative size-2 rounded-full',
                    live ? 'bg-emerald-500 shadow-[0_0_0_3px_rgb(16_185_129/0.18)]' : 'bg-red-600 shadow-[0_0_0_3px_rgb(220_38_38/0.18)]',
                )}
            />
        </span>
    );
}

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

function GaugeCell({ label, value, meta, ringValue, ringColor, ringLabel }) {
    return (
        <div className="flex h-full min-w-0 items-center gap-[13px] bg-[radial-gradient(120%_140%_at_0%_0%,#fff_0%,rgb(248_250_252)_100%)] px-4 py-4 sm:px-5 sm:py-[17px] @4xl:px-6">
            <div
                className="flex shrink-0 items-center justify-center"
                style={{ width: GAUGE_RING_PX, height: GAUGE_RING_PX }}
            >
                <GaugeRing value={ringValue} color={ringColor}>
                    {ringLabel}
                </GaugeRing>
            </div>
            <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
                <div className="text-[10.5px] font-bold uppercase leading-none tracking-[0.07em] text-slate-400">
                    {label}
                </div>
                <div className="text-[15px] font-semibold leading-tight tracking-tight text-foreground">
                    {value}
                </div>
                <div className="font-mono text-[11px] leading-snug text-slate-500">
                    {meta}
                </div>
            </div>
        </div>
    );
}

export default function WorkspaceHero({
    workspaceName,
    timezone,
    apiHealth,
    gauges,
    onHealthCheck,
    healthLoading,
}) {
    const isChecking = apiHealth == null;
    const isLive = apiHealth?.status === 'healthy';

    return (
        <div className="@container relative mb-6 w-full min-w-0 overflow-hidden rounded-[18px] border border-border bg-[radial-gradient(120%_140%_at_0%_0%,#fff_0%,rgb(248_250_252)_100%)] shadow-sm">
            <div
                className="pointer-events-none absolute inset-0 opacity-50"
                style={{
                    backgroundImage: 'radial-gradient(rgb(226 232 240) 1px, transparent 1px)',
                    backgroundSize: '22px 22px',
                    maskImage: 'radial-gradient(80% 120% at 100% 0%, #000, transparent 60%)',
                }}
            />
            <div className="relative flex flex-col gap-4 p-4 sm:gap-6 sm:p-6 @4xl:flex-row @4xl:items-start @4xl:justify-between">
                <div className="flex min-w-0 items-start gap-3 sm:items-center sm:gap-3.5">
                    <div className="grid size-11 shrink-0 place-items-center rounded-[13px] bg-gradient-to-b from-red-400 via-red-600 to-red-700 text-lg font-extrabold tracking-tight text-white shadow-[0_8px_20px_-6px_rgb(220_38_38/0.5)] sm:size-[50px] sm:text-[22px]">
                        O
                    </div>
                    <div className="min-w-0 flex-1">
                        <div className="mb-1 text-[11px] font-bold uppercase tracking-widest text-slate-400">
                            Workspace
                        </div>
                        <h1 className="mb-1.5 break-words text-xl font-bold leading-tight tracking-tight text-foreground sm:text-2xl @4xl:text-[26px] @4xl:leading-none">
                            {workspaceName}
                        </h1>
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-card px-2 py-0.5 text-xs text-slate-600">
                                <Globe className="size-3 shrink-0 text-slate-400" />
                                <span className="truncate">{timezone}</span>
                            </span>
                            <span className="inline-flex items-center rounded-full border border-border bg-card px-2 py-0.5 font-mono text-[11px] text-slate-600">
                                env: {import.meta.env.MODE}
                            </span>
                            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2 py-0.5 text-xs text-slate-600">
                                {!isChecking && <PulseDot live={isLive} />}
                                {isChecking ? 'Checking…' : isLive ? 'Live' : 'Degraded'}
                            </span>
                        </div>
                    </div>
                </div>
                <div className="flex w-full shrink-0 @sm:w-auto @4xl:justify-end">
                    <SButton
                        variant="outline"
                        size="sm"
                        className="w-full @sm:w-auto"
                        icon={<RefreshCw className={cn('size-3.5', healthLoading && 'animate-spin')} />}
                        onClick={onHealthCheck}
                        disabled={healthLoading}
                    >
                        Run health check
                    </SButton>
                </div>
            </div>
            <div className="relative grid min-w-0 auto-rows-fr grid-cols-1 gap-px border-t border-border bg-border @sm:grid-cols-2 @4xl:grid-cols-4">
                {gauges.map((g) => (
                    <GaugeCell
                        key={g.label}
                        label={g.label}
                        value={g.value}
                        meta={g.meta}
                        ringValue={g.v}
                        ringColor={g.c}
                        ringLabel={g.num}
                    />
                ))}
            </div>
        </div>
    );
}
