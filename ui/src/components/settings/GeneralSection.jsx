import React from 'react';
import { Globe } from 'lucide-react';
import { TIMEZONE_OPTIONS } from './settingsConstants';
import { CARD, SField, SInput, SSection, SSelect } from './settings-ui';

export default function GeneralSection({
    workspaceName,
    onWorkspaceNameChange,
    timezone,
    onTimezoneChange,
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
        </div>
    );
}
