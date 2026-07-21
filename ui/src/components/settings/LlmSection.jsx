import React from 'react';
import { Cpu, Edit2, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import {
    CARD,
    EMPTY_STATE,
    SBadge,
    SButton,
    SSection,
    SStatusBadge,
    TABLE_CELL,
    TABLE_HEAD,
    TABLE_WIDE,
    TABLE_WRAP,
    cn,
} from './settings-ui';
import { providerBadgeVariant } from './settingsUtils';

export default function LlmSection({
    llms,
    llmConnectionStatus,
    selectedForDelete,
    onToggleSelectAll,
    onToggleSelect,
    onAdd,
    onEdit,
    onDelete,
    onTest,
    onDiscover,
    onBulkDelete,
    onClearSelection,
}) {
    const llmEntries = Object.entries(llms);
    const allSelected = selectedForDelete.length > 0
        && selectedForDelete.length === llmEntries.length;

    return (
        <SSection
            title="Model configurations"
            icon={<Cpu className="size-4" />}
            desc="Named (provider, model, params) combos your workflows reference by name."
            actions={
                <>
                    <SButton
                        variant="outline"
                        size="sm"
                        icon={<Sparkles className="size-3.5" />}
                        onClick={onDiscover}
                    >
                        Discover
                    </SButton>
                    <SButton
                        variant="primary"
                        size="sm"
                        icon={<Plus className="size-3.5" />}
                        onClick={() => onAdd()}
                    >
                        Add model
                    </SButton>
                </>
            }
        >
            {selectedForDelete.length > 0 && (
                <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-slate-50 px-3.5 py-2.5 text-[13px] text-slate-600 sm:gap-3">
                    <span>{selectedForDelete.length} selected</span>
                    <SButton variant="primary" size="sm" onClick={onBulkDelete}>
                        Delete selected
                    </SButton>
                    <SButton variant="ghost" size="sm" onClick={onClearSelection}>
                        Clear
                    </SButton>
                </div>
            )}

            <div className={CARD}>
                {llmEntries.length === 0 ? (
                    <div className={EMPTY_STATE}>
                        <div className="mb-1 text-sm font-semibold text-slate-700">No models configured</div>
                        Add a Bedrock model or use Discover to import from your AWS account.
                    </div>
                ) : (
                    <div className={TABLE_WRAP}>
                        <table className={TABLE_WIDE}>
                            <thead>
                                <tr>
                                    <th className={TABLE_HEAD} style={{ width: 40 }}>
                                        <input
                                            type="checkbox"
                                            checked={allSelected}
                                            onChange={onToggleSelectAll}
                                            aria-label="Select all models"
                                        />
                                    </th>
                                    <th className={TABLE_HEAD} style={{ width: 56 }} />
                                    <th className={TABLE_HEAD}>Name</th>
                                    <th className={TABLE_HEAD} style={{ width: 140 }}>Provider</th>
                                    <th className={TABLE_HEAD} style={{ width: 100 }}>Temp</th>
                                    <th className={TABLE_HEAD} style={{ width: 130 }}>Status</th>
                                    <th className={TABLE_HEAD} style={{ width: 110 }} />
                                </tr>
                            </thead>
                            <tbody>
                                {llmEntries.map(([name, config]) => {
                                    const status = llmConnectionStatus[name];
                                    const temp = config.temperature ?? 0;

                                    return (
                                        <tr key={name} className="transition-colors hover:bg-slate-50">
                                            <td className={TABLE_CELL}>
                                                <input
                                                    type="checkbox"
                                                    checked={selectedForDelete.includes(name)}
                                                    onChange={(e) => onToggleSelect(name, e.target.checked)}
                                                    aria-label={`Select ${name}`}
                                                />
                                            </td>
                                            <td className={TABLE_CELL}>
                                                <span className="inline-flex size-[38px] items-center justify-center rounded-[10px] bg-slate-100 text-[17px] shadow-[inset_0_0_0_1px_rgb(15_23_42/0.03)]">
                                                    {config.icon || '🧠'}
                                                </span>
                                            </td>
                                            <td className={cn(TABLE_CELL, 'whitespace-normal')}>
                                                <div className="font-semibold text-foreground">{name}</div>
                                                <div className="mt-0.5 break-all font-mono text-xs text-slate-500 sm:break-normal">
                                                    {config.model}
                                                </div>
                                            </td>
                                            <td className={TABLE_CELL}>
                                                <SBadge variant={providerBadgeVariant(config.provider)}>
                                                    {config.provider || 'AWS Bedrock'}
                                                </SBadge>
                                            </td>
                                            <td className={TABLE_CELL}>
                                                <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11.5px] text-slate-600">
                                                    {Number(temp).toFixed(1)}
                                                </code>
                                            </td>
                                            <td className={TABLE_CELL}>
                                                <SStatusBadge status={status?.status} message={status?.message} />
                                            </td>
                                            <td className={TABLE_CELL}>
                                                <div className="flex justify-end gap-0.5">
                                                    <SButton
                                                        variant="ghost"
                                                        size="icon"
                                                        title="Test"
                                                        icon={<RefreshCw className="size-4" />}
                                                        onClick={() => onTest(name, config)}
                                                        disabled={status?.status === 'testing'}
                                                    />
                                                    <SButton
                                                        variant="ghost"
                                                        size="icon"
                                                        title="Edit"
                                                        icon={<Edit2 className="size-4" />}
                                                        onClick={() => onEdit(name)}
                                                    />
                                                    <SButton
                                                        variant="ghost"
                                                        size="icon"
                                                        title="Delete"
                                                        icon={<Trash2 className="size-4" />}
                                                        onClick={() => onDelete(name)}
                                                    />
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </SSection>
    );
}
