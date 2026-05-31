import React, { useRef } from 'react';
import { Edit2, RefreshCw, Trash2 } from 'lucide-react';
import { useContainerQuery } from '@/hooks/useContainerQuery';
import {
    CARD_CONTAINER,
    EMPTY_STATE,
    LIST_CARD,
    TABLE_BREAKPOINT_MD,
    SButton,
    SStatusBadge,
    TABLE,
    TABLE_CELL,
    TABLE_HEAD,
    TABLE_WRAP,
    cn,
} from './settings-ui';
import { formatCommandPreview } from './settingsUtils';

function McpServerActions({ name, config, status, onTest, onEdit, onDelete }) {
    return (
        <div className="flex shrink-0 gap-0.5">
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
    );
}

function McpServerCard({ name, config, status, onTest, onEdit, onDelete }) {
    return (
        <div className={LIST_CARD}>
            <div className="flex min-w-0 items-start gap-3">
                <span className="inline-flex size-[38px] shrink-0 items-center justify-center rounded-[10px] bg-slate-100 text-[17px] shadow-[inset_0_0_0_1px_rgb(15_23_42/0.03)]">
                    {config.icon || '🔧'}
                </span>
                <div className="min-w-0 flex-1">
                    <div className="font-semibold text-foreground">{name}</div>
                    {config.description && (
                        <div className="mt-0.5 text-xs text-slate-500">{config.description}</div>
                    )}
                    <code className="mt-2 inline-block max-w-full break-all rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11.5px] text-slate-600">
                        {formatCommandPreview(config.command, config.args)}
                    </code>
                    <div className="mt-2.5 flex min-w-0 flex-wrap items-center justify-between gap-2">
                        <SStatusBadge status={status?.status} message={status?.message} />
                        <McpServerActions
                            name={name}
                            config={config}
                            status={status}
                            onTest={onTest}
                            onEdit={onEdit}
                            onDelete={onDelete}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}

function McpServerTableRow({ name, config, status, onTest, onEdit, onDelete }) {
    return (
        <tr className="transition-colors hover:bg-slate-50">
            <td className={TABLE_CELL}>
                <span className="inline-flex size-[38px] items-center justify-center rounded-[10px] bg-slate-100 text-[17px] shadow-[inset_0_0_0_1px_rgb(15_23_42/0.03)]">
                    {config.icon || '🔧'}
                </span>
            </td>
            <td className={cn(TABLE_CELL, 'whitespace-normal')}>
                <div className="font-semibold text-foreground">{name}</div>
                {config.description && (
                    <div className="mt-0.5 text-xs text-slate-500">{config.description}</div>
                )}
            </td>
            <td className={cn(TABLE_CELL, 'whitespace-normal')}>
                <code className="break-all rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11.5px] text-slate-600">
                    {formatCommandPreview(config.command, config.args)}
                </code>
            </td>
            <td className={cn(TABLE_CELL, 'whitespace-normal')}>
                <SStatusBadge status={status?.status} message={status?.message} />
            </td>
            <td className={TABLE_CELL}>
                <div className="flex justify-end">
                    <McpServerActions
                        name={name}
                        config={config}
                        status={status}
                        onTest={onTest}
                        onEdit={onEdit}
                        onDelete={onDelete}
                    />
                </div>
            </td>
        </tr>
    );
}

export default function McpSection({
    servers,
    connectionStatus,
    onEdit,
    onDelete,
    onTest,
}) {
    const serverEntries = Object.entries(servers);
    const containerRef = useRef(null);
    const showTable = useContainerQuery(containerRef, TABLE_BREAKPOINT_MD);

    return (
        <div ref={containerRef} className={CARD_CONTAINER}>
            {serverEntries.length === 0 ? (
                <div className={EMPTY_STATE}>
                    <div className="mb-1 text-sm font-semibold text-slate-700">No MCP servers configured</div>
                    Click &quot;Add server&quot; to connect your first data source.
                </div>
            ) : showTable ? (
                <div className={TABLE_WRAP}>
                    <table className={TABLE}>
                        <thead>
                            <tr>
                                <th className={TABLE_HEAD} style={{ width: 56 }} />
                                <th className={TABLE_HEAD}>Server</th>
                                <th className={TABLE_HEAD}>Command</th>
                                <th className={TABLE_HEAD}>Status</th>
                                <th className={TABLE_HEAD} style={{ width: 110 }} />
                            </tr>
                        </thead>
                        <tbody>
                            {serverEntries.map(([name, config]) => (
                                <McpServerTableRow
                                    key={name}
                                    name={name}
                                    config={config}
                                    status={connectionStatus[name]}
                                    onTest={onTest}
                                    onEdit={onEdit}
                                    onDelete={onDelete}
                                />
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                serverEntries.map(([name, config]) => (
                    <McpServerCard
                        key={name}
                        name={name}
                        config={config}
                        status={connectionStatus[name]}
                        onTest={onTest}
                        onEdit={onEdit}
                        onDelete={onDelete}
                    />
                ))
            )}
        </div>
    );
}
