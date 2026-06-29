import React, { memo } from 'react';
import { Settings as SettingsIcon } from 'lucide-react';
import { cn } from './settings-ui';

function SettingsRail({ sections, active, onChange }) {
    return (
        <aside className="w-full shrink-0 border-b border-border bg-card xl:min-h-svh xl:border-b-0 xl:border-r">
            <div className="px-4 py-6 sm:px-6 xl:px-[26px] xl:py-8">
                <div className="mb-4 flex items-center gap-2 xl:mb-[22px]">
                    <SettingsIcon className="size-4 shrink-0 text-red-600" />
                    <p className="m-0 text-base font-bold tracking-tight text-foreground">Settings</p>
                </div>

                <nav
                    className="-mx-1 flex gap-1 overflow-x-auto overscroll-x-contain pb-1 [-ms-overflow-style:none] [scrollbar-width:none] xl:mx-0 xl:flex-col xl:overflow-visible xl:pb-0 [&::-webkit-scrollbar]:hidden"
                    aria-label="Settings sections"
                >
                    {sections.map((s) => {
                        const Ico = s.icon;
                        const isActive = active === s.id;
                        return (
                            <button
                                key={s.id}
                                type="button"
                                className={cn(
                                    'flex min-w-[168px] shrink-0 items-center gap-3 rounded-[10px] border-0 px-2.5 py-2 text-left transition-colors xl:min-w-0 xl:w-full',
                                    isActive
                                        ? 'bg-slate-100 text-foreground'
                                        : 'text-slate-600 hover:bg-slate-50 hover:text-foreground',
                                )}
                                onClick={() => onChange(s.id)}
                            >
                                <span
                                    className={cn(
                                        'grid size-[30px] shrink-0 place-items-center rounded-lg transition-all [&_svg]:size-4',
                                        isActive
                                            ? 'bg-red-600 text-white shadow-[0_4px_10px_-3px_rgb(220_38_38/0.5)]'
                                            : 'bg-slate-100 text-slate-500',
                                    )}
                                >
                                    <Ico />
                                </span>
                                <span className="flex min-w-0 flex-1 flex-col">
                                    <span
                                        className={cn(
                                            'text-[13.5px] leading-tight text-foreground',
                                            isActive && 'font-semibold',
                                        )}
                                    >
                                        {s.title}
                                    </span>
                                    <span className="hidden truncate text-[11px] text-slate-400 sm:block">
                                        {s.sub}
                                    </span>
                                </span>
                                {s.count != null && (
                                    <span
                                        className={cn(
                                            'shrink-0 rounded-full px-2 py-px text-[11px] font-semibold tabular-nums',
                                            isActive ? 'bg-red-50 text-red-600' : 'bg-slate-100 text-slate-400',
                                        )}
                                    >
                                        {s.count}
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </nav>
            </div>
        </aside>
    );
}

export default memo(SettingsRail);
