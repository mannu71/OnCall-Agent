import React, { useState, useEffect } from 'react';
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
    Check,
    Info
} from 'lucide-react';
import { getMCPServers, addMCPServer, updateMCPServer, deleteMCPServer, getMCPInputValues, updateMCPInputValue, invalidateCache } from '../services/mcpService';
import { getLLMs, addLLM, updateLLM, deleteLLM } from '../services/llmService';
import agentApiClient from '../services/agentApiClient';

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
        apiKey: '', // Direct API key input
        endpoint: '',
        baseUrl: '',
        temperature: 0
    });

    // OpenAI reasoning models that don't support temperature parameter
    const REASONING_MODELS = ['o1', 'o1-mini', 'o1-preview', 'o3', 'o3-mini', 'o4-mini'];
    const isReasoningModel = (model) => {
        if (!model) return false;
        const modelLower = model.toLowerCase();
        return REASONING_MODELS.some(rm => modelLower.startsWith(rm));
    };
    const [showApiKey, setShowApiKey] = useState(false);
    const [existingApiKey, setExistingApiKey] = useState(null); // To show if key exists
    const [llmConnectionStatus, setLLMConnectionStatus] = useState({}); // { llmName: { status: 'untested' | 'testing' | 'connected' | 'error', message: '' } }

    // Certificates state
    const [certificates, setCertificates] = useState([]);
    const [certUploadLoading, setCertUploadLoading] = useState(false);

    // API Health state
    const [apiHealth, setApiHealth] = useState(null);
    const [apiHealthLoading, setApiHealthLoading] = useState(false);

    // Load MCP servers and LLMs on mount
    useEffect(() => {
        loadServers();
        loadLLMConfigs();
        loadCertificates();
        checkApiHealth();
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
        if (!globalThis.electronAPI?.testLLM) {
            setSaveMessage('LLM connection test is only available in the desktop app');
            setTimeout(() => setSaveMessage(''), 3000);
            return;
        }

        setLLMConnectionStatus(prev => ({
            ...prev,
            [llmName]: { status: 'testing', message: 'Testing connection...' }
        }));

        try {
            const result = await globalThis.electronAPI.testLLM(llmName, llmConfig);

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
        setShowApiKey(false);
        setExistingApiKey(null);

        if (llmName) {
            const llm = llms[llmName];
            setEditingLLM(llmName);
            setLLMFormData({
                name: llmName,
                provider: llm.provider || 'OpenAI',
                model: llm.model || '',
                icon: llm.icon || '🧠',
                apiKey: '', // Don't load actual key, just check if exists
                endpoint: llm.endpoint || '',
                baseUrl: llm.baseUrl || '',
                temperature: llm.temperature ?? 0
            });

            // Check if API key exists for this LLM
            if (globalThis.electronAPI?.getApiKeyMasked) {
                const result = await globalThis.electronAPI.getApiKeyMasked(llmName);
                if (result.success && result.masked) {
                    setExistingApiKey(result.masked);
                }
            }
        } else {
            setEditingLLM(null);
            setLLMFormData({
                name: '',
                provider: 'OpenAI',
                model: '',
                icon: '🧠',
                apiKey: '',
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
        setShowApiKey(false);
        setExistingApiKey(null);
    };

    const handleSaveLLM = async () => {
        try {
            const llmConfig = {
                provider: llmFormData.provider,
                model: llmFormData.model,
                icon: llmFormData.icon,
                // Only save temperature for non-reasoning models
                ...(!isReasoningModel(llmFormData.model) && { temperature: llmFormData.temperature }),
                ...(llmFormData.endpoint && { endpoint: llmFormData.endpoint }),
                ...(llmFormData.baseUrl && { baseUrl: llmFormData.baseUrl })
            };

            // Use model name as the identifier
            const llmName = llmFormData.model;

            // First save the LLM config
            if (editingLLM) {
                await updateLLM(editingLLM, llmConfig, llmName);
            } else {
                await addLLM(llmName, llmConfig);
            }

            // Then save API key if provided (LLM must exist first)
            if (llmFormData.apiKey && globalThis.electronAPI?.setApiKey) {
                const keyResult = await globalThis.electronAPI.setApiKey(llmName, llmFormData.apiKey);
                if (!keyResult.success) {
                    console.error('Failed to save API key:', keyResult.error);
                }
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
                                                {' • '}
                                                Active Workflows: {apiHealth.active_workflows || 0}
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
                            <Button onClick={() => handleOpenLLMDialog()}>
                                <Plus className="w-4 h-4 mr-2" />
                                Add LLM
                            </Button>
                        </div>

                        <div className="border rounded-lg">
                            <Table>
                                <TableHeader>
                                    <TableRow>
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
                                            <TableCell colSpan={6} className="text-center py-6 text-muted-foreground">
                                                No LLMs configured. Click "Add LLM" to get started.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
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

                            {/* API Key Section */}
                            {llmFormData.provider !== 'Ollama' && (
                                <div className="p-4 bg-muted rounded-lg flex flex-col gap-3">
                                    <div className="flex items-center gap-2">
                                        <Key className="w-4 h-4 text-primary" />
                                        <h4 className="text-sm font-medium">API Key</h4>
                                    </div>

                                    {existingApiKey ? (
                                        <div className="flex items-center gap-2">
                                            <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200">
                                                <Check className="w-3 h-3 mr-1" />
                                                Key configured: {existingApiKey}
                                            </Badge>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                onClick={() => setExistingApiKey(null)}
                                            >
                                                Update Key
                                            </Button>
                                        </div>
                                    ) : (
                                        <div className="space-y-2">
                                            <div className="relative">
                                                <Input
                                                    id="llm-apiKey"
                                                    value={llmFormData.apiKey}
                                                    onChange={(e) => setLLMFormData({ ...llmFormData, apiKey: e.target.value })}
                                                    type={showApiKey ? 'text' : 'password'}
                                                    placeholder="sk-..."
                                                    className="pr-10"
                                                />
                                                <Button
                                                    type="button"
                                                    size="icon"
                                                    variant="ghost"
                                                    onClick={() => setShowApiKey(!showApiKey)}
                                                    className="absolute right-0 top-0 h-full"
                                                >
                                                    {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                                </Button>
                                            </div>
                                            <p className="text-sm text-muted-foreground">
                                                Your API key will be securely stored locally
                                            </p>
                                        </div>
                                    )}
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
            </div>
        </div>
    );
};

export default Settings;
