import React from 'react';
import {
    CARD,
    DlgAlert,
    SButton,
    SDialog,
    TABLE,
    TABLE_CELL,
    TABLE_HEAD,
    TABLE_WRAP,
    cn,
} from './settings-ui';

export default function DiscoverModelsDialog({
    open,
    onClose,
    discoverable,
    discoveredModels,
    selectedModels,
    onToggleSelectAll,
    onToggleSelect,
    onDiscover,
    onAddSelected,
    discovering,
    adding,
    region,
}) {
    const addable = discoveredModels.filter((m) => !m.already_exists);

    return (
        <SDialog
            open={open}
            onClose={onClose}
            title="Discover AWS Bedrock models"
            desc="Query AWS Bedrock to list foundation models available in your configured region."
            maxWidth={720}
            footer={
                <>
                    <SButton variant="outline" size="sm" onClick={onClose}>
                        Cancel
                    </SButton>
                    {discoveredModels.length === 0 ? (
                        <SButton
                            variant="primary"
                            size="sm"
                            onClick={onDiscover}
                            disabled={discovering || !discoverable}
                        >
                            {discovering ? 'Discovering…' : 'Discover models'}
                        </SButton>
                    ) : (
                        <SButton
                            variant="primary"
                            size="sm"
                            onClick={onAddSelected}
                            disabled={selectedModels.length === 0 || adding}
                        >
                            {adding ? 'Adding…' : `Add selected (${selectedModels.length})`}
                        </SButton>
                    )}
                </>
            }
        >
            {discoveredModels.length === 0 ? (
                <DlgAlert variant="info">
                    <strong>AWS Bedrock model discovery</strong>
                    <p className="mt-2 mb-0">
                        Lists active foundation models in
                        {' '}
                        {region || 'us-east-1'}
                        .
                    </p>
                    {discoverable ? (
                        <DlgAlert variant="success" className="mt-3">
                            Using configured credentials (Region: {region || 'us-east-1'})
                        </DlgAlert>
                    ) : (
                        <DlgAlert variant="warning" className="mt-3">
                            No AWS credentials saved. Configure AWS Bedrock credentials first.
                        </DlgAlert>
                    )}
                </DlgAlert>
            ) : (
                <>
                    <p className="m-0 text-slate-500">
                        {discoveredModels.length} models found
                        {region ? ` in ${region}` : ''}
                    </p>
                    <div className={`${CARD} max-h-[50vh] overflow-y-auto`}>
                        <div className={TABLE_WRAP}>
                            <table className={TABLE}>
                            <thead>
                                <tr>
                                    <th className={TABLE_HEAD} style={{ width: 40 }}>
                                        <input
                                            type="checkbox"
                                            checked={addable.length > 0 && selectedModels.length === addable.length}
                                            onChange={onToggleSelectAll}
                                            aria-label="Select all new models"
                                        />
                                    </th>
                                    <th className={TABLE_HEAD}>Model</th>
                                    <th className={TABLE_HEAD}>ID</th>
                                    <th className={TABLE_HEAD}>Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {discoveredModels.map((model) => (
                                    <tr
                                        key={model.name}
                                        className={model.already_exists ? 'opacity-50' : undefined}
                                    >
                                        <td className={TABLE_CELL}>
                                            <input
                                                type="checkbox"
                                                checked={selectedModels.includes(model.name)}
                                                disabled={model.already_exists}
                                                onChange={(e) => onToggleSelect(model.name, e.target.checked)}
                                                aria-label={`Select ${model.name}`}
                                            />
                                        </td>
                                        <td className={cn(TABLE_CELL, 'whitespace-normal')}>
                                            <span className="mr-1">{model.icon}</span>
                                            <span className="font-semibold text-foreground">{model.name}</span>
                                        </td>
                                        <td className={cn(TABLE_CELL, 'max-w-[180px] whitespace-normal')}>
                                            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11.5px] text-slate-600">
                                                {model.model}
                                            </code>
                                        </td>
                                        <td className={TABLE_CELL}>
                                            <span className="text-xs text-slate-500">
                                                {model.already_exists ? 'Exists' : 'New'}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        </div>
                    </div>
                </>
            )}
        </SDialog>
    );
}
