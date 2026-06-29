import React from 'react';
import { Globe, Zap } from 'lucide-react';
import { TIMEZONE_OPTIONS } from './settingsConstants';
import { CARD, SField, SInput, SSection, SSelect, SToggle } from './settings-ui';

export default function GeneralSection({
    workspaceName,
    onWorkspaceNameChange,
    timezone,
    onTimezoneChange,
    agentTimeout,
    onAgentTimeoutChange,
    confirmDestructive,
    onConfirmDestructiveChange,
}) {
    return (
        <div>
            <SSection
                title="Workspace"
                icon={<Globe className="size-4" />}
                desc="Identity and locale of this OnCall Agent install."
            >
                <div className={CARD}>
                    <SField
                        label="Workspace name"
                        help="Shown in alerts, audit logs, and the sidebar header."
                    >
                        <SInput
                            value={workspaceName}
                            onChange={(e) => onWorkspaceNameChange(e.target.value)}
                        />
                    </SField>
                    <SField
                        label="Timezone"
                        help="Used for the scheduler, incident timestamps, and digest emails."
                    >
                        <SSelect
                            value={timezone}
                            onChange={(e) => onTimezoneChange(e.target.value)}
                            options={TIMEZONE_OPTIONS}
                        />
                    </SField>
                </div>
            </SSection>

            <SSection
                title="Agent behavior"
                icon={<Zap className="size-4" />}
                desc="Defaults applied to every workflow run."
            >
                <div className={CARD}>
                    <SField
                        label="Confirm destructive actions"
                        help="Ask for human approval before running tools that mutate production data."
                    >
                        <div className="flex items-center gap-3">
                            <SToggle
                                checked={confirmDestructive}
                                onChange={onConfirmDestructiveChange}
                            />
                            <span className="text-[13.5px] text-slate-500">Recommended</span>
                        </div>
                    </SField>
                    <SField
                        label="Default execution timeout"
                        help="Maximum wall-clock time for a single agent run before it's aborted."
                    >
                        <SInput
                            mono
                            type="number"
                            min={30}
                            max={3600}
                            value={agentTimeout}
                            onChange={(e) => onAgentTimeoutChange(e.target.value)}
                            className="w-full sm:max-w-[220px]"
                            suffix={
                                <span className="flex items-center border-l border-border bg-slate-50 px-2.5 font-mono text-xs text-slate-500">
                                    seconds
                                </span>
                            }
                        />
                    </SField>
                </div>
            </SSection>
        </div>
    );
}
