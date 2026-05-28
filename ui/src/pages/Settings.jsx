import React, { useState, useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { 
    Dialog, 
    DialogContent, 
    DialogHeader, 
    DialogTitle, 
    DialogFooter 
} from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Alert } from '@/components/ui/alert';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Plus,
    Edit2,
    Trash2,
    RefreshCw,
    CheckCircle,
    XCircle,
    Clock,
    Loader2,
    Eye,
    EyeOff,
    Key,
    Info,
    Shield,
    Cloud
} from 'lucide-react';
import { getMCPServers, addMCPServer, updateMCPServer, deleteMCPServer, getMCPInputValues, updateMCPInputValue, invalidateCache } from '../services/mcpService';
import { getLLMs, addLLM, updateLLM, deleteLLM, discoverModels, addDiscoveredModels, bulkDeleteLLMs } from '../services/llmService';
import { getModelKeys, upsertModelKey, deleteModelKey } from '../services/modelKeyService';
import agentApiClient from '../services/agentApiClient';
import { testLLMConnection as testLLMConnectionAPI } from '../services/apiClient';

// Icon options for MCP Servers
const MCP_SERVER_ICONS = [
    { value: '🔧', label: '🔧 Tool' },
    { value: '🎭', label: '🎭 Playwright' },
    { value: '🗄️', label: '🗄️ Database' },
    { value: '📊', label: '📊 Analytics' },
    { value: '🐘', label: '🐘 PostgreSQL' },
    { value: '🔍', label: '🔍 Search' },
    { value: '📁', label: '📁 FileSystem' },
    { value: '☁️', label: '☁️ Cloud' },
    { value: '🌐', label: '🌐 Web' },
    { value: '📡', label: '📡 API' },
    { value: '⚡', label: '⚡ Fast' },
    { value: '🔐', label: '🔐 Security' },
    { value: '📝', label: '📝 Notes' },
    { value: '🤖', label: '🤖 Bot' },
    { value: '💾', label: '💾 Storage' },
];

// Icon options for LLMs
const LLM_ICONS = [
    { value: '🧠', label: '🧠 Brain' },
    { value: '⚡', label: '⚡ Lightning' },
    { value: '🤖', label: '🤖 Robot' },
    { value: '✨', label: '✨ Sparkles' },
    { value: '🚀', label: '🚀 Rocket' },
    { value: '💡', label: '💡 Lightbulb' },
    { value: '🎯', label: '🎯 Target' },
    { value: '🔮', label: '🔮 Crystal Ball' },
    { value: '🌟', label: '🌟 Star' },
    { value: '💫', label: '💫 Dizzy' },
    { value: '🎓', label: '🎓 Graduation' },
    { value: '📚', label: '📚 Books' },
    { value: '🔬', label: '🔬 Microscope' },
    { value: '🎨', label: '🎨 Art' },
    { value: '🌈', label: '🌈 Rainbow' },
];

// Helper to extract ${input:...} variables from args string
const extractInputVariables = (argsString) => {
    const matches = argsString.match(/\$\{input:([^}]+)\}/g) || [];
    return matches.map(m => m.match(/\$\{input:([^}]+)\}/)[1]);
};

// Parse args string into array, handling comma-separated npx args
const parseArgsString = (argsString) => {
    let args = argsString
        .split('\n')
        .map(arg => arg.trim())
        .filter(arg => arg.length > 0);

    if (args.length !== 1 || !args[0].includes(', ')) {
        return args;
    }

    const singleArg = args[0];
    if (!singleArg.startsWith('-y, ') && !singleArg.startsWith('-y,')) {
        return args;
    }

    const parts = [];
    let remaining = singleArg;
    const firstComma = remaining.indexOf(',');

    if (firstComma === -1) {
        return args;
    }

    parts.push(remaining.substring(0, firstComma).trim());
    remaining = remaining.substring(firstComma + 1).trim();

    const urlStart = remaining.indexOf('://');
    const secondComma = remaining.indexOf(', ');

    if (secondComma !== -1 && (urlStart === -1 || secondComma < urlStart)) {
        parts.push(remaining.substring(0, secondComma).trim());
        remaining = remaining.substring(secondComma + 1).trim();
    }

    if (remaining) {
        parts.push(remaining);
    }

    return parts.length >= 2 ? parts : args;
};

// Parse environment variables JSON string
const parseEnvVars = (envString) => {
    if (!envString.trim()) {
        return {};
    }
    return JSON.parse(envString);
};

const Settings = () => {
    const [servers, setServers] = useState({});
    const [openDialog, setOpenDialog] = useState(false);
    const [editingServer, setEditingServer] = useState(null);
    const [formData, setFormData] = useState({
        name: '',
        command: '',
        args: '',
        type: 'stdio',
        icon: '🔧',
        description: '',
        env: ''
    });
    const [detectedInputVars, setDetectedInputVars] = useState([]);
    const [inputVarValues, setInputVarValues] = useState({});
    const [saveMessage, setSaveMessage] = useState('');

    // Connection status state for MCP servers
    const [connectionStatus, setConnectionStatus] = useState({}); // { serverName: { status: 'untested' | 'testing' | 'connected' | 'error', message: '' } }

    // LLM state
    const [llms, setLlms] = useState({});
    const [openLLMDialog, setOpenLLMDialog] = useState(false);
    const [editingLLM, setEditingLLM] = useState(null);
    const [llmFormData, setLLMFormData] = useState({
        name: '',
        provider: 'OpenAI',
        model: '',
        icon: '🧠',
        description: '',
        endpoint: '',
        baseUrl: '',
        temperature: 0,
        access_key_id: '',
        secret_access_key: '',
        session_token: '',
        // Flags this LLM config as the active embedding model. Server-side
        // ``llm_config_repository.save`` enforces single-row exclusivity
        // (saving with true unsets the flag on any other row).
        use_for_embeddings: false,
    });

    const [openAWSDialog, setOpenAWSDialog] = useState(false);
    const [discoverProvider, setDiscoverProvider] = useState('');
    const [awsFormData, setAWSFormData] = useState({
        region: 'us-east-1',
        access_key_id: '',
        secret_access_key: '',
        session_token: '',
    });
    const [awsDiscovering, setAwsDiscovering] = useState(false);
    const [discoveredModels, setDiscoveredModels] = useState([]);
    const [selectedModels, setSelectedModels] = useState([]);
    const [awsAdding, setAwsAdding] = useState(false);
    const [selectedForDelete, setSelectedForDelete] = useState([]);

    // OpenAI reasoning models that don't support temperature parameter
    const REASONING_MODELS = ['o1', 'o1-mini', 'o1-preview', 'o3', 'o3-mini', 'o4-mini'];
    const isReasoningModel = (model) => {
        if (!model) return false;
        const modelLower = model.toLowerCase();
        return REASONING_MODELS.some(rm => modelLower.startsWith(rm));
    };
    const [llmConnectionStatus, setLLMConnectionStatus] = useState({}); // { llmName: { status: 'untested' | 'testing' | 'connected' | 'error', message: '' } }

    // Certificates state
    const [certificates, setCertificates] = useState([]);
    const [certUploadLoading, setCertUploadLoading] = useState(false);

    // Model Keys state
    const [modelKeys, setModelKeys] = useState([]);
    const [bedrockCredentials, setBedrockCredentials] = useState({
        access_key_id: '',
        secret_access_key: '',
        session_token: '',
        region: 'us-east-1',
        description: '',
    });
    const [showModelKeyFields, setShowModelKeyFields] = useState({});
    const originalMaskedValues = useRef({});

    const MODEL_KEY_PROVIDERS = [
        { value: 'AWS Bedrock', label: 'AWS Bedrock', icon: '🌩️', fields: ['access_key_id', 'secret_access_key', 'session_token', 'region'] },
    ];

    // Normalise provider names: DB may store "bedrock" while UI uses "AWS Bedrock"
    const normalizeProvider = (p) => (p || '').toLowerCase().replace(/\s+/g, '');
    const findModelKey = (uiProvider) => {
        const norm = normalizeProvider(uiProvider);
        return modelKeys.find(k =>
            normalizeProvider(k.provider) === norm ||
            // "awsbedrock" matches "bedrock"
            (norm.includes('bedrock') && normalizeProvider(k.provider).includes('bedrock')) ||
            (normalizeProvider(k.provider).includes('bedrock') && norm.includes('bedrock'))
        );
    };
    // True when a model key record has at least one credential set
    const keyIsConfigured = (mk) =>
        mk && (mk.has_api_key || mk.has_secret_key || mk.has_access_credentials || mk.endpoint);

    // Sync loaded bedrock keys to form state
    useEffect(() => {
        const existing = modelKeys.find(k => k.provider === 'AWS Bedrock' || normalizeProvider(k.provider).includes('bedrock'));
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
            setBedrockCredentials({
                access_key_id: '',
                secret_access_key: '',
                session_token: '',
                region: 'us-east-1',
                description: '',
            });
            originalMaskedValues.current = {};
        }
    }, [modelKeys]);

    const discoverableProviders = (() => {
        const providers = [];
        const supportedForDiscovery = ['AWS Bedrock'];
        for (const mk of modelKeys) {
            const matchedProvider = supportedForDiscovery.find(p => normalizeProvider(p) === normalizeProvider(mk.provider) || (p === 'AWS Bedrock' && normalizeProvider(mk.provider).includes('bedrock')));
            if (!matchedProvider) continue;
            const hasCreds = mk.has_api_key || mk.has_secret_key || mk.has_access_credentials || mk.endpoint;
            if (!hasCreds) continue;
            providers.push({
                provider: 'AWS Bedrock',
                icon: '🌩️',
                label: 'AWS Bedrock',
                region: mk.region,
                endpoint: mk.endpoint,
                source: 'model-keys',
            });
        }
        return providers;
    })();

    // Load MCP servers and LLMs on mount
    useEffect(() => {
        loadServers();
        loadLLMConfigs();
        loadCertificates();
        loadModelKeys();
    }, []);

    const loadServers = async () => {
        invalidateCache(); // Ensure fresh data
        const mcpServers = await getMCPServers();
        setServers(mcpServers);
    };

    // Test MCP server connection
    const testServerConnection = async (serverName, serverConfig) => {
        if (!globalThis.electronAPI?.testMCPServer) {
            setSaveMessage('Connection test is only available in the desktop app');
            setTimeout(() => setSaveMessage(''), 3000);
            return;
        }

        setConnectionStatus(prev => ({
            ...prev,
            [serverName]: { status: 'testing', message: 'Testing connection...' }
        }));

        try {
            const result = await globalThis.electronAPI.testMCPServer(serverName, serverConfig);

            if (result.success) {
                setConnectionStatus(prev => ({
                    ...prev,
                    [serverName]: { status: 'connected', message: result.message || 'Connected' }
                }));
            } else {
                setConnectionStatus(prev => ({
                    ...prev,
                    [serverName]: { status: 'error', message: result.error || 'Connection failed' }
                }));
            }
        } catch (error) {
            setConnectionStatus(prev => ({
                ...prev,
                [serverName]: { status: 'error', message: error.message || 'Connection test failed' }
            }));
        }
    };

    // Test all servers
    const testAllConnections = async () => {
        for (const [name, config] of Object.entries(servers)) {
            await testServerConnection(name, config);
        }
    };

    const loadLLMConfigs = async () => {
        const llmConfigs = await getLLMs();
        setLlms(llmConfigs);
    };

    const loadModelKeys = async () => {
        try {
            const keys = await getModelKeys();
            setModelKeys(keys);
        } catch (error) {
            console.error('Failed to load model keys:', error);
            setModelKeys([]);
        }
    };

    const handleSaveBedrockCredentials = async () => {
        try {
            const data = { provider: 'AWS Bedrock' };
            const fields = ['access_key_id', 'secret_access_key', 'session_token'];
            fields.forEach(field => {
                const value = bedrockCredentials[field];
                if (value && value !== originalMaskedValues.current[field]) {
                    data[field] = value;
                }
            });
            if (bedrockCredentials.region) data.region = bedrockCredentials.region;
            if (bedrockCredentials.description) data.description = bedrockCredentials.description;

            await upsertModelKey(data);
            setSaveMessage('Saved AWS Bedrock credentials');
            await loadModelKeys();
        } catch (error) {
            console.error('Error saving AWS Bedrock credentials:', error);
            alert('Failed to save credentials: ' + error.message);
        }
        setTimeout(() => setSaveMessage(''), 3000);
    };

    const handleDeleteBedrockCredentials = async () => {
        if (globalThis.confirm('Delete AWS Bedrock credentials?')) {
            try {
                await deleteModelKey('AWS Bedrock');
                setSaveMessage('Deleted AWS Bedrock credentials');
                setBedrockCredentials({
                    access_key_id: '',
                    secret_access_key: '',
                    session_token: '',
                    region: 'us-east-1',
                    description: '',
                });
                originalMaskedValues.current = {};
                await loadModelKeys();
            } catch (error) {
                console.error('Error deleting AWS Bedrock credentials:', error);
                alert('Failed to delete: ' + error.message);
            }
            setTimeout(() => setSaveMessage(''), 3000);
        }
    };

    // Certificate management functions
    const loadCertificates = async () => {
        try {
            const certs = await agentApiClient.listCertificates();
            setCertificates(certs);
        } catch (error) {
            console.error('Failed to load certificates:', error);
            setCertificates([]);
        }
    };

    const handleCertificateUpload = async (event) => {
        const file = event.target.files[0];
        if (!file) return;

        setCertUploadLoading(true);
        try {
            const result = await agentApiClient.uploadCertificate(file);
            setSaveMessage(result.message);
            await loadCertificates();
        } catch (error) {
            setSaveMessage(`Failed to upload certificate: ${error.message}`);
        } finally {
            setCertUploadLoading(false);
            event.target.value = ''; // Reset file input
        }
        setTimeout(() => setSaveMessage(''), 3000);
    };

    const handleDeleteCertificate = async (filename) => {
        if (!globalThis.confirm(`Delete certificate "${filename}"?`)) return;

        try {
            await agentApiClient.deleteCertificate(filename);
            setSaveMessage(`Deleted certificate: ${filename}`);
            await loadCertificates();
        } catch (error) {
            setSaveMessage(`Failed to delete certificate: ${error.message}`);
        }
        setTimeout(() => setSaveMessage(''), 3000);
    };

    // Test LLM connection
    const testLLMConnection = async (llmName, llmConfig) => {
        setLLMConnectionStatus(prev => ({
            ...prev,
            [llmName]: { status: 'testing', message: 'Testing connection...' }
        }));

        try {
            const result = globalThis.electronAPI?.testLLM
                ? await globalThis.electronAPI.testLLM(llmName, {})
                : await testLLMConnectionAPI(llmName, {});

            if (result.success) {
                setLLMConnectionStatus(prev => ({
                    ...prev,
                    [llmName]: { status: 'connected', message: result.message || 'Connected' }
                }));
            } else {
                setLLMConnectionStatus(prev => ({
                    ...prev,
                    [llmName]: { status: 'error', message: result.error || 'Connection failed' }
                }));
            }
        } catch (error) {
            setLLMConnectionStatus(prev => ({
                ...prev,
                [llmName]: { status: 'error', message: error.message || 'Connection test failed' }
            }));
        }
    };

    const handleOpenDialog = async (serverName = null) => {
        // Load current input values
        const currentInputValues = await getMCPInputValues();

        if (serverName) {
            // Edit mode
            const server = servers[serverName];
            const argsString = Array.isArray(server.args) ? server.args.join('\n') : '';
            const vars = extractInputVariables(argsString);

            setEditingServer(serverName);
            setFormData({
                name: serverName,
                command: server.command || '',
                args: argsString,
                type: server.type || 'stdio',
                icon: server.icon || '🔧',
                description: server.description || '',
                env: server.env ? JSON.stringify(server.env, null, 2) : ''
            });
            setDetectedInputVars(vars);
            setInputVarValues(currentInputValues);
        } else {
            // Add mode
            setEditingServer(null);
            setFormData({
                name: '',
                command: '',
                args: '',
                type: 'stdio',
                icon: '🔧',
                description: '',
                env: ''
            });
            setDetectedInputVars([]);
            setInputVarValues(currentInputValues);
        }
        setOpenDialog(true);
    };

    const handleCloseDialog = () => {
        setOpenDialog(false);
        setEditingServer(null);
        setDetectedInputVars([]);
    };

    const handleSave = async () => {
        try {
            // Save input variable values first
            for (const varName of detectedInputVars) {
                if (inputVarValues[varName]) {
                    await updateMCPInputValue(varName, inputVarValues[varName]);
                }
            }

            const args = parseArgsString(formData.args);

            let env = {};
            if (formData.env.trim()) {
                try {
                    env = parseEnvVars(formData.env);
                } catch (e) {
                    console.error('Invalid JSON in environment variables:', e);
                    alert('Invalid JSON in environment variables');
                    return;
                }
            }

            const serverConfig = {
                command: formData.command,
                args,
                type: formData.type,
                icon: formData.icon,
                description: formData.description,
                env
            };

            const serverNameToTest = formData.name;

            if (editingServer) {
                const newName = editingServer === formData.name ? null : formData.name;
                await updateMCPServer(editingServer, serverConfig, newName);
                if (editingServer === formData.name) {
                    setSaveMessage(`Updated server: ${formData.name}`);
                } else {
                    setSaveMessage(`Renamed server: ${editingServer} → ${formData.name}`);
                }
            } else {
                await addMCPServer(formData.name, serverConfig);
                setSaveMessage(`Added new server: ${formData.name}`);
            }

            await loadServers();
            handleCloseDialog();

            testServerConnection(serverNameToTest, serverConfig);

            setTimeout(() => setSaveMessage(''), 3000);
        } catch (error) {
            console.error('Error saving server:', error);
            alert('Failed to save server configuration');
        }
    };

    const handleDelete = async (serverName) => {
        if (globalThis.confirm(`Are you sure you want to delete "${serverName}"?`)) {
            try {
                await deleteMCPServer(serverName);
                setSaveMessage(`Deleted server: ${serverName}`);
                await loadServers();
                setTimeout(() => setSaveMessage(''), 3000);
            } catch (error) {
                console.error('Error deleting server:', error);
                alert('Failed to delete server');
            }
        }
    };

    // LLM Dialog handlers
    const handleOpenLLMDialog = async (llmName = null) => {
        if (llmName) {
            const llm = llms[llmName];
            setEditingLLM(llmName);
            setLLMFormData({
                name: llmName,
                provider: llm.provider || 'OpenAI',
                model: llm.model || '',
                icon: llm.icon || '🧠',
                endpoint: llm.endpoint || '',
                baseUrl: llm.baseUrl || '',
                temperature: llm.temperature ?? 0,
                // Backend exposes both snake_case and camelCase; tolerate either.
                use_for_embeddings: Boolean(llm.use_for_embeddings ?? llm.useForEmbeddings ?? false),
            });
        } else {
            setEditingLLM(null);
            setLLMFormData({
                name: '',
                provider: 'OpenAI',
                model: '',
                icon: '🧠',
                endpoint: '',
                baseUrl: '',
                temperature: 0,
                use_for_embeddings: false,
            });
        }
        setOpenLLMDialog(true);
    };

    const handleCloseLLMDialog = () => {
        setOpenLLMDialog(false);
        setEditingLLM(null);
    };

    const handleSaveLLM = async () => {
        try {
            const llmConfig = {
                provider: llmFormData.provider,
                model: llmFormData.model,
                icon: llmFormData.icon,
                ...(!isReasoningModel(llmFormData.model) && { temperature: llmFormData.temperature }),
                ...(llmFormData.endpoint && { endpoint: llmFormData.endpoint }),
                ...(llmFormData.baseUrl && { baseUrl: llmFormData.baseUrl }),
                ...(llmFormData.access_key_id && { access_key_id: llmFormData.access_key_id }),
                ...(llmFormData.secret_access_key && { secret_access_key: llmFormData.secret_access_key }),
                ...(llmFormData.session_token && { session_token: llmFormData.session_token }),
                // Always send the embedding flag (true or false) so unchecking
                // an existing row clears the flag — the server doesn't auto-
                // unset on an absent field.
                use_for_embeddings: Boolean(llmFormData.use_for_embeddings),
            };

            // On Add: row name = model string (legacy convention).
            // On Edit: keep the existing row name — do NOT pass newName, so
            // updateLLM() leaves the name field alone. Previously the form
            // re-synced name <- model on every keystroke in the model field
            // and then handleSaveLLM passed it as newName, silently renaming
            // the row (and breaking workflows that referenced the old name).
            if (editingLLM) {
                await updateLLM(editingLLM, llmConfig);
            } else {
                await addLLM(llmFormData.model, llmConfig);
            }

            setSaveMessage(editingLLM ? `Updated LLM: ${llmName}` : `Added new LLM: ${llmName}`);

            await loadLLMConfigs();
            handleCloseLLMDialog();
            setTimeout(() => setSaveMessage(''), 3000);
        } catch (error) {
            console.error('Error saving LLM:', error);
            alert('Failed to save LLM configuration');
        }
    };

    const handleDeleteLLM = async (llmName) => {
        if (globalThis.confirm(`Are you sure you want to delete "${llmName}"?`)) {
            try {
                await deleteLLM(llmName);
                setSaveMessage(`Deleted LLM: ${llmName}`);
                await loadLLMConfigs();
                setTimeout(() => setSaveMessage(''), 3000);
            } catch (error) {
                console.error('Error deleting LLM:', error);
                alert('Failed to delete LLM');
            }
        }
    };

    const PROVIDERS = ['AWS Bedrock'];

    const getModelOptions = (provider) => {
        switch (provider) {
            case 'AWS Bedrock':
                return [
                    'anthropic.claude-3-5-sonnet-20241022-v2:0',
                    'anthropic.claude-3-5-haiku-20241022-v1:0',
                    'anthropic.claude-3-opus-20240229-v1:0',
                    'anthropic.claude-3-sonnet-20240229-v1:0',
                    'anthropic.claude-3-haiku-20240307-v1:0',
                    'meta.llama3-1-70b-instruct-v1:0',
                    'meta.llama3-1-8b-instruct-v1:0',
                    'amazon.titan-embed-text-v1',
                    'amazon.titan-embed-text-v2:0',
                    'cohere.embed-english-v3',
                    'cohere.embed-multilingual-v3'
                ];
            default:
                return [];
        }
    };

    return (
        <div className="p-4 min-h-screen bg-background">
            <div className="max-w-6xl mx-auto px-0">
                <div className="mb-8">
                    <h1 className="text-4xl font-bold mb-2">Settings</h1>
                    <p className="text-muted-foreground">Configure system services, MCP servers, and language models</p>
                </div>

                {saveMessage && (
                    <div className="bg-green-50 border border-green-200 text-green-800 px-4 py-3 rounded mb-4">
                        {saveMessage}
                    </div>
                )}

                <Card className="mb-6">
                    <CardContent className="pt-6">
                        <div className="flex justify-between items-center mb-4">
                            <h2 className="text-xl font-medium">MCP Servers</h2>
                            <div className="flex gap-2">
                                <Button
                                    variant="outline"
                                    onClick={testAllConnections}
                                    disabled={Object.keys(servers).length === 0}
                                >
                                    <RefreshCw className="w-4 h-4 mr-2" />
                                    Test All
                                </Button>
                                <Button onClick={() => handleOpenDialog()}>
                                    <Plus className="w-4 h-4 mr-2" />
                                    Add Server
                                </Button>
                            </div>
                        </div>

                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Icon</TableHead>
                                        <TableHead>Name</TableHead>
                                        <TableHead>Description</TableHead>
                                        <TableHead>Command</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {Object.entries(servers).map(([name, config]) => {
                                        const status = connectionStatus[name];
                                        return (
                                            <TableRow key={name}>
                                                <TableCell>{config.icon || '🔧'}</TableCell>
                                                <TableCell>
                                                    <span className="text-sm font-medium">{name}</span>
                                                </TableCell>
                                                <TableCell>{config.description || '-'}</TableCell>
                                                <TableCell>
                                                    <code className="text-xs font-mono">{config.command}</code>
                                                </TableCell>
                                                <TableCell>
                                                    {status?.status === 'testing' && (
                                                        <Badge variant="outline" className="gap-1">
                                                            <Clock className="w-3 h-3" />
                                                            Testing...
                                                        </Badge>
                                                    )}
                                                    {status?.status === 'connected' && (
                                                        <Badge variant="default" className="bg-green-600 gap-1">
                                                            <CheckCircle className="w-3 h-3" />
                                                            Connected
                                                        </Badge>
                                                    )}
                                                    {status?.status === 'error' && (
                                                        <Badge variant="destructive" className="gap-1" title={status.message}>
                                                            <XCircle className="w-3 h-3" />
                                                            {status.message?.substring(0, 20) || 'Error'}
                                                        </Badge>
                                                    )}
                                                    {!status && (
                                                        <Badge variant="outline">Not tested</Badge>
                                                    )}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <div className="flex justify-end gap-1">
                                                        <Button
                                                            size="icon"
                                                            variant="ghost"
                                                            onClick={() => testServerConnection(name, config)}
                                                            title="Test Connection"
                                                            disabled={status?.status === 'testing'}
                                                        >
                                                            <RefreshCw className="w-4 h-4" />
                                                        </Button>
                                                        <Button
                                                            size="icon"
                                                            variant="ghost"
                                                            onClick={() => handleOpenDialog(name)}
                                                            title="Edit"
                                                        >
                                                            <Edit2 className="w-4 h-4" />
                                                        </Button>
                                                        <Button
                                                            size="icon"
                                                            variant="ghost"
                                                            onClick={() => handleDelete(name)}
                                                            title="Delete"
                                                            className="text-red-600 hover:text-red-700"
                                                        >
                                                            <Trash2 className="w-4 h-4" />
                                                        </Button>
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                    {Object.keys(servers).length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={6} className="text-center py-6 text-muted-foreground">
                                                No MCP servers configured. Click "Add Server" to get started.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                </Card>

                {/* Certificates Section */}
                <Card className="mb-6">
                    <CardContent className="pt-6">
                        <div className="flex justify-between items-center mb-4">
                            <h2 className="text-xl font-medium">SSL Certificates</h2>
                            <div>
                                <input
                                    id="certificate-upload"
                                    type="file"
                                    hidden
                                    accept=".pem,.crt,.cer,.cert"
                                    onChange={handleCertificateUpload}
                                />
                                <Button 
                                    asChild
                                    disabled={certUploadLoading}
                                >
                                    <label htmlFor="certificate-upload" className="cursor-pointer">
                                        {certUploadLoading ? (
                                            <>
                                                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                                Uploading...
                                            </>
                                        ) : (
                                            <>
                                                <Plus className="w-4 h-4 mr-2" />
                                                Upload Certificate
                                            </>
                                        )}
                                    </label>
                                </Button>
                            </div>
                        </div>
                        <p className="text-sm text-muted-foreground mb-4">
                            Upload SSL certificates for secure database connections. Certificates are automatically used when configuring MCP servers with SSL.
                        </p>
                        {certificates.length > 0 ? (
                            <div className="border rounded-lg">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Filename</TableHead>
                                            <TableHead>Container Path</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {certificates.map((filename) => (
                                            <TableRow key={filename}>
                                                <TableCell>
                                                    <span className="text-sm font-mono">{filename}</span>
                                                </TableCell>
                                                <TableCell>
                                                    <span className="text-sm font-mono text-muted-foreground">
                                                        /app/data/certs/{filename}
                                                    </span>
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        onClick={() => handleDeleteCertificate(filename)}
                                                        title="Delete Certificate"
                                                        className="text-red-600 hover:text-red-700"
                                                    >
                                                        <Trash2 className="w-4 h-4" />
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">
                                No certificates uploaded. Click "Upload Certificate" to add one.
                            </p>
                        )}
                    </CardContent>
                </Card>

                {/* AWS Bedrock Configuration (Credentials & Models) */}
                <Card className="mb-6">
                    <CardContent className="pt-6 space-y-8">
                        {/* Section 1: Credentials */}
                        <div>
                            <div className="mb-4">
                                <h2 className="text-xl font-medium text-foreground flex items-center gap-2">
                                    <span>🌩️</span> AWS Bedrock Credentials
                                </h2>
                                <p className="text-sm text-muted-foreground mt-0.5">
                                    Configure AWS credentials here before adding Bedrock language models
                                </p>
                            </div>
                            
                            <div className="grid gap-4 md:grid-cols-2 mt-4">
                                <div className="space-y-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="bedrock-region">AWS Region</Label>
                                        <Input
                                            id="bedrock-region"
                                            value={bedrockCredentials.region}
                                            onChange={(e) => setBedrockCredentials({ ...bedrockCredentials, region: e.target.value })}
                                            placeholder="us-east-1"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="bedrock-access-key">Access Key ID</Label>
                                        <div className="relative">
                                            <Input
                                                id="bedrock-access-key"
                                                value={bedrockCredentials.access_key_id}
                                                onChange={(e) => setBedrockCredentials({ ...bedrockCredentials, access_key_id: e.target.value })}
                                                type={showModelKeyFields.access_key_id ? 'text' : 'password'}
                                                placeholder="AKIA..."
                                                className="pr-10"
                                            />
                                            <Button
                                                type="button"
                                                size="icon"
                                                variant="ghost"
                                                onClick={() => setShowModelKeyFields(prev => ({ ...prev, access_key_id: !prev.access_key_id }))}
                                                className="absolute right-0 top-0 h-full"
                                            >
                                                {showModelKeyFields.access_key_id ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                            </Button>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="bedrock-secret-key">Secret Access Key</Label>
                                        <div className="relative">
                                            <Input
                                                id="bedrock-secret-key"
                                                value={bedrockCredentials.secret_access_key}
                                                onChange={(e) => setBedrockCredentials({ ...bedrockCredentials, secret_access_key: e.target.value })}
                                                type={showModelKeyFields.secret_access_key ? 'text' : 'password'}
                                                placeholder="Enter secret key"
                                                className="pr-10"
                                            />
                                            <Button
                                                type="button"
                                                size="icon"
                                                variant="ghost"
                                                onClick={() => setShowModelKeyFields(prev => ({ ...prev, secret_access_key: !prev.secret_access_key }))}
                                                className="absolute right-0 top-0 h-full"
                                            >
                                                {showModelKeyFields.secret_access_key ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                            </Button>
                                        </div>
                                    </div>
                                </div>
                                
                                <div className="space-y-4 flex flex-col justify-between">
                                    <div className="space-y-4">
                                        <div className="space-y-2">
                                            <Label htmlFor="bedrock-session-token">Session Token <span className="text-muted-foreground">(optional)</span></Label>
                                            <div className="relative">
                                                <Input
                                                    id="bedrock-session-token"
                                                    value={bedrockCredentials.session_token}
                                                    onChange={(e) => setBedrockCredentials({ ...bedrockCredentials, session_token: e.target.value })}
                                                    type={showModelKeyFields.session_token ? 'text' : 'password'}
                                                    placeholder="For temporary credentials"
                                                    className="pr-10"
                                                />
                                                <Button
                                                    type="button"
                                                    size="icon"
                                                    variant="ghost"
                                                    onClick={() => setShowModelKeyFields(prev => ({ ...prev, session_token: !prev.session_token }))}
                                                    className="absolute right-0 top-0 h-full"
                                                >
                                                    {showModelKeyFields.session_token ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                                </Button>
                                            </div>
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="bedrock-description">Description <span className="text-muted-foreground">(optional)</span></Label>
                                            <Input
                                                id="bedrock-description"
                                                value={bedrockCredentials.description}
                                                onChange={(e) => setBedrockCredentials({ ...bedrockCredentials, description: e.target.value })}
                                                placeholder="e.g., AWS Bedrock keys"
                                            />
                                        </div>
                                    </div>
                                    <div className="flex gap-2 pt-2 justify-end">
                                        {keyIsConfigured(findModelKey('AWS Bedrock')) && (
                                            <Button variant="outline" className="text-red-600 hover:text-red-700 border-red-200" onClick={handleDeleteBedrockCredentials}>
                                                <Trash2 className="w-4 h-4 mr-2" />
                                                Delete Credentials
                                            </Button>
                                        )}
                                        <Button onClick={handleSaveBedrockCredentials}>
                                            <Shield className="w-4 h-4 mr-2" />
                                            Save Credentials
                                        </Button>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div className="border-t border-border pt-6">
                            <div className="flex justify-between items-center mb-4">
                                <div>
                                    <h2 className="text-xl font-medium text-foreground flex items-center gap-2">
                                        <span>🧠</span> AWS Bedrock Models (LLMs)
                                    </h2>
                                    <p className="text-sm text-muted-foreground mt-0.5">
                                        Manage your configured Bedrock language models for workflows
                                    </p>
                                </div>
                                <div className="flex gap-2">
                                    <Button variant="outline" onClick={() => {
                                        setDiscoverProvider('AWS Bedrock');
                                        loadModelKeys();
                                        setDiscoveredModels([]);
                                        setSelectedModels([]);
                                        setOpenAWSDialog(true);
                                    }}>
                                        <RefreshCw className="w-4 h-4 mr-2" />
                                        Discover Models
                                    </Button>
                                    <Button onClick={() => handleOpenLLMDialog()}>
                                        <Plus className="w-4 h-4 mr-2" />
                                        Add Bedrock Model
                                    </Button>
                                </div>
                            </div>

                            {selectedForDelete.length > 0 && (
                                <div className="flex items-center gap-3 mt-2 p-2 border rounded-md bg-muted/50 mb-4">
                                    <span className="text-sm text-muted-foreground">{selectedForDelete.length} selected</span>
                                    <Button
                                        variant="destructive"
                                        size="sm"
                                        onClick={async () => {
                                            try {
                                                await bulkDeleteLLMs(selectedForDelete);
                                                setSaveMessage(`Deleted ${selectedForDelete.length} models`);
                                                setSelectedForDelete([]);
                                                await loadLLMConfigs();
                                                setTimeout(() => setSaveMessage(''), 3000);
                                            } catch (err) {
                                                alert('Failed to delete models: ' + err.message);
                                            }
                                        }}
                                    >
                                        <Trash2 className="w-3 h-3 mr-1" />
                                        Delete Selected
                                    </Button>
                                    <Button variant="ghost" size="sm" onClick={() => setSelectedForDelete([])}>
                                        Clear
                                    </Button>
                                </div>
                            )}

                            <div className="border rounded-lg">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead className="w-12">
                                                <input
                                                    type="checkbox"
                                                    checked={selectedForDelete.length > 0 && selectedForDelete.length === Object.keys(llms).length}
                                                    onChange={() => {
                                                        setSelectedForDelete(selectedForDelete.length === Object.keys(llms).length ? [] : Object.keys(llms));
                                                    }}
                                                />
                                            </TableHead>
                                            <TableHead>Icon</TableHead>
                                            <TableHead>Name</TableHead>
                                            <TableHead>Provider</TableHead>
                                            <TableHead>Model</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {Object.entries(llms).map(([name, config]) => {
                                            const status = llmConnectionStatus[name];
                                            return (
                                                <TableRow key={name}>
                                                    <TableCell>
                                                        <input
                                                            type="checkbox"
                                                            checked={selectedForDelete.includes(name)}
                                                            onChange={(e) => {
                                                                if (e.target.checked) {
                                                                    setSelectedForDelete([...selectedForDelete, name]);
                                                                } else {
                                                                    setSelectedForDelete(selectedForDelete.filter(n => n !== name));
                                                                }
                                                            }}
                                                        />
                                                    </TableCell>
                                                    <TableCell>{config.icon || '🧠'}</TableCell>
                                                    <TableCell>
                                                        <span className="text-sm font-medium">{name}</span>
                                                    </TableCell>
                                                    <TableCell>
                                                        <Badge variant="outline">{config.provider}</Badge>
                                                    </TableCell>
                                                    <TableCell>
                                                        <code className="text-xs font-mono">{config.model}</code>
                                                    </TableCell>
                                                    <TableCell>
                                                        {status?.status === 'testing' && (
                                                            <Badge variant="outline" className="gap-1">
                                                                <Clock className="w-3 h-3" />
                                                                Testing...
                                                            </Badge>
                                                        )}
                                                        {status?.status === 'connected' && (
                                                            <Badge variant="default" className="bg-green-600 gap-1">
                                                                <CheckCircle className="w-3 h-3" />
                                                                {status.message}
                                                            </Badge>
                                                        )}
                                                        {status?.status === 'error' && (
                                                            <Badge variant="destructive" className="gap-1" title={status.message}>
                                                                <XCircle className="w-3 h-3" />
                                                                {status.message}
                                                            </Badge>
                                                        )}
                                                        {!status && (
                                                            <Badge variant="outline">Not tested</Badge>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        <div className="flex justify-end gap-1">
                                                            <Button
                                                                size="icon"
                                                                variant="ghost"
                                                                onClick={() => testLLMConnection(name, config)}
                                                                title="Test Connection"
                                                            >
                                                                <RefreshCw className="w-4 h-4" />
                                                            </Button>
                                                            <Button
                                                                size="icon"
                                                                variant="ghost"
                                                                onClick={() => handleOpenLLMDialog(name)}
                                                                title="Edit"
                                                            >
                                                                <Edit2 className="w-4 h-4" />
                                                            </Button>
                                                            <Button
                                                                size="icon"
                                                                variant="ghost"
                                                                onClick={() => handleDeleteLLM(name)}
                                                                title="Delete"
                                                                className="text-red-600 hover:text-red-700"
                                                            >
                                                                <Trash2 className="w-4 h-4" />
                                                            </Button>
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                        {Object.keys(llms).length === 0 && (
                                            <TableRow>
                                                <TableCell colSpan={7} className="text-center py-6 text-muted-foreground">
                                                    No LLMs configured. Click "Add Bedrock Model" to get started.
                                                </TableCell>
                                            </TableRow>
                                        )}
                                    </TableBody>
                                </Table>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* Add/Edit MCP Server Dialog */}
                <Dialog open={openDialog} onOpenChange={(open) => !open && handleCloseDialog()}>
                    <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                        <DialogHeader>
                            <DialogTitle>
                                {editingServer ? `Edit Server: ${editingServer}` : 'Add New MCP Server'}
                            </DialogTitle>
                        </DialogHeader>
                        <div className="flex flex-col gap-4 mt-4">
                            <div className="space-y-2">
                                <Label htmlFor="server-name">Server Name <span className="text-red-500">*</span></Label>
                                <Input
                                    id="server-name"
                                    value={formData.name}
                                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                    disabled={!!editingServer}
                                    placeholder="e.g., playwright, postgres-dev"
                                />
                                <p className="text-sm text-muted-foreground">
                                    Unique identifier for the server
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="server-icon">Icon</Label>
                                <Select
                                    value={formData.icon}
                                    onValueChange={(value) => setFormData({ ...formData, icon: value })}
                                >
                                    <SelectTrigger id="server-icon">
                                        <SelectValue placeholder="Select an icon" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {MCP_SERVER_ICONS.map((icon) => (
                                            <SelectItem key={icon.value} value={icon.value}>
                                                {icon.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-muted-foreground">
                                    Select an emoji icon for the server
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="server-description">Description</Label>
                                <Input
                                    id="server-description"
                                    value={formData.description}
                                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                                    placeholder="Brief description"
                                />
                                <p className="text-sm text-muted-foreground">
                                    Brief description of what this server does
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="server-command">Command <span className="text-red-500">*</span></Label>
                                <Input
                                    id="server-command"
                                    value={formData.command}
                                    onChange={(e) => setFormData({ ...formData, command: e.target.value })}
                                    placeholder="npx"
                                />
                                <p className="text-sm text-muted-foreground">
                                    Command to execute (e.g., 'npx', 'uvx', 'python')
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="server-args">Arguments</Label>
                                <Textarea
                                    id="server-args"
                                    value={formData.args}
                                    onChange={(e) => {
                                        const newArgs = e.target.value;
                                        setFormData({ ...formData, args: newArgs });
                                        // Detect input variables and update state
                                        const vars = extractInputVariables(newArgs);
                                        setDetectedInputVars(vars);
                                        if (vars.length > 0) {
                                            const newInputVarValues = { ...inputVarValues };
                                            vars.forEach(v => {
                                                if (!(v in newInputVarValues)) {
                                                    newInputVarValues[v] = '';
                                                }
                                            });
                                            setInputVarValues(newInputVarValues);
                                        }
                                    }}
                                    rows={3}
                                    placeholder="One argument per line"
                                />
                                <p className="text-sm text-muted-foreground">
                                    One argument per line. Use $&#123;input:var_name&#125; for configurable values
                                </p>
                            </div>

                            {/* Dynamic input variable fields */}
                            {detectedInputVars.length > 0 && (
                                <div className="p-4 bg-muted rounded-lg flex flex-col gap-3">
                                    <h4 className="text-sm font-medium text-muted-foreground">
                                        Configure Input Variables
                                    </h4>
                                    {detectedInputVars.map((varName) => (
                                        <div key={varName} className="space-y-2">
                                            <Label htmlFor={`input-var-${varName}`}>{varName}</Label>
                                            <Input
                                                id={`input-var-${varName}`}
                                                value={inputVarValues[varName] || ''}
                                                onChange={(e) => setInputVarValues({
                                                    ...inputVarValues,
                                                    [varName]: e.target.value
                                                })}
                                                placeholder={`Value for $&#123;input:${varName}&#125;`}
                                            />
                                        </div>
                                    ))}
                                </div>
                            )}

                            <div className="space-y-2">
                                <Label htmlFor="server-type">Type</Label>
                                <Input
                                    id="server-type"
                                    value={formData.type}
                                    onChange={(e) => setFormData({ ...formData, type: e.target.value })}
                                    placeholder="stdio"
                                />
                                <p className="text-sm text-muted-foreground">
                                    Connection type (usually 'stdio')
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="server-env">Environment Variables (JSON)</Label>
                                <Textarea
                                    id="server-env"
                                    value={formData.env}
                                    onChange={(e) => setFormData({ ...formData, env: e.target.value })}
                                    rows={4}
                                    placeholder='{"AWS_PROFILE": "default"}'
                                />
                                <p className="text-sm text-muted-foreground">
                                    Optional JSON object for environment variables
                                </p>
                            </div>
                        </div>
                        <DialogFooter className="mt-6">
                            <Button variant="outline" onClick={handleCloseDialog}>Cancel</Button>
                            <Button onClick={handleSave} disabled={!formData.name || !formData.command}>
                                {editingServer ? 'Update' : 'Add'}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {/* Add/Edit LLM Dialog */}
                <Dialog open={openLLMDialog} onOpenChange={(open) => !open && handleCloseLLMDialog()}>
                    <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
                        <DialogHeader>
                            <DialogTitle>
                                {editingLLM ? `Edit Bedrock Model: ${llmFormData.model || editingLLM}` : 'Add New AWS Bedrock Model'}
                            </DialogTitle>
                        </DialogHeader>
                        <div className="flex flex-col gap-4 mt-4">
                            <div className="space-y-2">
                                <Label htmlFor="llm-icon">Icon</Label>
                                <Select
                                    value={llmFormData.icon}
                                    onValueChange={(value) => setLLMFormData({ ...llmFormData, icon: value })}
                                >
                                    <SelectTrigger id="llm-icon">
                                        <SelectValue placeholder="Select an icon" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {LLM_ICONS.map((icon) => (
                                            <SelectItem key={icon.value} value={icon.value}>
                                                {icon.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-muted-foreground">
                                    Select an emoji icon for the LLM
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="llm-provider">Provider</Label>
                                <Input
                                    id="llm-provider"
                                    value="AWS Bedrock"
                                    disabled
                                    className="bg-muted text-muted-foreground"
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="llm-model">Model ID</Label>
                                <Input
                                    id="llm-model"
                                    value={llmFormData.model}
                                    onChange={(e) => {
                                        const newValue = e.target.value;
                                        setLLMFormData(prev => ({
                                            ...prev,
                                            model: newValue,
                                            ...(editingLLM ? {} : { name: newValue }),
                                        }));
                                    }}
                                    placeholder="Select or type Bedrock model ID"
                                    list="model-suggestions"
                                />
                                <datalist id="model-suggestions">
                                    {getModelOptions('AWS Bedrock').map(model => (
                                        <option key={model} value={model} />
                                    ))}
                                </datalist>
                                <p className="text-sm text-muted-foreground">
                                    Select from suggestions or type a custom Bedrock model identifier (e.g., anthropic.claude-3-5-sonnet-20241022-v2:0)
                                </p>
                            </div>

                            {/* Temperature Setting */}
                            {isReasoningModel(llmFormData.model) ? (
                                <Alert className="flex items-start gap-2">
                                    <Info className="w-4 h-4 mt-0.5" />
                                    <div>
                                        <p className="text-sm">
                                            Reasoning models do not support temperature settings.
                                        </p>
                                    </div>
                                </Alert>
                            ) : (
                                <div className="space-y-2">
                                    <Label htmlFor="llm-temperature">Temperature</Label>
                                    <Input
                                        id="llm-temperature"
                                        type="number"
                                        value={llmFormData.temperature}
                                        onChange={(e) => setLLMFormData({ 
                                            ...llmFormData, 
                                            temperature: Math.max(0, Math.min(1, Number.parseFloat(e.target.value) || 0)) 
                                        })}
                                        min="0"
                                        max="1"
                                        step="0.1"
                                    />
                                    <p className="text-sm text-muted-foreground">
                                        Controls randomness (0 = deterministic, 1 = creative)
                                    </p>
                                </div>
                            )}

                            {/* AWS credentials info Box — sourced from Model Keys */}
                            {(() => {
                                const mk = findModelKey('AWS Bedrock');
                                const hasCreds = mk && (mk.has_access_credentials || mk.has_api_key);
                                return (
                                    <div className={`rounded-md border p-3 flex items-start gap-3 ${hasCreds ? 'border-green-200 bg-green-50/40' : 'border-amber-200 bg-amber-50/40'}`}>
                                        {hasCreds ? (
                                            <>
                                                <CheckCircle className="w-4 h-4 text-green-600 mt-0.5 shrink-0" />
                                                <div>
                                                    <p className="text-sm font-medium text-green-800">AWS credentials configured</p>
                                                    <p className="text-xs text-green-700 mt-0.5">
                                                        Using credentials from AWS Bedrock Credentials{mk.region ? ` · Region: ${mk.region}` : ''}
                                                    </p>
                                                </div>
                                            </>
                                        ) : (
                                            <>
                                                <XCircle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                                                <div>
                                                    <p className="text-sm font-medium text-amber-800">No AWS credentials found</p>
                                                    <p className="text-xs text-amber-700 mt-0.5">
                                                        Go to <strong>AWS Bedrock Credentials</strong> section above to configure credentials
                                                    </p>
                                                </div>
                                            </>
                                        )}
                                    </div>
                                );
                            })()}

                            {/* Use-for-embeddings flag */}
                            <div className="rounded-md border p-3 bg-muted/30">
                                <label className="flex items-start gap-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="mt-1"
                                        checked={!!llmFormData.use_for_embeddings}
                                        onChange={(e) => setLLMFormData({
                                            ...llmFormData,
                                            use_for_embeddings: e.target.checked,
                                        })}
                                    />
                                    <div>
                                        <p className="text-sm font-medium">Use for embeddings</p>
                                        <p className="text-xs text-muted-foreground mt-0.5">
                                            Pick this model for vector embeddings (code indexer, semantic search).
                                            Only one row can be flagged at a time — saving here will unset any other.
                                        </p>
                                    </div>
                                </label>
                            </div>

                            {/* Data leaves container warning */}
                            {llmFormData.use_for_embeddings && (
                                <div
                                    className="rounded-md border border-amber-300 bg-amber-50 p-3"
                                    role="alert"
                                    aria-live="polite"
                                >
                                    <div className="flex items-start gap-3">
                                        <span aria-hidden="true" className="text-amber-700 text-base leading-none">
                                            ⚠
                                        </span>
                                        <div>
                                            <p className="text-sm font-medium text-amber-900">
                                                Data leaves the container during indexing
                                            </p>
                                            <p className="text-xs text-amber-800 mt-1">
                                                Embedding calls flow to <strong>AWS Bedrock</strong>. Tree-sitter
                                                chunks (function bodies, signatures, docstrings) are sent to the
                                                model for vector generation. Other layers — SCIP, trigram, BM25
                                                and the 7-stage reranker — run entirely inside the container.
                                                Confirm this is acceptable for your KYC compliance posture before
                                                enabling.
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                        <DialogFooter className="mt-6">
                            <Button variant="outline" onClick={handleCloseLLMDialog}>Cancel</Button>
                            <Button
                                onClick={handleSaveLLM}
                                disabled={!llmFormData.model}
                            >
                                {editingLLM ? 'Update' : 'Add'}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {/* AWS Bedrock Discovery Dialog */}
                <Dialog open={openAWSDialog} onOpenChange={(open) => {
                    if (!open) { setDiscoveredModels([]); setSelectedModels([]); }
                    setOpenAWSDialog(open);
                }}>
                    <DialogContent className="sm:max-w-[600px] max-h-[80vh]">
                        <DialogHeader>
                            <DialogTitle>Discover AWS Bedrock Models</DialogTitle>
                        </DialogHeader>
                        {discoveredModels.length === 0 ? (
                            <div className="flex flex-col gap-4 mt-4">
                                <div className="space-y-2 bg-muted/30 p-4 rounded-lg border border-border">
                                    <div className="flex items-center gap-2 font-medium text-sm text-foreground">
                                        🌩️ AWS Bedrock Model Discovery
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Querying AWS Bedrock to list all active foundation models in your configured region.
                                    </p>
                                    {discoverableProviders.length > 0 ? (
                                        <div className="mt-3 text-xs text-green-700 bg-green-50/50 border border-green-200 rounded px-2.5 py-1.5 flex items-center gap-1.5 w-fit">
                                            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></span>
                                            Using configured credentials (Region: {discoverableProviders[0].region || 'us-east-1'})
                                        </div>
                                    ) : (
                                        <div className="mt-3 text-xs text-amber-700 bg-amber-50/50 border border-amber-200 rounded px-2.5 py-1.5">
                                            ⚠️ No AWS credentials saved. Please configure credentials in the AWS Bedrock Credentials section first.
                                        </div>
                                    )}
                                </div>
                            </div>
                        ) : (
                            <div className="flex flex-col gap-3 mt-4">
                                <p className="text-sm text-muted-foreground">
                                    {discoveredModels.length} models found for {discoverProvider}
                                    {discoveredModels[0]?.region ? ` in ${discoveredModels[0].region}` : ''}
                                </p>
                                <div className="border rounded-md overflow-y-auto max-h-[50vh]">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead className="w-12">
                                                    <input
                                                        type="checkbox"
                                                        checked={discoveredModels.filter(m => !m.already_exists).length > 0 && selectedModels.length === discoveredModels.filter(m => !m.already_exists).length}
                                                        onChange={() => {
                                                            const addable = discoveredModels.filter(m => !m.already_exists).map(m => m.name);
                                                            setSelectedModels(selectedModels.length === addable.length ? [] : addable);
                                                        }}
                                                    />
                                                </TableHead>
                                                <TableHead>Model</TableHead>
                                                <TableHead>ID</TableHead>
                                                <TableHead>Status</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {discoveredModels.map((model) => (
                                                <TableRow key={model.name} className={model.already_exists ? 'opacity-50' : ''}>
                                                    <TableCell>
                                                        <input
                                                            type="checkbox"
                                                            checked={selectedModels.includes(model.name)}
                                                            disabled={model.already_exists}
                                                            onChange={(e) => {
                                                                 if (e.target.checked) {
                                                                     setSelectedModels([...selectedModels, model.name]);
                                                                 } else {
                                                                     setSelectedModels(selectedModels.filter(n => n !== model.name));
                                                                 }
                                                            }}
                                                        />
                                                    </TableCell>
                                                    <TableCell>
                                                        <span className="mr-1">{model.icon}</span>
                                                        <span className="font-medium">{model.name}</span>
                                                    </TableCell>
                                                    <TableCell>
                                                        <code className="text-xs">{model.model}</code>
                                                    </TableCell>
                                                    <TableCell>
                                                        {model.already_exists ? (
                                                            <Badge variant="secondary">Exists</Badge>
                                                        ) : (
                                                            <Badge variant="outline">New</Badge>
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </TableBody>
                                    </Table>
                                </div>
                            </div>
                        )}
                        <DialogFooter className="mt-4">
                            <Button variant="outline" onClick={() => {
                                setOpenAWSDialog(false);
                                setDiscoveredModels([]);
                                setSelectedModels([]);
                            }}>Cancel</Button>
                            {discoveredModels.length === 0 ? (
                                <Button
                                    onClick={async () => {
                                        if (!discoverProvider) return;
                                        setAwsDiscovering(true);
                                        try {
                                            let result;
                                            if (globalThis.electronAPI?.discoverModels) {
                                                result = await globalThis.electronAPI.discoverModels(discoverProvider);
                                            } else {
                                                result = await discoverModels(discoverProvider);
                                            }
                                            setDiscoveredModels(result.models || []);
                                            setSelectedModels((result.models || []).filter(m => !m.already_exists).map(m => m.name));
                                        } catch (err) {
                                            alert('Failed to discover models: ' + err.message);
                                        } finally {
                                            setAwsDiscovering(false);
                                        }
                                    }}
                                    disabled={awsDiscovering || discoverableProviders.length === 0}
                                >
                                    {awsDiscovering ? (
                                        <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Discovering...</>
                                    ) : 'Discover Models'}
                                </Button>
                            ) : (
                                <Button
                                    onClick={async () => {
                                        if (selectedModels.length === 0) return;
                                        setAwsAdding(true);
                                        try {
                                            const modelsToAdd = discoveredModels.filter(m => selectedModels.includes(m.name));
                                            const region = discoveredModels[0]?.region || awsFormData.region;
                                            let result;
                                            if (globalThis.electronAPI?.addDiscoveredModels) {
                                                result = await globalThis.electronAPI.addDiscoveredModels(modelsToAdd, region);
                                            } else {
                                                result = await addDiscoveredModels(modelsToAdd, region);
                                            }
                                            setSaveMessage(`Added ${result.added_count} models, skipped ${result.skipped_count} existing`);
                                            setOpenAWSDialog(false);
                                            setDiscoveredModels([]);
                                            setSelectedModels([]);
                                            await loadLLMConfigs();
                                            setTimeout(() => setSaveMessage(''), 3000);
                                        } catch (err) {
                                            alert('Failed to add models: ' + err.message);
                                        } finally {
                                            setAwsAdding(false);
                                        }
                                    }}
                                    disabled={selectedModels.length === 0 || awsAdding}
                                >
                                    {awsAdding ? (
                                        <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Adding...</>
                                    ) : `Add Selected (${selectedModels.length})`}
                                </Button>
                            )}
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {/* Dialog for model key is removed since it is now configured inline */}

            </div>
        </div>
    );
};

export default Settings;
