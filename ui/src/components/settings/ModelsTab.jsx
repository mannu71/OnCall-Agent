import React, { memo } from 'react';
import ProviderKeysSection from './ProviderKeysSection';
import LlmSection from './LlmSection';

function ModelsTab({
    bedrockKey,
    onEditProvider,
    llms,
    llmConnectionStatus,
    selectedForDelete,
    onToggleSelectAll,
    onToggleSelect,
    onAddLlm,
    onEditLlm,
    onDeleteLlm,
    onTestLlm,
    onDiscover,
    onBulkDelete,
    onClearSelection,
}) {
    return (
        <>
            <ProviderKeysSection modelKey={bedrockKey} onEdit={onEditProvider} />
            <LlmSection
                llms={llms}
                llmConnectionStatus={llmConnectionStatus}
                selectedForDelete={selectedForDelete}
                onToggleSelectAll={onToggleSelectAll}
                onToggleSelect={onToggleSelect}
                onAdd={onAddLlm}
                onEdit={onEditLlm}
                onDelete={onDeleteLlm}
                onTest={onTestLlm}
                onDiscover={onDiscover}
                onBulkDelete={onBulkDelete}
                onClearSelection={onClearSelection}
            />
        </>
    );
}

export default memo(ModelsTab);
