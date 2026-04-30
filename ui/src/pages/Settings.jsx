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
import { getModelKeys, upsertModelKey, deleteModelKey, getProviderSchemas } from '../services/modelKeyService';
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
    // Debug: Verify new code is loaded
    console.log('Settings component loaded - VERSION 2.0 - Fixed provider fields - Timestamp:', new Date().toISOString());
    
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

    // API Health state
    const [apiHealth, setApiHealth] = useState(null);
    const [apiHealthLoading, setApiHealthLoading] = useState(false);

    // Model Keys state
    const [modelKeys, setModelKeys] = useState([]);
    const [providerSchemas, setProviderSchemas] = useState([]);
    const [openModelKeyDialog, setOpenModelKeyDialog] = useState(false);
    const [editingModelKeyProvider, setEditingModelKeyProvider] = useState(null);
    const [modelKeyFormData, setModelKeyFormData] = useState({
        provider: 'OpenAI',
        api_key: '',
        secret_key: '',
        endpoint: '',
        region: '',
        access_key_id: '',
        secret_access_key: '',
        session_token: '',
        description: '',
    });
    const [showModelKeyFields, setShowModelKeyFields] = useState({});
    const originalMaskedValues = useRef({});

    const MODEL_KEY_PROVIDERS = [
        { value: 'openai', label: 'OpenAI', icon: '🧠', fields: ['api_key'] },
        { value: 'anthropic', label: 'Anthropic', icon: '🤖', fields: ['api_key'] },
        { value: 'google', label: 'Google AI', icon: '✨', fields: ['api_key'] },
        { value: 'groq', label: 'Groq', icon: '⚡', fields: ['api_key'] },
        { value: 'azure openai', label: 'Azure OpenAI', icon: '☁️', fields: ['api_key', 'endpoint'] },
        { value: 'bedrock', label: 'AWS Bedrock', icon: '🌩️', fields: ['access_key_id', 'secret_access_key', 'session_token', 'region'] },
        { value: 'ollama', label: 'Ollama', icon: '🦙', fields: ['endpoint'] },
        { value: 'custom', label: 'Custom', icon: '🔧', fields: ['api_key', 'secret_key', 'endpoint'] },
    ];

    const emptyModelKeyForm = (provider) => {
        // Default to 'openai' if no provider specified
        const selectedProvider = provider || 'openai';
        
        // Only initialize fields that are relevant for this provider
        const providerConfig = MODEL_KEY_PROVIDERS.find(p => p.value === selectedProvider);
        const base = {
            provider: selectedProvider,
            description: '',
        };
        
        // Only add fields that are configured for this provider
        if (providerConfig) {
            providerConfig.fields.forEach(field => {
                base[field] = '';
            });
        }
        
        // Set default values for specific providers
        if (selectedProvider === 'azure openai' && base.endpoint !== undefined) base.endpoint = 'https://your-resource.openai.azure.com';
        if (selectedProvider === 'ollama' && base.endpoint !== undefined) base.endpoint = 'http://localhost:11434';
        if (selectedProvider === 'bedrock' && base.region !== undefined) base.region = 'us-east-1';
        
        return base;
    };

    const discoverableProviders = (() => {
        const providers = [];
        const supportedForDiscovery = ['openai', 'anthropic', 'google', 'groq', 'azure openai', 'ollama', 'bedrock'];
        for (const mk of modelKeys) {
            if (!supportedForDiscovery.includes(mk.provider)) continue;
            const hasCreds = mk.has_api_key || mk.has_secret_key || mk.has_access_credentials || mk.endpoint;
            if (!hasCreds) continue;
            const providerCfg = MODEL_KEY_PROVIDERS.find(p => p.value === mk.provider);
            providers.push({
                provider: mk.provider,
                icon: providerCfg?.icon || '🔧',
                label: providerCfg?.label || mk.provider,
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
        checkApiHealth();
        loadModelKeys();
        loadProviderSchemas();
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

    const loadProviderSchemas = async () => {
        try {
            const schemas = await getProviderSchemas();
            setProviderSchemas(schemas);
        } catch (error) {
            console.error('Failed to load provider schemas:', error);
            setProviderSchemas([]);
        }
    };

    const handleOpenModelKeyDialog = (provider = null) => {
        if (provider) {
            const existing = modelKeys.find(k => k.provider === provider);
            const providerConfig = MODEL_KEY_PROVIDERS.find(p => p.value === provider);
            setEditingModelKeyProvider(provider);
            
            // Only include fields that are configured for this provider
            const formData = {
                provider,
                description: existing?.description || '',
            };
            
            if (providerConfig) {
                providerConfig.fields.forEach(field => {
                    formData[field] = existing?.[field] || '';
                });
            }
            
            setModelKeyFormData(formData);
            originalMaskedValues.current = { ...formData };
        } else {
            setEditingModelKeyProvider(null);
            setModelKeyFormData(emptyModelKeyForm());
            originalMaskedValues.current = {};
        }
        setOpenModelKeyDialog(true);
    };

    const handleCloseModelKeyDialog = () => {
        setOpenModelKeyDialog(false);
        setEditingModelKeyProvider(null);
    };

    const handleSaveModelKey = async () => {
        try {
            const providerConfig = MODEL_KEY_PROVIDERS.find(p => p.value === modelKeyFormData.provider);
            const data = { provider: modelKeyFormData.provider };
            
            console.log('=== DEBUG: handleSaveModelKey ===');
            console.log('Provider:', modelKeyFormData.provider);
            console.log('Provider config:', providerConfig);
            console.log('Form data before filtering:', modelKeyFormData);
            
            // Only include fields that are configured for this provider
            if (providerConfig) {
                const validFields = providerConfig.fields;
                console.log('Valid fields for this provider:', validFields);
                
                validFields.forEach(field => {
                    const value = modelKeyFormData[field];
                    console.log(`Checking field '${field}':`, value);
                    // Include the field if it has a value and either:
                    // 1. It's different from the masked value (for edits), or
                    // 2. We're creating a new entry (no original masked value)
                    if (value && (value !== originalMaskedValues.current[field] || !editingModelKeyProvider)) {
                        data[field] = value;
                        console.log(`  -> Including field '${field}'`);
                    } else {
                        console.log(`  -> Skipping field '${field}' (empty or unchanged)`);
                    }
                });
            }
            
            // Add description if provided
            if (modelKeyFormData.description) {
                data.description = modelKeyFormData.description;
            }
            
            console.log('Final data to send:', data);
            console.log('=== END DEBUG ===');
            
            await upsertModelKey(data);
            setSaveMessage(`Saved keys for ${modelKeyFormData.provider}`);
            await loadModelKeys();
            handleCloseModelKeyDialog();
        } catch (error) {
            console.error('Error saving model key:', error);
            
            // Try to parse validation error from API
            let errorMessage = 'Failed to save: ' + error.message;
            try {
                // Check if error response contains validation details
                const response = await error.response?.json();
                if (response?.detail) {
                    const detail = response.detail;
                    if (detail.errors && Array.isArray(detail.errors)) {
                        errorMessage = 'Validation failed:\n' + 
                            detail.errors.map(e => `• ${e.field}: ${e.message}`).join('\n');
                    } else if (typeof detail === 'string') {
                        errorMessage = detail;
                    }
                }
            } catch (parseError) {
                // Use original error message
            }
            
            alert(errorMessage);
        }
        setTimeout(() => setSaveMessage(''), 3000);
    };

    const handleDeleteModelKey = async (provider) => {
        if (globalThis.confirm(`Delete all keys for "${provider}"?`)) {
            try {
                await deleteModelKey(provider);
                setSaveMessage(`Deleted keys for ${provider}`);
                await loadModelKeys();
            } catch (error) {
                console.error('Error deleting model key:', error);
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

    // API Health check
    const checkApiHealth = async () => {
        setApiHealthLoading(true);
        try {
            const health = await agentApiClient.getHealth();
            setApiHealth({
                status: 'healthy',
                ...health
            });
        } catch (error) {
            setApiHealth({
                status: 'error',
                message: error.message || 'Failed to connect to API'
            });
        } finally {
            setApiHealthLoading(false);
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
                temperature: llm.temperature ?? 0
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
                temperature: 0
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
            };

            const llmName = llmFormData.model;

            if (editingLLM) {
                await updateLLM(editingLLM, llmConfig, llmName);
            } else {
                await addLLM(llmName, llmConfig);
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

    const PROVIDERS = ['OpenAI', 'Groq', 'Anthropic', 'Google', 'Azure OpenAI', 'Ollama', 'Custom'];

    const getModelOptions = (provider) => {
        switch (provider) {
            case 'OpenAI':
                return ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo', 'o1', 'o1-mini', 'o1-preview'];
            case 'Groq':
                return ['llama-3.3-70b-versatile', 'llama-3.1-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768', 'gemma2-9b-it'];
            case 'Anthropic':
                return ['claude-sonnet-4-20250514', 'claude-opus-4-20250514', 'claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229'];
            case 'Google':
                return ['gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-1.0-pro'];
            case 'Azure OpenAI':
                return ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-4', 'gpt-35-turbo'];
            case 'Ollama':
                return ['llama3.2', 'llama3.1', 'mistral', 'mixtral', 'codellama', 'phi3', 'gemma2'];
            case 'Custom':
                return [];
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

                {/* API Health Check Section */}
                <Card className="mb-6">
                    <CardContent className="pt-6">
                        <div className="flex justify-between items-center mb-4">
                            <h2 className="text-xl font-medium">API Status</h2>
                            <Button
                                variant="outline"
                                onClick={checkApiHealth}
                                disabled={apiHealthLoading}
                            >
                                {apiHealthLoading ? (
                                    <>
                                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                        Checking...
                                    </>
                                ) : (
                                    <>
                                        <RefreshCw className="w-4 h-4 mr-2" />
                                        Check API
                                    </>
                                )}
                            </Button>
                        </div>
                        <div className="flex items-center gap-4">
                            {apiHealth ? (
                                <>
                                    {apiHealth.status === 'healthy' ? (
                                        <Badge variant="default" className="bg-green-600">
                                            <CheckCircle className="w-3 h-3 mr-1" />
                                            Connected
                                        </Badge>
                                    ) : (
                                        <Badge variant="destructive">
                                            <XCircle className="w-3 h-3 mr-1" />
                                            Error
                                        </Badge>
                                    )}
                                    <p className="text-sm text-muted-foreground">
                                        {apiHealth.status === 'healthy' ? (
                                            <>
                                                Scheduler: {apiHealth.scheduler_running ? 'Running' : 'Stopped'}
                                            </>
                                        ) : (
                                            apiHealth.message
                                        )}
                                    </p>
                                </>
                            ) : (
                                <p className="text-sm text-muted-foreground">
                                    Click "Check API" to verify connection
                                </p>
                            )}
                        </div>
                    </CardContent>
                </Card>

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

                {/* LLM Configuration Section */}
                <Card className="mb-6">
                    <CardContent className="pt-6">
                        <div className="flex justify-between items-center mb-4">
                            <h2 className="text-xl font-medium">Language Models (LLMs)</h2>
                            <div className="flex gap-2">
                                <Button variant="outline" onClick={() => {
                                    setDiscoverProvider('');
                                    setAWSFormData({ region: 'us-east-1', access_key_id: '', secret_access_key: '', session_token: '' });
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
                                    Add LLM
                                </Button>
                            </div>
                        </div>

                        {selectedForDelete.length > 0 && (
                            <div className="flex items-center gap-3 mt-2 p-2 border rounded-md bg-muted/50">
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
                                                No LLMs configured. Click "Add LLM" to get started.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </div>
                    </CardContent>
                </Card>

                {/* Model Keys Section */}
                <Card className="mb-6">
                    <CardContent className="pt-6">
                        <div className="flex justify-between items-center mb-2">
                            <h2 className="text-xl font-medium">Model Keys</h2>
                            <Button onClick={() => handleOpenModelKeyDialog()}>
                                <Plus className="w-4 h-4 mr-2" />
                                Add Provider Key
                            </Button>
                        </div>
                        <p className="text-sm text-muted-foreground mb-4">
                            Centralized API key and credential management for all model providers
                        </p>
                        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                            {providerSchemas.map((schema) => {
                                const existing = modelKeys.find(k => k.provider.toLowerCase() === schema.provider.toLowerCase());
                                const configuredFields = existing?.configured_fields || [];
                                
                                return (
                                    <div key={schema.provider} className="border rounded-lg p-4">
                                        <div className="flex items-start justify-between mb-2">
                                            <div className="flex items-center gap-2">
                                                <span className="text-xl">
                                                    {schema.provider === 'openai' && '🧠'}
                                                    {schema.provider === 'anthropic' && '🤖'}
                                                    {schema.provider === 'groq' && '⚡'}
                                                    {schema.provider === 'bedrock' && '🌩️'}
                                                    {schema.provider === 'cloudwatch' && '☁️'}
                                                </span>
                                                <span className="font-medium text-sm">{schema.display_name}</span>
                                            </div>
                                            <div className="flex gap-1">
                                                <Button
                                                    size="icon"
                                                    variant="ghost"
                                                    className="h-7 w-7"
                                                    onClick={() => handleOpenModelKeyDialog(schema.provider)}
                                                    title="Edit"
                                                >
                                                    <Edit2 className="w-3.5 h-3.5" />
                                                </Button>
                                                {existing && (
                                                    <Button
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-7 w-7 text-red-600 hover:text-red-700"
                                                        onClick={() => handleDeleteModelKey(schema.provider)}
                                                        title="Delete"
                                                    >
                                                        <Trash2 className="w-3.5 h-3.5" />
                                                    </Button>
                                                )}
                                            </div>
                                        </div>
                                        
                                        {/* Authentication Type Badge */}
                                        <div className="mb-2">
                                            {schema.auth_type === 'api_key' && (
                                                <Badge variant="outline" className="text-xs">
                                                    <Key className="w-3 h-3 mr-1" /> API Key Auth
                                                </Badge>
                                            )}
                                            {schema.auth_type === 'aws_iam' && (
                                                <Badge variant="outline" className="text-xs">
                                                    <Cloud className="w-3 h-3 mr-1" /> AWS IAM Auth
                                                </Badge>
                                            )}
                                            {schema.auth_type === 'api_key_or_aws_iam' && (
                                                <Badge variant="outline" className="text-xs">
                                                    <Key className="w-3 h-3 mr-1" /> Dual Auth
                                                </Badge>
                                            )}
                                        </div>
                                        
                                        {/* Configured Fields */}
                                        <div className="flex flex-wrap gap-1 mb-2">
                                            {existing && configuredFields.length > 0 ? (
                                                configuredFields.map(field => (
                                                    <Badge 
                                                        key={field} 
                                                        variant="outline" 
                                                        className="bg-green-50 text-green-700 border-green-200 text-xs"
                                                    >
                                                        {field.replace(/_/g, ' ')}
                                                    </Badge>
                                                ))
                                            ) : (
                                                <Badge variant="outline" className="text-muted-foreground text-xs">
                                                    Not configured
                                                </Badge>
                                            )}
                                        </div>
                                        
                                        {/* Additional Info */}
                                        {existing?.endpoint && (
                                            <p className="text-xs text-muted-foreground font-mono truncate mb-1" title={existing.endpoint}>
                                                {existing.endpoint}
                                            </p>
                                        )}
                                        {existing?.region && (
                                            <p className="text-xs text-muted-foreground mb-1">
                                                Region: {existing.region}
                                            </p>
                                        )}
                                        
                                        {/* Description */}
                                        <p className="text-xs text-muted-foreground line-clamp-2">
                                            {schema.description}
                                        </p>
                                        
                                        {!existing && (
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                className="mt-2 w-full text-xs h-7"
                                                onClick={() => handleOpenModelKeyDialog(schema.provider)}
                                            >
                                                <Plus className="w-3 h-3 mr-1" /> Configure
                                            </Button>
                                        )}
                                    </div>
                                );
                            })}
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
                                {editingLLM ? `Edit LLM: ${llmFormData.model || editingLLM}` : 'Add New LLM'}
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
                                <select
                                    id="llm-provider"
                                    value={llmFormData.provider}
                                    onChange={(e) => setLLMFormData({
                                        ...llmFormData,
                                        provider: e.target.value,
                                        model: '' // Reset model when provider changes
                                    })}
                                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                >
                                    {PROVIDERS.map(provider => (
                                        <option key={provider} value={provider}>{provider}</option>
                                    ))}
                                </select>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="llm-model">Model</Label>
                                <Input
                                    id="llm-model"
                                    value={llmFormData.model}
                                    onChange={(e) => {
                                        const newValue = e.target.value;
                                        setLLMFormData({ ...llmFormData, model: newValue, name: newValue });
                                    }}
                                    placeholder={(() => {
                                        if (llmFormData.provider === 'Ollama') return 'e.g., llama3.2:latest';
                                        if (llmFormData.provider === 'Custom') return 'Enter custom model name';
                                        return 'Select or type model name';
                                    })()}
                                    list={llmFormData.provider === 'Custom' ? undefined : 'model-suggestions'}
                                />
                                {llmFormData.provider !== 'Custom' && (
                                    <datalist id="model-suggestions">
                                        {getModelOptions(llmFormData.provider).map(model => (
                                            <option key={model} value={model} />
                                        ))}
                                    </datalist>
                                )}
                                <p className="text-sm text-muted-foreground">
                                    {llmFormData.provider === 'Custom' 
                                        ? 'Enter the model identifier'
                                        : 'Select from suggestions or type a custom model name'}
                                </p>
                            </div>

                            {llmFormData.provider === 'Azure OpenAI' && (
                                <div className="space-y-2">
                                    <Label htmlFor="llm-endpoint">Endpoint URL</Label>
                                    <Input
                                        id="llm-endpoint"
                                        value={llmFormData.endpoint || ''}
                                        onChange={(e) => setLLMFormData({ ...llmFormData, endpoint: e.target.value })}
                                        placeholder="https://your-resource.openai.azure.com"
                                    />
                                    <p className="text-sm text-muted-foreground">
                                        Your Azure OpenAI endpoint URL
                                    </p>
                                </div>
                            )}

                            {llmFormData.provider === 'Ollama' && (
                                <div className="space-y-2">
                                    <Label htmlFor="llm-baseUrl">Base URL</Label>
                                    <Input
                                        id="llm-baseUrl"
                                        value={llmFormData.baseUrl || 'http://localhost:11434'}
                                        onChange={(e) => setLLMFormData({ ...llmFormData, baseUrl: e.target.value })}
                                    />
                                    <p className="text-sm text-muted-foreground">
                                        Ollama server URL (default: http://localhost:11434)
                                    </p>
                                </div>
                            )}

                            {/* Temperature Setting */}
                            {isReasoningModel(llmFormData.model) ? (
                                <Alert className="flex items-start gap-2">
                                    <Info className="w-4 h-4 mt-0.5" />
                                    <div>
                                        <p className="text-sm">
                                            Reasoning models (o1, o3, o4-mini, etc.) do not support temperature settings.
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

                            {/* AWS Credentials Section */}
                            {(llmFormData.provider === 'AWS Bedrock' || llmFormData.provider === 'Bedrock' || llmFormData.provider === 'bedrock') && (
                                <div className="p-4 bg-muted rounded-lg flex flex-col gap-3">
                                    <div className="flex items-center gap-2">
                                        <Key className="w-4 h-4 text-primary" />
                                        <h4 className="text-sm font-medium">AWS Credentials</h4>
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="llm-aws-access-key">Access Key ID</Label>
                                        <Input
                                            id="llm-aws-access-key"
                                            type="password"
                                            placeholder="AKIA..."
                                            value={llmFormData.access_key_id}
                                            onChange={(e) => setLLMFormData({ ...llmFormData, access_key_id: e.target.value })}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="llm-aws-secret-key">Secret Access Key</Label>
                                        <Input
                                            id="llm-aws-secret-key"
                                            type="password"
                                            placeholder="Secret key"
                                            value={llmFormData.secret_access_key}
                                            onChange={(e) => setLLMFormData({ ...llmFormData, secret_access_key: e.target.value })}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="llm-aws-session-token">Session Token <span className="text-muted-foreground">(optional)</span></Label>
                                        <Input
                                            id="llm-aws-session-token"
                                            type="password"
                                            placeholder="For temporary credentials"
                                            value={llmFormData.session_token}
                                            onChange={(e) => setLLMFormData({ ...llmFormData, session_token: e.target.value })}
                                        />
                                    </div>
                                    <p className="text-xs text-muted-foreground">Credentials are stored in the database and used automatically when running workflows.</p>
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
                            <DialogTitle>Discover Models</DialogTitle>
                        </DialogHeader>
                        {discoveredModels.length === 0 ? (
                            <div className="flex flex-col gap-4 mt-4">
                                <div className="space-y-2">
                                    <Label htmlFor="discover-provider-select">Provider Configuration</Label>
                                    <select
                                        id="discover-provider-select"
                                        value={discoverProvider}
                                        onChange={(e) => setDiscoverProvider(e.target.value)}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                    >
                                        <option value="">-- Select a configured provider --</option>
                                        {discoverableProviders.map(p => (
                                            <option key={p.provider} value={p.provider}>
                                                {p.icon} {p.label}{p.region ? ` (${p.region})` : ''}{p.endpoint ? ` - ${p.endpoint}` : ''}
                                            </option>
                                        ))}
                                    </select>
                                    <p className="text-xs text-muted-foreground">
                                        Select a provider with credentials configured in Model Keys
                                    </p>
                                </div>

                                {discoverableProviders.length === 0 && (
                                    <div className="rounded-md border bg-yellow-50 border-yellow-200 p-3 text-sm text-yellow-800">
                                        No providers configured. Go to Model Keys section below to configure API credentials first.
                                    </div>
                                )}

                                {discoverProvider && (
                                    <div className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground">
                                        {(() => {
                                            const cfg = discoverableProviders.find(p => p.provider === discoverProvider);
                                            if (!cfg) return 'Provider not found.';
                                            const parts = [`Using ${cfg.label} credentials from Model Keys.`];
                                            if (cfg.region) parts.push(`Region: ${cfg.region}`);
                                            if (cfg.endpoint) parts.push(`Endpoint: ${cfg.endpoint}`);
                                            return parts.join(' ');
                                        })()}
                                    </div>
                                )}
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
                                    disabled={awsDiscovering || !discoverProvider}
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

                {/* Add/Edit Model Key Dialog */}
                <Dialog open={openModelKeyDialog} onOpenChange={(open) => !open && handleCloseModelKeyDialog()}>
                    <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
                        <DialogHeader>
                            <DialogTitle>
                                {editingModelKeyProvider ? `Edit Keys: ${editingModelKeyProvider}` : 'Configure Provider Keys'}
                            </DialogTitle>
                        </DialogHeader>
                        <div className="flex flex-col gap-4 mt-4">
                            {!editingModelKeyProvider && (
                                <div className="space-y-2">
                                    <Label htmlFor="mk-provider-select">Provider</Label>
                                    <select
                                        id="mk-provider-select"
                                        value={modelKeyFormData.provider}
                                        onChange={(e) => setModelKeyFormData(emptyModelKeyForm(e.target.value))}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                                    >
                                        {MODEL_KEY_PROVIDERS.map(p => (
                                            <option key={p.value} value={p.value}>{p.icon} {p.label}</option>
                                        ))}
                                    </select>
                                </div>
                            )}

                            {modelKeyFormData.provider && (() => {
                                const providerCfg = MODEL_KEY_PROVIDERS.find(p => p.value === modelKeyFormData.provider);
                                if (!providerCfg) return null;
                                return providerCfg.fields.map(field => {
                                    if (field === 'api_key') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-api-key">API Key</Label>
                                                <div className="relative">
                                                    <Input
                                                        id="mk-api-key"
                                                        value={modelKeyFormData.api_key}
                                                        onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, api_key: e.target.value })}
                                                        type={showModelKeyFields.api_key ? 'text' : 'password'}
                                                        placeholder={modelKeyFormData.provider === 'openai' ? 'sk-...' : modelKeyFormData.provider === 'anthropic' ? 'sk-ant-...' : 'Enter API key'}
                                                        className="pr-10"
                                                    />
                                                    <Button
                                                        type="button"
                                                        size="icon"
                                                        variant="ghost"
                                                        onClick={() => setShowModelKeyFields(prev => ({ ...prev, api_key: !prev.api_key }))}
                                                        className="absolute right-0 top-0 h-full"
                                                    >
                                                        {showModelKeyFields.api_key ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                                    </Button>
                                                </div>
                                                {editingModelKeyProvider && (
                                                    <p className="text-xs text-muted-foreground">Clear or change to update; leave as-is to keep existing</p>
                                                )}
                                            </div>
                                        );
                                    }
                                    if (field === 'secret_key') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-secret-key">Secret Key</Label>
                                                <div className="relative">
                                                    <Input
                                                        id="mk-secret-key"
                                                        value={modelKeyFormData.secret_key}
                                                        onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, secret_key: e.target.value })}
                                                        type={showModelKeyFields.secret_key ? 'text' : 'password'}
                                                        placeholder="Enter secret key"
                                                        className="pr-10"
                                                    />
                                                    <Button
                                                        type="button"
                                                        size="icon"
                                                        variant="ghost"
                                                        onClick={() => setShowModelKeyFields(prev => ({ ...prev, secret_key: !prev.secret_key }))}
                                                        className="absolute right-0 top-0 h-full"
                                                    >
                                                        {showModelKeyFields.secret_key ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                                    </Button>
                                                </div>
                                                {editingModelKeyProvider && (
                                                    <p className="text-xs text-muted-foreground">Clear or change to update; leave as-is to keep existing</p>
                                                )}
                                            </div>
                                        );
                                    }
                                    if (field === 'endpoint') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-endpoint">
                                                    {modelKeyFormData.provider === 'ollama' ? 'Base URL' : 'Endpoint URL'}
                                                </Label>
                                                <Input
                                                    id="mk-endpoint"
                                                    value={modelKeyFormData.endpoint}
                                                    onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, endpoint: e.target.value })}
                                                    placeholder={modelKeyFormData.provider === 'ollama' ? 'http://localhost:11434' : 'https://...'}
                                                />
                                            </div>
                                        );
                                    }
                                    if (field === 'region') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-region">AWS Region</Label>
                                                <Input
                                                    id="mk-region"
                                                    value={modelKeyFormData.region}
                                                    onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, region: e.target.value })}
                                                    placeholder="us-east-1"
                                                />
                                            </div>
                                        );
                                    }
                                    if (field === 'access_key_id') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-aws-access-key">Access Key ID</Label>
                                                <Input
                                                    id="mk-aws-access-key"
                                                    type="password"
                                                    value={modelKeyFormData.access_key_id}
                                                    onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, access_key_id: e.target.value })}
                                                    placeholder="AKIA..."
                                                />
                                            </div>
                                        );
                                    }
                                    if (field === 'secret_access_key') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-aws-secret-key">Secret Access Key</Label>
                                                <Input
                                                    id="mk-aws-secret-key"
                                                    type="password"
                                                    value={modelKeyFormData.secret_access_key}
                                                    onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, secret_access_key: e.target.value })}
                                                    placeholder="Secret key"
                                                />
                                            </div>
                                        );
                                    }
                                    if (field === 'session_token') {
                                        return (
                                            <div key={field} className="space-y-2">
                                                <Label htmlFor="mk-aws-session-token">Session Token <span className="text-muted-foreground">(optional)</span></Label>
                                                <Input
                                                    id="mk-aws-session-token"
                                                    type="password"
                                                    value={modelKeyFormData.session_token}
                                                    onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, session_token: e.target.value })}
                                                    placeholder="For temporary credentials"
                                                />
                                            </div>
                                        );
                                    }
                                    return null;
                                });
                            })()}

                            <div className="space-y-2">
                                <Label htmlFor="mk-description">Description <span className="text-muted-foreground">(optional)</span></Label>
                                <Input
                                    id="mk-description"
                                    value={modelKeyFormData.description}
                                    onChange={(e) => setModelKeyFormData({ ...modelKeyFormData, description: e.target.value })}
                                    placeholder="e.g., Production OpenAI key"
                                />
                            </div>

                            <div className="rounded-md border bg-muted/50 p-3 text-sm text-muted-foreground">
                                <div className="flex items-center gap-2 mb-1">
                                    <Shield className="w-4 h-4" />
                                    <span className="font-medium">Secure Storage</span>
                                </div>
                                Keys are stored in the database and masked when retrieved.
                            </div>
                        </div>
                        <DialogFooter className="mt-6">
                            <Button variant="outline" onClick={handleCloseModelKeyDialog}>Cancel</Button>
                            <Button onClick={handleSaveModelKey}>
                                {editingModelKeyProvider ? 'Update' : 'Save'}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

            </div>
        </div>
    );
};

export default Settings;
