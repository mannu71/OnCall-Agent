import React from 'react';

export default function SettingsPageHead({ section, actions }) {
    const Ico = section.icon;
    return (
        <div className="mb-6 flex min-w-0 flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-start md:justify-between">
            <div className="min-w-0 flex-1">
                <div className="mb-1.5 text-[11px] font-bold uppercase tracking-widest text-slate-400">
                    Settings
                </div>
                <div className="mb-1.5 flex flex-wrap items-center gap-2.5 sm:gap-3">
                    <span className="grid size-[38px] shrink-0 place-items-center rounded-[10px] bg-red-600 text-white shadow-[0_6px_14px_-5px_rgb(220_38_38/0.5)] [&_svg]:size-[19px]">
                        <Ico />
                    </span>
                    <h1 className="m-0 min-w-0 text-xl font-bold tracking-tight text-foreground sm:text-[26px]">
                        {section.title}
                    </h1>
                </div>
                <p className="m-0 max-w-xl text-[13px] text-slate-500 sm:text-[13.5px]">{section.desc}</p>
            </div>
            <div className="flex w-full shrink-0 flex-col items-stretch gap-3 md:w-auto md:items-end">
                {actions && (
                    <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap md:justify-end [&>button]:w-full [&>button]:sm:w-auto">
                        {actions}
                    </div>
                )}
                <div className="text-xs text-slate-500">
                    <span>Saved automatically</span>
                </div>
            </div>
        </div>
    );
}
