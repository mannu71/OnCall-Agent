import React from 'react';
import {
    DlgAlert,
    SButton,
    SDialog,
    SDlgField,
    SInput,
    SSegmented,
    SSelect,
    STextarea,
} from './settings-ui';
import { MCP_SERVER_ICONS, extractInputVariables } from './settingsUtils';

export default function AddMcpDialog({
    open,
    onClose,
    onSave,
    editingServer,
    formData,
    setFormData,
    detectedInputVars,
    setDetectedInputVars,
    inputVarValues,
    setInputVarValues,
    saving,
}) {
    const [type, setType] = React.useState(formData.type || 'stdio');

    React.useEffect(() => {
        if (open) {
            setType(formData.type || 'stdio');
        }
    }, [open, formData.type]);

    const handleArgsChange = (value) => {
        setFormData({ ...formData, args: value });
        const vars = extractInputVariables(value);
        setDetectedInputVars(vars);
        if (vars.length > 0) {
            const next = { ...inputVarValues };
            vars.forEach((v) => {
                if (!(v in next)) next[v] = '';
            });
            setInputVarValues(next);
        }
    };

    return (
        <SDialog
            open={open}
            onClose={onClose}
            title={editingServer ? `Edit MCP server: ${editingServer}` : 'Add MCP server'}
            desc="Configure a Model Context Protocol server. The agent connects on first use."
            footer={
                <>
                    <SButton variant="outline" size="sm" onClick={onClose}>
                        Cancel
                    </SButton>
                    <SButton
                        variant="primary"
                        size="sm"
                        onClick={onSave}
                        disabled={!formData.name || !formData.command || saving}
                    >
                        {editingServer ? 'Save changes' : 'Add server'}
                    </SButton>
                </>
            }
        >
            <SDlgField label="Name" hint="kebab-case">
                <SInput
                    placeholder="my-database"
                    mono
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    disabled={Boolean(editingServer)}
                />
            </SDlgField>

            <SDlgField label="Connection type">
                <SSegmented
                    value={type}
                    onChange={(v) => {
                        setType(v);
                        setFormData({ ...formData, type: v });
                    }}
                    options={[
                        { value: 'stdio', label: 'stdio' },
                        { value: 'sse', label: 'SSE' },
                        { value: 'http', label: 'HTTP' },
                    ]}
                />
            </SDlgField>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_2fr]">
                <SDlgField label="Command">
                    <SInput
                        placeholder="npx"
                        mono
                        value={formData.command}
                        onChange={(e) => setFormData({ ...formData, command: e.target.value })}
                    />
                </SDlgField>
                <SDlgField label="Icon">
                    <SSelect
                        value={formData.icon}
                        onChange={(e) => setFormData({ ...formData, icon: e.target.value })}
                        options={MCP_SERVER_ICONS}
                    />
                </SDlgField>
            </div>

            <SDlgField label="Arguments" hint="one per line">
                <STextarea
                    mono
                    placeholder={'-y\n@modelcontextprotocol/server-postgres\n${input:DATABASE_URL}'}
                    value={formData.args}
                    onChange={(e) => handleArgsChange(e.target.value)}
                />
            </SDlgField>

            {detectedInputVars.length > 0 && (
                <DlgAlert variant="info">
                    <strong>Input variables</strong>
                    {detectedInputVars.map((varName) => (
                        <div key={varName} className="mt-2.5">
                            <SDlgField label={varName}>
                                <SInput
                                    mono
                                    value={inputVarValues[varName] || ''}
                                    onChange={(e) => setInputVarValues({
                                        ...inputVarValues,
                                        [varName]: e.target.value,
                                    })}
                                    placeholder={`Value for \${input:${varName}}`}
                                />
                            </SDlgField>
                        </div>
                    ))}
                </DlgAlert>
            )}

            <SDlgField label="Description" hint="optional">
                <SInput
                    placeholder="One-line summary shown in the table"
                    value={formData.description}
                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                />
            </SDlgField>

            <SDlgField label="Environment variables" hint="JSON · optional">
                <STextarea
                    mono
                    placeholder='{"AWS_PROFILE": "default"}'
                    value={formData.env}
                    onChange={(e) => setFormData({ ...formData, env: e.target.value })}
                    rows={3}
                />
            </SDlgField>
        </SDialog>
    );
}
