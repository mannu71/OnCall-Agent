import React from 'react';
import { CheckCircle, Eye, EyeOff, Lock, RefreshCw, Trash2 } from 'lucide-react';
import {
    SBadge,
    SButton,
    SDialog,
    SDlgField,
    SInput,
    SSelect,
} from './settings-ui';

const REGION_OPTIONS = [
    { value: 'us-east-1', label: 'us-east-1' },
    { value: 'us-west-2', label: 'us-west-2' },
    { value: 'eu-west-1', label: 'eu-west-1' },
    { value: 'eu-central-1', label: 'eu-central-1' },
    { value: 'ap-southeast-1', label: 'ap-southeast-1' },
];

export default function BedrockCredentialsDialog({
    open,
    onClose,
    onSave,
    onDelete,
    configured,
    credentials,
    setCredentials,
    showFields,
    setShowFields,
    saving,
}) {
    const toggle = (field) => setShowFields((prev) => ({ ...prev, [field]: !prev[field] }));

    return (
        <SDialog
            open={open}
            onClose={onClose}
            title={null}
            desc={null}
            maxWidth={520}
            footer={
                <>
                    {configured && (
                        <SButton
                            variant="ghost"
                            size="sm"
                            className="w-full text-red-600 hover:text-red-700 sm:mr-auto sm:w-auto"
                            icon={<Trash2 className="size-3.5" />}
                            onClick={onDelete}
                        >
                            Remove credentials
                        </SButton>
                    )}
                    <SButton variant="outline" size="sm" className="w-full sm:w-auto" onClick={onClose}>
                        Cancel
                    </SButton>
                    <SButton variant="primary" size="sm" className="w-full sm:w-auto" onClick={onSave} disabled={saving}>
                        {configured ? 'Save changes' : 'Connect provider'}
                    </SButton>
                </>
            }
        >
            <div className="mb-1 flex items-center gap-3.5">
                <div className="grid size-11 shrink-0 place-items-center rounded-[10px] bg-slate-100 text-[22px]">
                    🌩️
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2 text-base font-semibold text-foreground">
                        AWS Bedrock
                        {configured ? (
                            <SBadge variant="success" icon={<CheckCircle className="size-3" />}>
                                Configured
                            </SBadge>
                        ) : (
                            <SBadge variant="outline" dot>
                                Not set
                            </SBadge>
                        )}
                    </div>
                    <div className="mt-0.5 text-[13px] text-slate-500">
                        Anthropic, Llama, Titan embeddings
                    </div>
                </div>
            </div>

            <div className="flex gap-2.5 rounded-lg border border-border bg-slate-50 px-3 py-2.5 text-xs leading-relaxed text-slate-600">
                <Lock className="mt-0.5 size-3.5 shrink-0 text-slate-500" />
                <span>
                    Stored encrypted at rest. The agent never logs full credentials — only masked values are echoed in the UI.
                </span>
            </div>

            <SDlgField label="Access key ID">
                <SInput
                    mono
                    placeholder="AKIAIOSFODNN7EXAMPLE"
                    value={credentials.access_key_id}
                    onChange={(e) => setCredentials({ ...credentials, access_key_id: e.target.value })}
                    type={showFields.access_key_id ? 'text' : 'password'}
                    suffix={
                        <button
                            type="button"
                            className="flex items-center border-l border-border bg-background px-2.5 text-slate-500 hover:bg-slate-50 hover:text-foreground"
                            onClick={() => toggle('access_key_id')}
                            title={showFields.access_key_id ? 'Hide' : 'Show'}
                        >
                            {showFields.access_key_id ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                        </button>
                    }
                />
            </SDlgField>

            <SDlgField label="Secret access key" hint={configured ? 'leave unchanged to keep existing' : ''}>
                <SInput
                    mono
                    placeholder="••••••••••••••••••••"
                    value={credentials.secret_access_key}
                    onChange={(e) => setCredentials({ ...credentials, secret_access_key: e.target.value })}
                    type={showFields.secret_access_key ? 'text' : 'password'}
                    suffix={
                        <button
                            type="button"
                            className="flex items-center border-l border-border bg-background px-2.5 text-slate-500 hover:bg-slate-50 hover:text-foreground"
                            onClick={() => toggle('secret_access_key')}
                            title={showFields.secret_access_key ? 'Hide' : 'Show'}
                        >
                            {showFields.secret_access_key ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                        </button>
                    }
                />
            </SDlgField>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <SDlgField label="Region">
                    <SSelect
                        value={credentials.region}
                        onChange={(e) => setCredentials({ ...credentials, region: e.target.value })}
                        options={REGION_OPTIONS}
                    />
                </SDlgField>
                <SDlgField label="Session token" hint="optional">
                    <SInput
                        mono
                        type="password"
                        placeholder="—"
                        value={credentials.session_token}
                        onChange={(e) => setCredentials({ ...credentials, session_token: e.target.value })}
                    />
                </SDlgField>
            </div>

            <SDlgField label="Label" hint="optional · internal note">
                <SInput
                    placeholder="e.g. Production keys — read-only"
                    value={credentials.description}
                    onChange={(e) => setCredentials({ ...credentials, description: e.target.value })}
                />
            </SDlgField>

            <div className="flex flex-col gap-3 rounded-lg border border-dashed border-border px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                <p className="m-0 text-xs text-slate-500">Send a tiny request to validate credentials after saving.</p>
                <SButton variant="outline" size="xs" className="w-full shrink-0 sm:w-auto" icon={<RefreshCw className="size-3" />} disabled>
                    Test connection
                </SButton>
            </div>
        </SDialog>
    );
}
