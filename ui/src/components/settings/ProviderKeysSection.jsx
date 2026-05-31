import React from 'react';
import { CheckCircle, ChevronRight, Key } from 'lucide-react';
import { SBadge, SSection, cn } from './settings-ui';

const BEDROCK_PROVIDER = {
    id: 'bedrock',
    name: 'AWS Bedrock',
    icon: '🌩️',
    scope: 'Anthropic, Llama, Titan embeddings',
};

export default function ProviderKeysSection({ modelKey, onEdit }) {
    const configured = Boolean(
        modelKey
        && (modelKey.has_api_key || modelKey.has_secret_key || modelKey.has_access_credentials),
    );

    const description = configured
        ? (modelKey.region ? `Region: ${modelKey.region}` : 'Credentials configured')
        : 'No credentials set';

    const provider = {
        ...BEDROCK_PROVIDER,
        configured,
        description,
    };

    return (
        <SSection
            title="Provider credentials"
            icon={<Key className="size-4" />}
            desc="Encrypted keys the agent uses to reach hosted LLM providers. Configure these before adding models."
            actions={
                <SBadge variant="muted">
                    {configured ? '1 of 1 configured' : '0 of 1 configured'}
                </SBadge>
            }
        >
            <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,240px),1fr))] gap-3.5">
                <button
                    type="button"
                    className={cn(
                        'relative flex flex-col gap-3 rounded-[14px] border border-border bg-card p-[18px] text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-md',
                        configured && 'before:absolute before:bottom-3.5 before:left-0 before:top-3.5 before:w-[3px] before:rounded-r before:bg-gradient-to-b before:from-emerald-500 before:to-emerald-600',
                    )}
                    onClick={() => onEdit(provider)}
                >
                    <div className="flex items-center gap-2.5">
                        <div className="grid size-[38px] shrink-0 place-items-center rounded-[10px] bg-slate-100 text-[19px] shadow-[inset_0_0_0_1px_rgb(15_23_42/0.03)]">
                            {provider.icon}
                        </div>
                        <div className="min-w-0 flex-1 text-left">
                            <div className="text-sm font-semibold text-foreground">{provider.name}</div>
                            <div
                                className={cn(
                                    'text-xs text-slate-500',
                                    configured && 'font-mono',
                                )}
                            >
                                {provider.description}
                            </div>
                        </div>
                    </div>
                    <div className="text-left text-xs leading-relaxed text-slate-500">{provider.scope}</div>
                    <div className="flex items-center justify-between gap-2">
                        {configured ? (
                            <SBadge variant="success" icon={<CheckCircle className="size-3" />}>
                                Configured
                            </SBadge>
                        ) : (
                            <SBadge variant="outline" dot>
                                Not set
                            </SBadge>
                        )}
                        <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                            {configured ? 'Manage' : 'Connect'}
                            <ChevronRight className="size-3" />
                        </span>
                    </div>
                </button>
            </div>
        </SSection>
    );
}
