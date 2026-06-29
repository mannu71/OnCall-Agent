import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
    addLLM,
    bulkDeleteLLMs,
    deleteLLM,
    updateLLM,
} from '../services/llmService';
import {
    addMCPServer,
    deleteMCPServer,
    getMCPInputValues,
    updateMCPServer,
    updateMCPInputValue,
} from '../services/mcpService';
import { deleteModelKey, upsertModelKey } from '../services/modelKeyService';
import { updateGeneralSettings } from '../services/apiClient';
import agentApiClient from '../services/agentApiClient';
import { useAgentApiHealth } from './useAgentApiHealth';
import { usePersistedState, useSettingsToast } from './usePersistedState';
import { useMcpConfigQuery } from './queries/useConfigQueries';
import { useLlmConfigQuery, useCertificatesQuery } from './queries/useConfigQueries';
import { useModelKeysQuery } from './queries/useModelKeysQuery';
import { useSettingsQuery, useStatusQuery } from './queries/useSettingsQuery';
import { queryKeys } from '../lib/queryKeys';
import {
    discoverBedrockModels,
    importDiscoveredModels,
    setConnectionError,
    setConnectionResult,
    setConnectionTesting,
    testLlmConnection,
    testMcpServer,
} from '../components/settings/settingsApi';
import {
    DEFAULT_TIMEZONE,
    DEFAULT_WORKSPACE,
    EMPTY_BEDROCK_CREDS,
    EMPTY_LLM_FORM,
    EMPTY_MCP_FORM,
    LS_CONFIRM_DESTRUCTIVE,
    LS_TIMEZONE,
    LS_WORKSPACE,
    TIMEZONE_LABELS,
    buildGauges,
    buildSections,
} from '../components/settings/settingsConstants';
import {
    extractInputVariables,
    findBedrockKey,
    isReasoningModel,
    keyIsConfigured,
    parseArgsString,
    parseEnvVars,
} from '../components/settings/settingsUtils';

export function useSettingsPage() {
    const [active, setActive] = useState('general');
    const { toast, showToast } = useSettingsToast();

    const [workspaceName, setWorkspaceName] = usePersistedState(LS_WORKSPACE, DEFAULT_WORKSPACE);
    const [timezone, setTimezone] = usePersistedState(LS_TIMEZONE, DEFAULT_TIMEZONE);
    const [confirmDestructive, setConfirmDestructive] = usePersistedState(LS_CONFIRM_DESTRUCTIVE, true);
    const [agentTimeout, setAgentTimeout] = useState('180');

    const queryClient = useQueryClient();
    const { data: settingsData } = useSettingsQuery();
    const { data: statusData } = useStatusQuery();
    const { data: mcpData } = useMcpConfigQuery();
    const { data: llmData } = useLlmConfigQuery();
    const { data: certData } = useCertificatesQuery();
    const { data: keysData } = useModelKeysQuery();

    const systemStatus = statusData ?? null;

    // Sync timezone from settings query (single source — no duplicate getAppSettings)
    useEffect(() => {
        if (settingsData?.global_timezone) {
            setTimezone(settingsData.global_timezone);
        }
    }, [settingsData?.global_timezone, setTimezone]);

    useEffect(() => {
        if (settingsData?.agent_timeout_seconds != null) {
            setAgentTimeout(String(settingsData.agent_timeout_seconds));
        }
    }, [settingsData?.agent_timeout_seconds]);

    // Persist timezone changes to the backend (and keep the localStorage cache).
    const handleTimezoneChange = useCallback((tz) => {
        setTimezone(tz);
        updateGeneralSettings({ global_timezone: tz })
            .then(() => showToast('Timezone updated'))
            .catch((err) => showToast(err?.message || 'Failed to update timezone', 'error'));
    }, [setTimezone, showToast]);

    const { apiHealth, loading: healthLoading, recheckWithSpinner } = useAgentApiHealth({
        initialShowSpinner: true,
    });

    const [servers, setServers] = useState({});
    const [connectionStatus, setConnectionStatus] = useState({});
    const [llms, setLlms] = useState({});
    const [llmConnectionStatus, setLLMConnectionStatus] = useState({});
    const [selectedForDelete, setSelectedForDelete] = useState([]);
    const [certificates, setCertificates] = useState([]);
    const [certUploadLoading, setCertUploadLoading] = useState(false);
    const [modelKeys, setModelKeys] = useState([]);

    useEffect(() => {
        if (mcpData) setServers(mcpData);
    }, [mcpData]);

    useEffect(() => {
        if (llmData) setLlms(llmData);
    }, [llmData]);

    useEffect(() => {
        if (certData) setCertificates(certData);
    }, [certData]);

    useEffect(() => {
        if (keysData) setModelKeys(keysData);
    }, [keysData]);

    const [dialog, setDialog] = useState(null);
    const [editingServer, setEditingServer] = useState(null);
    const [mcpFormData, setMcpFormData] = useState(EMPTY_MCP_FORM);
    const [detectedInputVars, setDetectedInputVars] = useState([]);
    const [inputVarValues, setInputVarValues] = useState({});
    const [editingLLM, setEditingLLM] = useState(null);
    const [llmFormData, setLLMFormData] = useState(EMPTY_LLM_FORM);
    const [bedrockCredentials, setBedrockCredentials] = useState(EMPTY_BEDROCK_CREDS);
    const [showModelKeyFields, setShowModelKeyFields] = useState({});
    const [discoveredModels, setDiscoveredModels] = useState([]);
    const [selectedModels, setSelectedModels] = useState([]);
    const [awsDiscovering, setAwsDiscovering] = useState(false);
    const [awsAdding, setAwsAdding] = useState(false);

    const originalMaskedValues = useRef({});

    const bedrockKey = useMemo(() => findBedrockKey(modelKeys), [modelKeys]);
    const hasBedrockCredentials = keyIsConfigured(bedrockKey);
    const bedrockRegion = bedrockKey?.region || bedrockCredentials.region;

    const serverCount = Object.keys(servers).length;
    const llmCount = Object.keys(llms).length;
    const connectedLlms = useMemo(
        () => Object.values(llmConnectionStatus).filter((s) => s?.status === 'connected').length,
        [llmConnectionStatus],
    );

    const sections = useMemo(
        () => buildSections({ serverCount, llmCount, certCount: certificates.length }),
        [serverCount, llmCount, certificates.length],
    );

    const activeSection = useMemo(
        () => sections.find((s) => s.id === active) ?? sections[0],
        [sections, active],
    );

    const gauges = useMemo(
        () => buildGauges({ apiHealth, systemStatus, llmCount, connectedLlms }),
        [apiHealth, systemStatus, llmCount, connectedLlms],
    );

    const timezoneLabel = TIMEZONE_LABELS[timezone] || timezone.replace('/', ' / ');

    const closeDialog = useCallback(() => {
        setDialog(null);
        setEditingServer(null);
        setEditingLLM(null);
    }, []);

    useEffect(() => {
        const existing = findBedrockKey(modelKeys);
        if (existing) {
            const initialForm = {
                access_key_id: existing.access_key_id || '',
                secret_access_key: existing.secret_access_key || '',
                session_token: existing.session_token || '',
                region: existing.region || 'us-east-1',
                description: existing.description || '',
            };
            setBedrockCredentials(initialForm);
            originalMaskedValues.current = { ...initialForm };
        } else {
            setBedrockCredentials(EMPTY_BEDROCK_CREDS);
            originalMaskedValues.current = {};
        }
    }, [modelKeys]);

    const reloadServers = useCallback(async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.mcp });
    }, [queryClient]);

    const reloadLlms = useCallback(async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.llm });
    }, [queryClient]);

    const reloadModelKeys = useCallback(async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.modelKeys });
    }, [queryClient]);

    const reloadCertificates = useCallback(async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.certificates });
    }, [queryClient]);

    const testServerConnection = useCallback(async (serverName, serverConfig) => {
        setConnectionTesting(setConnectionStatus, serverName);
        try {
            const result = await testMcpServer(serverName, serverConfig);
            setConnectionResult(setConnectionStatus, serverName, result, 'Connection failed');
        } catch (error) {
            setConnectionError(setConnectionStatus, serverName, error);
        }
    }, [showToast]);

    const testAllConnections = useCallback(async () => {
        showToast(`Testing ${serverCount} servers…`);
        await Promise.all(
            Object.entries(servers).map(([name, config]) => testServerConnection(name, config)),
        );
    }, [servers, serverCount, showToast, testServerConnection]);

    const testLLMConnectionHandler = useCallback(async (llmName) => {
        setConnectionTesting(setLLMConnectionStatus, llmName);
        try {
            const result = await testLlmConnection(llmName);
            setConnectionResult(setLLMConnectionStatus, llmName, result, 'Connection failed');
        } catch (error) {
            setConnectionError(setLLMConnectionStatus, llmName, error);
        }
    }, [showToast]);

    // LLM connection tests are manual only (testAllConnections / per-model test buttons)

    const openMcpDialog = useCallback(async (serverName = null) => {
        const currentInputValues = await getMCPInputValues();

        if (serverName) {
            const server = servers[serverName];
            const argsString = Array.isArray(server.args) ? server.args.join('\n') : '';
            setEditingServer(serverName);
            setMcpFormData({
                name: serverName,
                command: server.command || '',
                args: argsString,
                type: server.type || 'stdio',
                icon: server.icon || '🔧',
                description: server.description || '',
                env: server.env ? JSON.stringify(server.env, null, 2) : '',
            });
            setDetectedInputVars(extractInputVariables(argsString));
        } else {
            setEditingServer(null);
            setMcpFormData(EMPTY_MCP_FORM);
            setDetectedInputVars([]);
        }

        setInputVarValues(currentInputValues);
        setDialog('mcp');
    }, [servers]);

    const saveMcp = useCallback(async () => {
        try {
            await Promise.all(
                detectedInputVars
                    .filter((varName) => inputVarValues[varName])
                    .map((varName) => updateMCPInputValue(varName, inputVarValues[varName])),
            );

            let env = {};
            if (mcpFormData.env.trim()) {
                try {
                    env = parseEnvVars(mcpFormData.env);
                } catch {
                    alert('Invalid JSON in environment variables');
                    return;
                }
            }

            const serverConfig = {
                command: mcpFormData.command,
                args: parseArgsString(mcpFormData.args),
                type: mcpFormData.type,
                icon: mcpFormData.icon,
                description: mcpFormData.description,
                env,
            };

            if (editingServer) {
                const newName = editingServer === mcpFormData.name ? null : mcpFormData.name;
                await updateMCPServer(editingServer, serverConfig, newName);
                showToast(
                    editingServer === mcpFormData.name
                        ? `Updated server: ${mcpFormData.name}`
                        : `Renamed server: ${editingServer} → ${mcpFormData.name}`,
                );
            } else {
                await addMCPServer(mcpFormData.name, serverConfig);
                showToast(`Added server: ${mcpFormData.name}`);
            }

            await reloadServers();
            closeDialog();
            testServerConnection(mcpFormData.name, serverConfig);
        } catch (error) {
            console.error('Error saving server:', error);
            alert('Failed to save server configuration');
        }
    }, [
        closeDialog,
        detectedInputVars,
        editingServer,
        inputVarValues,
        mcpFormData,
        reloadServers,
        showToast,
        testServerConnection,
    ]);

    const deleteServer = useCallback(async (serverName) => {
        if (!globalThis.confirm(`Delete "${serverName}"?`)) return;
        try {
            await deleteMCPServer(serverName);
            showToast(`Deleted server: ${serverName}`);
            await reloadServers();
        } catch (error) {
            console.error('Error deleting server:', error);
            alert('Failed to delete server');
        }
    }, [reloadServers, showToast]);

    const openLlmDialog = useCallback((llmName = null) => {
        if (llmName) {
            const llm = llms[llmName];
            setEditingLLM(llmName);
            setLLMFormData({
                name: llmName,
                provider: llm.provider || 'AWS Bedrock',
                model: llm.model || '',
                icon: llm.icon || '🧠',
                temperature: llm.temperature ?? 0,
                use_for_embeddings: Boolean(llm.use_for_embeddings ?? llm.useForEmbeddings ?? false),
            });
        } else {
            setEditingLLM(null);
            setLLMFormData(EMPTY_LLM_FORM);
        }
        setDialog('llm');
    }, [llms]);

    const saveLlm = useCallback(async () => {
        try {
            const llmConfig = {
                provider: 'AWS Bedrock',
                model: llmFormData.model,
                icon: llmFormData.icon,
                ...(!isReasoningModel(llmFormData.model) && { temperature: llmFormData.temperature }),
                use_for_embeddings: Boolean(llmFormData.use_for_embeddings),
            };

            if (editingLLM) {
                await updateLLM(editingLLM, llmConfig);
                showToast(`Updated LLM: ${editingLLM}`);
            } else {
                await addLLM(llmFormData.model, llmConfig);
                showToast(`Added LLM: ${llmFormData.model}`);
            }

            await reloadLlms();
            closeDialog();
        } catch (error) {
            console.error('Error saving LLM:', error);
            alert('Failed to save LLM configuration');
        }
    }, [closeDialog, editingLLM, llmFormData, reloadLlms, showToast]);

    const deleteLlm = useCallback(async (llmName) => {
        if (!globalThis.confirm(`Delete "${llmName}"?`)) return;
        try {
            await deleteLLM(llmName);
            showToast(`Deleted LLM: ${llmName}`);
            await reloadLlms();
        } catch (error) {
            console.error('Error deleting LLM:', error);
            alert('Failed to delete LLM');
        }
    }, [reloadLlms, showToast]);

    const bulkDeleteLlms = useCallback(async () => {
        if (selectedForDelete.length === 0) return;
        if (!globalThis.confirm(`Delete ${selectedForDelete.length} models?`)) return;
        try {
            await bulkDeleteLLMs(selectedForDelete);
            showToast(`Deleted ${selectedForDelete.length} models`);
            setSelectedForDelete([]);
            await reloadLlms();
        } catch (err) {
            alert(`Failed to delete models: ${err.message}`);
        }
    }, [reloadLlms, selectedForDelete, showToast]);

    const toggleSelectAllLlms = useCallback(() => {
        const names = Object.keys(llms);
        setSelectedForDelete((prev) => (prev.length === names.length ? [] : names));
    }, [llms]);

    const toggleSelectLlm = useCallback((name, checked) => {
        setSelectedForDelete((prev) =>
            checked ? [...prev, name] : prev.filter((n) => n !== name),
        );
    }, []);

    const saveBedrockCredentials = useCallback(async () => {
        try {
            const data = { provider: 'AWS Bedrock' };
            ['access_key_id', 'secret_access_key', 'session_token'].forEach((field) => {
                const value = bedrockCredentials[field];
                if (value && value !== originalMaskedValues.current[field]) {
                    data[field] = value;
                }
            });
            if (bedrockCredentials.region) data.region = bedrockCredentials.region;
            if (bedrockCredentials.description) data.description = bedrockCredentials.description;

            await upsertModelKey(data);
            showToast('AWS Bedrock credentials saved');
            await reloadModelKeys();
            closeDialog();
        } catch (error) {
            console.error('Error saving AWS Bedrock credentials:', error);
            alert(`Failed to save credentials: ${error.message}`);
        }
    }, [bedrockCredentials, closeDialog, reloadModelKeys, showToast]);

    const deleteBedrockCredentials = useCallback(async () => {
        if (!globalThis.confirm('Delete AWS Bedrock credentials?')) return;
        try {
            await deleteModelKey('AWS Bedrock');
            showToast('AWS Bedrock credentials removed');
            closeDialog();
            await reloadModelKeys();
        } catch (error) {
            console.error('Error deleting AWS Bedrock credentials:', error);
            alert(`Failed to delete: ${error.message}`);
        }
    }, [closeDialog, reloadModelKeys, showToast]);

    const uploadCertificate = useCallback(async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;

        setCertUploadLoading(true);
        try {
            const result = await agentApiClient.uploadCertificate(file);
            showToast(result.message || 'Certificate uploaded');
            await reloadCertificates();
        } catch (error) {
            showToast(`Upload failed: ${error.message}`);
        } finally {
            setCertUploadLoading(false);
            event.target.value = '';
        }
    }, [reloadCertificates, showToast]);

    const deleteCertificate = useCallback(async (filename) => {
        if (!globalThis.confirm(`Delete certificate "${filename}"?`)) return;
        try {
            await agentApiClient.deleteCertificate(filename);
            showToast(`Deleted certificate: ${filename}`);
            await reloadCertificates();
        } catch (error) {
            showToast(`Delete failed: ${error.message}`);
        }
    }, [reloadCertificates, showToast]);

    const openDiscoverDialog = useCallback(() => {
        setDiscoveredModels([]);
        setSelectedModels([]);
        setDialog('discover');
    }, []);

    const closeDiscoverDialog = useCallback(() => {
        setDialog((d) => (d === 'discover' ? null : d));
        setDiscoveredModels([]);
        setSelectedModels([]);
    }, []);

    const discoverModelsHandler = useCallback(async () => {
        setAwsDiscovering(true);
        try {
            const result = await discoverBedrockModels();
            const models = result.models || [];
            setDiscoveredModels(models);
            setSelectedModels(models.filter((m) => !m.already_exists).map((m) => m.name));
        } catch (err) {
            alert(`Failed to discover models: ${err.message}`);
        } finally {
            setAwsDiscovering(false);
        }
    }, []);

    const addDiscoveredHandler = useCallback(async () => {
        if (selectedModels.length === 0) return;
        setAwsAdding(true);
        try {
            const modelsToAdd = discoveredModels.filter((m) => selectedModels.includes(m.name));
            const region = discoveredModels[0]?.region || bedrockRegion;
            const result = await importDiscoveredModels(modelsToAdd, region);
            showToast(`Added ${result.added_count} models, skipped ${result.skipped_count} existing`);
            closeDiscoverDialog();
            await reloadLlms();
        } catch (err) {
            alert(`Failed to add models: ${err.message}`);
        } finally {
            setAwsAdding(false);
        }
    }, [
        bedrockRegion,
        closeDiscoverDialog,
        discoveredModels,
        reloadLlms,
        selectedModels,
        showToast,
    ]);

    const toggleSelectAllDiscovered = useCallback(() => {
        const addable = discoveredModels.filter((m) => !m.already_exists).map((m) => m.name);
        setSelectedModels((prev) => (prev.length === addable.length ? [] : addable));
    }, [discoveredModels]);

    const toggleSelectDiscovered = useCallback((name, checked) => {
        setSelectedModels((prev) =>
            checked ? [...prev, name] : prev.filter((n) => n !== name),
        );
    }, []);

    const healthCheck = useCallback(async () => {
        await recheckWithSpinner();
        showToast('Health check complete');
    }, [recheckWithSpinner, showToast]);

    return {
        active,
        setActive,
        toast,
        sections,
        activeSection,
        gauges,
        timezoneLabel,
        general: {
            workspaceName,
            setWorkspaceName,
            timezone,
            setTimezone: handleTimezoneChange,
            agentTimeout,
            setAgentTimeout,
            confirmDestructive,
            setConfirmDestructive,
        },
        hero: {
            apiHealth,
            healthLoading,
            healthCheck,
            workspaceName,
            timezoneLabel,
            gauges,
        },
        mcp: {
            servers,
            connectionStatus,
            openMcpDialog,
            deleteServer,
            testServerConnection,
            testAllConnections,
            dialog: dialog === 'mcp',
            closeDialog,
            saveMcp,
            editingServer,
            mcpFormData,
            setMcpFormData,
            detectedInputVars,
            setDetectedInputVars,
            inputVarValues,
            setInputVarValues,
        },
        models: {
            bedrockKey,
            hasBedrockCredentials,
            bedrockRegion,
            llms,
            llmConnectionStatus,
            selectedForDelete,
            toggleSelectAllLlms,
            toggleSelectLlm,
            openLlmDialog,
            deleteLlm,
            testLLMConnection: testLLMConnectionHandler,
            openDiscoverDialog,
            bulkDeleteLlms,
            clearSelection: () => setSelectedForDelete([]),
            openBedrockDialog: () => setDialog('bedrock'),
            llmDialog: dialog === 'llm',
            bedrockDialog: dialog === 'bedrock',
            discoverDialog: dialog === 'discover',
            closeDialog,
            saveLlm,
            editingLLM,
            llmFormData,
            setLLMFormData,
            bedrockCredentials,
            setBedrockCredentials,
            showModelKeyFields,
            setShowModelKeyFields,
            saveBedrockCredentials,
            deleteBedrockCredentials,
            discoveredModels,
            selectedModels,
            discoverModels: discoverModelsHandler,
            addDiscovered: addDiscoveredHandler,
            toggleSelectAllDiscovered,
            toggleSelectDiscovered,
            closeDiscoverDialog,
            awsDiscovering,
            awsAdding,
        },
        certs: {
            certificates,
            certUploadLoading,
            uploadCertificate,
            deleteCertificate,
        },
    };
}
