import React, { lazy, Suspense, useMemo } from 'react';
import { Plus, RefreshCw, Upload } from 'lucide-react';
import SettingsRail from '../components/settings/SettingsRail';
import WorkspaceHero from '../components/settings/WorkspaceHero';
import SettingsPageHead from '../components/settings/SettingsPageHead';
import { SButton, SToast } from '../components/settings/settings-ui';
import { useSettingsPage } from '../hooks/useSettingsPage';

const GeneralSection = lazy(() => import('../components/settings/GeneralSection'));
const McpSection = lazy(() => import('../components/settings/McpSection'));
const ModelsTab = lazy(() => import('../components/settings/ModelsTab'));
const CertificatesSection = lazy(() => import('../components/settings/CertificatesSection'));
const AddMcpDialog = lazy(() => import('../components/settings/AddMcpDialog'));
const AddLlmDialog = lazy(() => import('../components/settings/AddLlmDialog'));
const BedrockCredentialsDialog = lazy(() => import('../components/settings/BedrockCredentialsDialog'));
const DiscoverModelsDialog = lazy(() => import('../components/settings/DiscoverModelsDialog'));

function SectionFallback() {
    return <div className="py-12 text-center text-sm text-slate-400">Loading…</div>;
}

const Settings = () => {
    const {
        active,
        setActive,
        toast,
        sections,
        activeSection,
        general,
        hero,
        mcp,
        models,
        certs,
    } = useSettingsPage();

    const pageHeadActions = useMemo(() => {
        if (active === 'mcp') {
            const serverCount = Object.keys(mcp.servers).length;
            return (
                <>
                    <SButton
                        variant="outline"
                        size="sm"
                        icon={<RefreshCw className="size-3.5" />}
                        onClick={mcp.testAllConnections}
                        disabled={serverCount === 0}
                    >
                        Test all
                    </SButton>
                    <SButton
                        variant="primary"
                        size="sm"
                        icon={<Plus className="size-3.5" />}
                        onClick={() => mcp.openMcpDialog()}
                    >
                        Add server
                    </SButton>
                </>
            );
        }

        if (active === 'certs') {
            return (
                <SButton
                    variant="outline"
                    size="sm"
                    icon={<Upload className="size-3.5" />}
                    disabled={certs.certUploadLoading}
                    onClick={() => document.getElementById('cert-upload')?.click()}
                >
                    {certs.certUploadLoading ? 'Uploading…' : 'Upload certificate'}
                </SButton>
            );
        }

        return null;
    }, [active, mcp, certs.certUploadLoading]);

    return (
        <div className="min-h-0 w-full min-w-0 overflow-x-hidden bg-slate-50 font-sans text-sm text-slate-800 lg:min-h-svh">
            <div className="flex w-full min-w-0 flex-col xl:grid xl:min-h-svh xl:grid-cols-[256px_minmax(0,1fr)]">
                <SettingsRail sections={sections} active={active} onChange={setActive} />
                <main className="min-w-0 px-4 py-6 pb-8 sm:px-6 md:px-8 lg:px-10 lg:py-8 xl:px-12">
                    {active === 'general' ? (
                        <WorkspaceHero
                            workspaceName={hero.workspaceName}
                            timezone={hero.timezoneLabel}
                            apiHealth={hero.apiHealth}
                            gauges={hero.gauges}
                            onHealthCheck={hero.healthCheck}
                            healthLoading={hero.healthLoading}
                        />
                    ) : (
                        <SettingsPageHead section={activeSection} actions={pageHeadActions} />
                    )}

                    <Suspense fallback={<SectionFallback />}>
                        {active === 'general' && (
                            <GeneralSection
                                workspaceName={general.workspaceName}
                                onWorkspaceNameChange={general.setWorkspaceName}
                                timezone={general.timezone}
                                onTimezoneChange={general.setTimezone}
                                agentTimeout={general.agentTimeout}
                                onAgentTimeoutChange={general.setAgentTimeout}
                                confirmDestructive={general.confirmDestructive}
                                onConfirmDestructiveChange={general.setConfirmDestructive}
                            />
                        )}
                        {active === 'mcp' && (
                            <McpSection
                                servers={mcp.servers}
                                connectionStatus={mcp.connectionStatus}
                                onEdit={mcp.openMcpDialog}
                                onDelete={mcp.deleteServer}
                                onTest={mcp.testServerConnection}
                            />
                        )}
                        {active === 'models' && (
                            <ModelsTab
                                bedrockKey={models.bedrockKey}
                                onEditProvider={models.openBedrockDialog}
                                llms={models.llms}
                                llmConnectionStatus={models.llmConnectionStatus}
                                selectedForDelete={models.selectedForDelete}
                                onToggleSelectAll={models.toggleSelectAllLlms}
                                onToggleSelect={models.toggleSelectLlm}
                                onAddLlm={models.openLlmDialog}
                                onEditLlm={models.openLlmDialog}
                                onDeleteLlm={models.deleteLlm}
                                onTestLlm={models.testLLMConnection}
                                onDiscover={models.openDiscoverDialog}
                                onBulkDelete={models.bulkDeleteLlms}
                                onClearSelection={models.clearSelection}
                            />
                        )}
                        {active === 'certs' && (
                            <CertificatesSection
                                certificates={certs.certificates}
                                onUpload={certs.uploadCertificate}
                                onDelete={certs.deleteCertificate}
                            />
                        )}
                    </Suspense>
                </main>
            </div>

            <Suspense fallback={null}>
                {mcp.dialog && (
                    <AddMcpDialog
                        open
                        onClose={mcp.closeDialog}
                        onSave={mcp.saveMcp}
                        editingServer={mcp.editingServer}
                        formData={mcp.mcpFormData}
                        setFormData={mcp.setMcpFormData}
                        detectedInputVars={mcp.detectedInputVars}
                        setDetectedInputVars={mcp.setDetectedInputVars}
                        inputVarValues={mcp.inputVarValues}
                        setInputVarValues={mcp.setInputVarValues}
                    />
                )}

                {models.llmDialog && (
                    <AddLlmDialog
                        open
                        onClose={models.closeDialog}
                        onSave={models.saveLlm}
                        editingLLM={models.editingLLM}
                        formData={models.llmFormData}
                        setFormData={models.setLLMFormData}
                        hasBedrockCredentials={models.hasBedrockCredentials}
                        bedrockRegion={models.bedrockRegion}
                    />
                )}

                {models.bedrockDialog && (
                    <BedrockCredentialsDialog
                        open
                        onClose={models.closeDialog}
                        onSave={models.saveBedrockCredentials}
                        onDelete={models.deleteBedrockCredentials}
                        configured={models.hasBedrockCredentials}
                        credentials={models.bedrockCredentials}
                        setCredentials={models.setBedrockCredentials}
                        showFields={models.showModelKeyFields}
                        setShowFields={models.setShowModelKeyFields}
                    />
                )}

                {models.discoverDialog && (
                    <DiscoverModelsDialog
                        open
                        onClose={models.closeDiscoverDialog}
                        discoverable={models.hasBedrockCredentials}
                        discoveredModels={models.discoveredModels}
                        selectedModels={models.selectedModels}
                        onToggleSelectAll={models.toggleSelectAllDiscovered}
                        onToggleSelect={models.toggleSelectDiscovered}
                        onDiscover={models.discoverModels}
                        onAddSelected={models.addDiscovered}
                        discovering={models.awsDiscovering}
                        adding={models.awsAdding}
                        region={models.bedrockRegion}
                    />
                )}
            </Suspense>

            {toast && <SToast message={toast} />}
        </div>
    );
};

export default Settings;
