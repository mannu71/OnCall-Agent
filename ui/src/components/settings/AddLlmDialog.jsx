import React from 'react';
import { Layers } from 'lucide-react';
import {
    DlgAlert,
    SButton,
    SDialog,
    SDlgField,
    SInput,
    SSegmented,
    SSelect,
} from './settings-ui';
import {
    BEDROCK_MODEL_OPTIONS,
    LLM_ICONS,
    isReasoningModel,
} from './settingsUtils';

export default function AddLlmDialog({
    open,
    onClose,
    onSave,
    editingLLM,
    formData,
    setFormData,
    hasBedrockCredentials,
    bedrockRegion,
    saving,
}) {
    const embed = Boolean(formData.use_for_embeddings);
    const reasoning = isReasoningModel(formData.model);

    return (
        <SDialog
            open={open}
            onClose={onClose}
            title={editingLLM ? `Edit model: ${editingLLM}` : 'Add LLM'}
            desc="Save a named (provider, model, params) combo. Workflows reference it by name."
            footer={
                <>
                    <SButton variant="outline" size="sm" onClick={onClose}>
                        Cancel
                    </SButton>
                    <SButton
                        variant="primary"
                        size="sm"
                        onClick={onSave}
                        disabled={!formData.model || saving}
                    >
                        {editingLLM ? 'Save changes' : 'Add LLM'}
                    </SButton>
                </>
            }
        >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <SDlgField label="Provider">
                    <SInput mono value="AWS Bedrock" disabled />
                </SDlgField>
                <SDlgField label="Icon">
                    <SSelect
                        value={formData.icon}
                        onChange={(e) => setFormData({ ...formData, icon: e.target.value })}
                        options={LLM_ICONS}
                    />
                </SDlgField>
            </div>

            <SDlgField label="Model" hint="exact Bedrock model id">
                <SInput
                    placeholder="anthropic.claude-3-5-sonnet-20241022-v2:0"
                    mono
                    value={formData.model}
                    onChange={(e) => setFormData({ ...formData, model: e.target.value })}
                    list="bedrock-model-suggestions"
                />
                <datalist id="bedrock-model-suggestions">
                    {BEDROCK_MODEL_OPTIONS.map((model) => (
                        <option key={model} value={model} />
                    ))}
                </datalist>
            </SDlgField>

            {!reasoning && (
                <SDlgField label="Temperature" hint="0 – 1">
                    <SInput
                        mono
                        type="number"
                        min={0}
                        max={1}
                        step={0.1}
                        value={formData.temperature}
                        onChange={(e) => setFormData({
                            ...formData,
                            temperature: Math.max(0, Math.min(1, Number.parseFloat(e.target.value) || 0)),
                        })}
                    />
                </SDlgField>
            )}

            {reasoning && (
                <DlgAlert variant="info">
                    Reasoning models do not support temperature settings.
                </DlgAlert>
            )}

            <SDlgField label="Role">
                <SSegmented
                    value={embed ? 'embed' : 'reason'}
                    onChange={(v) => setFormData({
                        ...formData,
                        use_for_embeddings: v === 'embed',
                    })}
                    options={[
                        { value: 'reason', label: 'Reasoning' },
                        { value: 'embed', label: 'Embeddings', icon: <Layers className="size-3.5" /> },
                    ]}
                />
            </SDlgField>

            {hasBedrockCredentials ? (
                <DlgAlert variant="success">
                    AWS credentials configured
                    {bedrockRegion ? ` · Region: ${bedrockRegion}` : ''}
                </DlgAlert>
            ) : (
                <DlgAlert variant="warning">
                    No AWS credentials found. Configure AWS Bedrock credentials first.
                </DlgAlert>
            )}

            {embed && (
                <DlgAlert variant="warning">
                    <strong>Data leaves the container during indexing.</strong>
                    {' '}
                    Embedding calls flow to AWS Bedrock. Confirm this is acceptable for your compliance posture.
                </DlgAlert>
            )}
        </SDialog>
    );
}
