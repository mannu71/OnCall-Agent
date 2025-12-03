import React, { useState, useEffect } from 'react';
import {
    Box,
    Container,
    Typography,
    Paper,
    Button,
    TextField,
    IconButton,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Chip,
    Alert,
    CircularProgress,
    Select,
    MenuItem,
    FormControl,
    InputLabel,
    InputAdornment,
    Autocomplete
} from '@mui/material';
import { 
    Add as AddIcon, 
    Edit as EditIcon, 
    Delete as DeleteIcon,
    PlayArrow as StartIcon,
    Stop as StopIcon,
    Visibility as VisibilityIcon,
    VisibilityOff as VisibilityOffIcon,
    Key as KeyIcon,
    Check as CheckIcon,
    Refresh as RefreshIcon,
    CheckCircle as CheckCircleIcon,
    Error as ErrorIcon,
    HourglassEmpty as HourglassEmptyIcon
} from '@mui/icons-material';
import { getMCPServers, addMCPServer, updateMCPServer, deleteMCPServer, getMCPInputValues, updateMCPInputValue, invalidateCache } from '../services/mcpService';
import { getLLMs, addLLM, updateLLM, deleteLLM } from '../services/llmService';

// Helper to extract ${input:...} variables from args string
const extractInputVariables = (argsString) => {
    const matches = argsString.match(/\$\{input:([^}]+)\}/g) || [];
    return matches.map(m => m.match(/\$\{input:([^}]+)\}/)[1]);
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
    const [llms, setLLMs] = useState({});
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
        baseUrl: ''
    });
    const [showApiKey, setShowApiKey] = useState(false);
    const [existingApiKey, setExistingApiKey] = useState(null); // To show if key exists
    const [llmConnectionStatus, setLLMConnectionStatus] = useState({}); // { llmName: { status: 'untested' | 'testing' | 'connected' | 'error', message: '' } }
    
    // Docker state
    const [dockerAvailable, setDockerAvailable] = useState(false);
    const [dockerLoading, setDockerLoading] = useState(false);
    const [dockerContainers, setDockerContainers] = useState([]);
    const [dockerError, setDockerError] = useState(null);

    // MCP Input values state (for server variable configuration)
    const [mcpInputValues, setMCPInputValues] = useState({});

    // Load MCP servers and LLMs on mount
    useEffect(() => {
        loadServers();
        loadLLMConfigs();
        loadMCPInputValuesData();
        if (window.electronAPI) {
            checkDockerStatus();
        }
    }, []);

    const loadServers = async () => {
        invalidateCache(); // Ensure fresh data
        const mcpServers = await getMCPServers();
        setServers(mcpServers);
    };

    // Test MCP server connection
    const testServerConnection = async (serverName, serverConfig) => {
        if (!window.electronAPI?.testMCPServer) {
            setSaveMessage('Connection test is only available in the desktop app');
            setTimeout(() => setSaveMessage(''), 3000);
            return;
        }

        setConnectionStatus(prev => ({
            ...prev,
            [serverName]: { status: 'testing', message: 'Testing connection...' }
        }));

        try {
            const result = await window.electronAPI.testMCPServer(serverName, serverConfig);
            
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
        setLLMs(llmConfigs);
    };

    // Test LLM connection
    const testLLMConnection = async (llmName, llmConfig) => {
        if (!window.electronAPI?.testLLM) {
            setSaveMessage('LLM connection test is only available in the desktop app');
            setTimeout(() => setSaveMessage(''), 3000);
            return;
        }

        setLLMConnectionStatus(prev => ({
            ...prev,
            [llmName]: { status: 'testing', message: 'Testing connection...' }
        }));

        try {
            const result = await window.electronAPI.testLLM(llmName, llmConfig);
            
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

    const loadMCPInputValuesData = async () => {
        const values = await getMCPInputValues();
        setMCPInputValues(values);
    };

    const checkDockerStatus = async () => {
        try {
            const checkResult = await window.electronAPI.checkDocker();
            setDockerAvailable(checkResult.available);
            setDockerError(checkResult.error);
            
            if (checkResult.available) {
                const statusResult = await window.electronAPI.dockerStatus();
                if (statusResult.success) {
                    setDockerContainers(statusResult.containers);
                }
            }
        } catch (error) {
            console.error('Error checking Docker status:', error);
            setDockerError('Failed to check Docker status');
        }
    };

    const handleDockerStart = async () => {
        setDockerLoading(true);
        setDockerError(null);
        try {
            const result = await window.electronAPI.dockerStart();
            if (result.success) {
                setSaveMessage('Docker services started successfully');
                setTimeout(() => setSaveMessage(''), 3000);
                await checkDockerStatus();
            } else {
                setDockerError(result.error);
            }
        } catch (error) {
            console.error('Error starting Docker:', error);
            setDockerError('Failed to start Docker services');
        } finally {
            setDockerLoading(false);
        }
    };

    const handleDockerStop = async () => {
        setDockerLoading(true);
        setDockerError(null);
        try {
            const result = await window.electronAPI.dockerStop();
            if (result.success) {
                setSaveMessage('Docker services stopped successfully');
                setTimeout(() => setSaveMessage(''), 3000);
                await checkDockerStatus();
            } else {
                setDockerError(result.error);
            }
        } catch (error) {
            console.error('Error stopping Docker:', error);
            setDockerError('Failed to stop Docker services');
        } finally {
            setDockerLoading(false);
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

    // Update detected variables when args change
    const handleArgsChange = (value) => {
        setFormData({ ...formData, args: value });
        const vars = extractInputVariables(value);
        setDetectedInputVars(vars);
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
            
            // Parse args - support both newline-separated and comma-separated formats
            // But be careful: connection strings contain commas, so only split on ", " (comma+space)
            // at the beginning of args (like "-y, @package, url")
            let args = formData.args
                .split('\n')
                .map(arg => arg.trim())
                .filter(arg => arg.length > 0);
            
            // If we have a single arg that looks like comma-separated npx args, split it
            // Pattern: starts with -y, followed by package name, followed by connection string
            if (args.length === 1 && args[0].includes(', ')) {
                const singleArg = args[0];
                // Check if it looks like: "-y, @package, url" format
                if (singleArg.startsWith('-y, ') || singleArg.startsWith('-y,')) {
                    // Split carefully - only split the first two comma-space occurrences
                    // because the URL might contain commas
                    const parts = [];
                    let remaining = singleArg;
                    
                    // Extract -y
                    const firstComma = remaining.indexOf(',');
                    if (firstComma !== -1) {
                        parts.push(remaining.substring(0, firstComma).trim());
                        remaining = remaining.substring(firstComma + 1).trim();
                        
                        // Extract package name (up to next comma-space, but before any ://)
                        const urlStart = remaining.indexOf('://');
                        const secondComma = remaining.indexOf(', ');
                        
                        if (secondComma !== -1 && (urlStart === -1 || secondComma < urlStart)) {
                            parts.push(remaining.substring(0, secondComma).trim());
                            remaining = remaining.substring(secondComma + 1).trim();
                        }
                        
                        // Rest is the connection string
                        if (remaining) {
                            parts.push(remaining);
                        }
                    }
                    
                    if (parts.length >= 2) {
                        args = parts;
                    }
                }
            }

            let env = {};
            if (formData.env.trim()) {
                try {
                    env = JSON.parse(formData.env);
                } catch (e) {
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
                env: env  // Always include env (even if empty, to clear old values)
            };

            const serverNameToTest = formData.name;
            
            if (editingServer) {
                // Update existing server (handles rename and syncs to workflows)
                await updateMCPServer(editingServer, serverConfig, formData.name !== editingServer ? formData.name : null);
                if (editingServer !== formData.name) {
                    setSaveMessage(`Renamed server: ${editingServer} → ${formData.name}`);
                } else {
                    setSaveMessage(`Updated server: ${formData.name}`);
                }
            } else {
                // Add new server
                await addMCPServer(formData.name, serverConfig);
                setSaveMessage(`Added new server: ${formData.name}`);
            }

            await loadServers();
            await loadMCPInputValuesData(); // Refresh input values
            handleCloseDialog();

            // Auto-test connection for the saved server
            testServerConnection(serverNameToTest, serverConfig);

            // Clear message after 3 seconds
            setTimeout(() => setSaveMessage(''), 3000);
        } catch (error) {
            console.error('Error saving server:', error);
            alert('Failed to save server configuration');
        }
    };

    const handleDelete = async (serverName) => {
        if (window.confirm(`Are you sure you want to delete "${serverName}"?`)) {
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
                baseUrl: llm.baseUrl || ''
            });
            
            // Check if API key exists for this LLM
            if (window.electronAPI?.getApiKeyMasked) {
                const result = await window.electronAPI.getApiKeyMasked(llmName);
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
                baseUrl: ''
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
                ...(llmFormData.endpoint && { endpoint: llmFormData.endpoint }),
                ...(llmFormData.baseUrl && { baseUrl: llmFormData.baseUrl })
            };

            // First save the LLM config
            if (editingLLM) {
                await updateLLM(llmFormData.name, llmConfig);
            } else {
                await addLLM(llmFormData.name, llmConfig);
            }

            // Then save API key if provided (LLM must exist first)
            if (llmFormData.apiKey && window.electronAPI?.setApiKey) {
                const keyResult = await window.electronAPI.setApiKey(llmFormData.name, llmFormData.apiKey);
                if (!keyResult.success) {
                    console.error('Failed to save API key:', keyResult.error);
                }
            }

            setSaveMessage(editingLLM ? `Updated LLM: ${llmFormData.name}` : `Added new LLM: ${llmFormData.name}`);

            await loadLLMConfigs();
            handleCloseLLMDialog();
            setTimeout(() => setSaveMessage(''), 3000);
        } catch (error) {
            console.error('Error saving LLM:', error);
            alert('Failed to save LLM configuration');
        }
    };

    const handleDeleteLLM = async (llmName) => {
        if (window.confirm(`Are you sure you want to delete "${llmName}"?`)) {
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
        <Box sx={{ p: 2, minHeight: '100vh', backgroundColor: 'background.default' }}>
            <Container maxWidth="lg" sx={{ px: 0 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
                    <Typography variant="h4" gutterBottom sx={{ fontWeight: 600, mb: 0 }}>
                        Settings
                    </Typography>
                </Box>

                {saveMessage && (
                    <Alert severity="success" sx={{ mb: 2 }}>
                        {saveMessage}
                    </Alert>
                )}

                {/* Docker Management Section */}
                {window.electronAPI && (
                    <Paper sx={{ p: 3, mb: 3 }}>
                        <Typography variant="h6" sx={{ fontWeight: 500, mb: 2 }}>
                            Agent Service (Docker)
                        </Typography>

                        {dockerError && (
                            <Alert severity="error" sx={{ mb: 2 }}>
                                {dockerError}
                            </Alert>
                        )}

                        <Box sx={{ mb: 2 }}>
                            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                                Start the OnCall Agent service in Docker to run scheduled workflows automatically.
                                The agent includes the scheduler and worker processes.
                            </Typography>

                            <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
                                <Button
                                    variant="contained"
                                    color="success"
                                    startIcon={dockerLoading ? <CircularProgress size={20} color="inherit" /> : <StartIcon />}
                                    onClick={handleDockerStart}
                                    disabled={!dockerAvailable || dockerLoading}
                                >
                                    Start Services
                                </Button>
                                <Button
                                    variant="contained"
                                    color="error"
                                    startIcon={dockerLoading ? <CircularProgress size={20} color="inherit" /> : <StopIcon />}
                                    onClick={handleDockerStop}
                                    disabled={!dockerAvailable || dockerLoading}
                                >
                                    Stop Services
                                </Button>
                                <Button
                                    variant="outlined"
                                    onClick={checkDockerStatus}
                                    disabled={dockerLoading}
                                >
                                    Refresh Status
                                </Button>
                            </Box>

                            {!dockerAvailable && (
                                <Alert severity="warning" sx={{ mb: 2 }}>
                                    Docker Desktop is not running. Please install and start Docker Desktop to manage dependencies.
                                </Alert>
                            )}

                            {dockerContainers.length > 0 && (
                                <Box>
                                    <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                                        Running Containers:
                                    </Typography>
                                    <TableContainer>
                                        <Table size="small">
                                            <TableHead>
                                                <TableRow>
                                                    <TableCell>Name</TableCell>
                                                    <TableCell>Components</TableCell>
                                                    <TableCell>Status</TableCell>
                                                </TableRow>
                                            </TableHead>
                                            <TableBody>
                                                {dockerContainers.map((container, index) => (
                                                    <TableRow key={index}>
                                                        <TableCell>
                                                            <code style={{ fontSize: '0.85em' }}>
                                                                {container.Name || container.name || 'N/A'}
                                                            </code>
                                                        </TableCell>
                                                        <TableCell>
                                                            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                                                                <Chip 
                                                                    label="Scheduler" 
                                                                    size="small" 
                                                                    color="primary"
                                                                    variant="outlined"
                                                                    sx={{ fontSize: '0.75em' }}
                                                                />
                                                                <Chip 
                                                                    label="Worker" 
                                                                    size="small" 
                                                                    color="secondary"
                                                                    variant="outlined"
                                                                    sx={{ fontSize: '0.75em' }}
                                                                />
                                                            </Box>
                                                        </TableCell>
                                                        <TableCell>
                                                            <Chip 
                                                                label={container.State || container.state || 'unknown'} 
                                                                size="small"
                                                                color={
                                                                    (container.State || container.state) === 'running' 
                                                                        ? 'success' 
                                                                        : 'default'
                                                                }
                                                            />
                                                        </TableCell>
                                                    </TableRow>
                                                ))}
                                            </TableBody>
                                        </Table>
                                    </TableContainer>
                                </Box>
                            )}
                        </Box>
                    </Paper>
                )}

                <Paper sx={{ p: 3, mb: 3 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                        <Typography variant="h6" sx={{ fontWeight: 500 }}>
                            MCP Servers
                        </Typography>
                        <Box sx={{ display: 'flex', gap: 1 }}>
                            <Button
                                variant="outlined"
                                startIcon={<RefreshIcon />}
                                onClick={testAllConnections}
                                disabled={Object.keys(servers).length === 0}
                            >
                                Test All
                            </Button>
                            <Button
                                variant="contained"
                                startIcon={<AddIcon />}
                                onClick={() => handleOpenDialog()}
                            >
                                Add Server
                            </Button>
                        </Box>
                    </Box>

                    <TableContainer>
                        <Table>
                            <TableHead>
                                <TableRow>
                                    <TableCell>Icon</TableCell>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Description</TableCell>
                                    <TableCell>Command</TableCell>
                                    <TableCell>Status</TableCell>
                                    <TableCell align="right">Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {Object.entries(servers).map(([name, config]) => {
                                    const status = connectionStatus[name];
                                    return (
                                    <TableRow key={name}>
                                        <TableCell>{config.icon || '🔧'}</TableCell>
                                        <TableCell>
                                            <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                                {name}
                                            </Typography>
                                        </TableCell>
                                        <TableCell>{config.description || '-'}</TableCell>
                                        <TableCell>
                                            <code style={{ fontSize: '0.85em' }}>{config.command}</code>
                                        </TableCell>
                                        <TableCell>
                                            {status?.status === 'testing' && (
                                                <Chip
                                                    icon={<HourglassEmptyIcon fontSize="small" />}
                                                    label="Testing..."
                                                    size="small"
                                                    color="info"
                                                />
                                            )}
                                            {status?.status === 'connected' && (
                                                <Chip
                                                    icon={<CheckCircleIcon fontSize="small" />}
                                                    label="Connected"
                                                    size="small"
                                                    color="success"
                                                />
                                            )}
                                            {status?.status === 'error' && (
                                                <Chip
                                                    icon={<ErrorIcon fontSize="small" />}
                                                    label={status.message?.substring(0, 20) || 'Error'}
                                                    size="small"
                                                    color="error"
                                                    title={status.message}
                                                />
                                            )}
                                            {!status && (
                                                <Chip
                                                    label="Not tested"
                                                    size="small"
                                                    variant="outlined"
                                                />
                                            )}
                                        </TableCell>
                                        <TableCell align="right">
                                            <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 0.5 }}>
                                                <IconButton
                                                    size="small"
                                                    onClick={() => testServerConnection(name, config)}
                                                    color="info"
                                                    title="Test Connection"
                                                    disabled={status?.status === 'testing'}
                                                >
                                                    <RefreshIcon fontSize="small" />
                                                </IconButton>
                                                <IconButton
                                                    size="small"
                                                    onClick={() => handleOpenDialog(name)}
                                                    color="primary"
                                                    title="Edit"
                                                >
                                                    <EditIcon fontSize="small" />
                                                </IconButton>
                                                <IconButton
                                                    size="small"
                                                    onClick={() => handleDelete(name)}
                                                    color="error"
                                                    title="Delete"
                                                >
                                                    <DeleteIcon fontSize="small" />
                                                </IconButton>
                                            </Box>
                                        </TableCell>
                                    </TableRow>
                                    );
                                })}
                                {Object.keys(servers).length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={6} align="center">
                                            <Typography variant="body2" color="text.secondary">
                                                No MCP servers configured. Click "Add Server" to get started.
                                            </Typography>
                                        </TableCell>
                                    </TableRow>
                                )}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </Paper>

                {/* LLM Configuration Section */}
                <Paper sx={{ p: 3, mb: 3 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                        <Typography variant="h6" sx={{ fontWeight: 500 }}>
                            Language Models (LLMs)
                        </Typography>
                        <Button
                            variant="contained"
                            startIcon={<AddIcon />}
                            onClick={() => handleOpenLLMDialog()}
                        >
                            Add LLM
                        </Button>
                    </Box>

                    <TableContainer>
                        <Table>
                            <TableHead>
                                <TableRow>
                                    <TableCell>Icon</TableCell>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Provider</TableCell>
                                    <TableCell>Model</TableCell>
                                    <TableCell>Status</TableCell>
                                    <TableCell align="right">Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {Object.entries(llms).map(([name, config]) => {
                                    const status = llmConnectionStatus[name];
                                    return (
                                    <TableRow key={name}>
                                        <TableCell>{config.icon || '🧠'}</TableCell>
                                        <TableCell>
                                            <Typography variant="body2" sx={{ fontWeight: 500 }}>
                                                {name}
                                            </Typography>
                                        </TableCell>
                                        <TableCell>
                                            <Chip label={config.provider} size="small" color="primary" variant="outlined" />
                                        </TableCell>
                                        <TableCell>
                                            <code style={{ fontSize: '0.85em' }}>{config.model}</code>
                                        </TableCell>
                                        <TableCell>
                                            {status?.status === 'testing' && (
                                                <Chip icon={<HourglassEmptyIcon />} label="Testing..." size="small" color="default" />
                                            )}
                                            {status?.status === 'connected' && (
                                                <Chip icon={<CheckCircleIcon />} label={status.message} size="small" color="success" />
                                            )}
                                            {status?.status === 'error' && (
                                                <Chip icon={<ErrorIcon />} label={status.message} size="small" color="error" title={status.message} />
                                            )}
                                            {!status && (
                                                <Chip label="Not tested" size="small" color="default" variant="outlined" />
                                            )}
                                        </TableCell>
                                        <TableCell align="right">
                                            <IconButton
                                                size="small"
                                                onClick={() => testLLMConnection(name, config)}
                                                color="default"
                                                title="Test Connection"
                                            >
                                                <RefreshIcon fontSize="small" />
                                            </IconButton>
                                            <IconButton
                                                size="small"
                                                onClick={() => handleOpenLLMDialog(name)}
                                                color="primary"
                                            >
                                                <EditIcon fontSize="small" />
                                            </IconButton>
                                            <IconButton
                                                size="small"
                                                onClick={() => handleDeleteLLM(name)}
                                                color="error"
                                            >
                                                <DeleteIcon fontSize="small" />
                                            </IconButton>
                                        </TableCell>
                                    </TableRow>
                                    );
                                })}
                                {Object.keys(llms).length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={6} align="center">
                                            <Typography variant="body2" color="text.secondary">
                                                No LLMs configured. Click "Add LLM" to get started.
                                            </Typography>
                                        </TableCell>
                                    </TableRow>
                                )}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </Paper>

                {/* Add/Edit MCP Server Dialog */}
                <Dialog open={openDialog} onClose={handleCloseDialog} maxWidth="md" fullWidth>
                    <DialogTitle>
                        {editingServer ? `Edit Server: ${editingServer}` : 'Add New MCP Server'}
                    </DialogTitle>
                    <DialogContent>
                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2 }}>
                            <TextField
                                label="Server Name"
                                value={formData.name}
                                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                disabled={!!editingServer}
                                fullWidth
                                required
                                helperText="Unique identifier for the server (e.g., 'playwright', 'postgres-dev')"
                            />

                            <TextField
                                label="Icon"
                                value={formData.icon}
                                onChange={(e) => setFormData({ ...formData, icon: e.target.value })}
                                fullWidth
                                helperText="Emoji icon for the server (e.g., 🎭, 🗄️, 📊)"
                            />

                            <TextField
                                label="Description"
                                value={formData.description}
                                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                                fullWidth
                                helperText="Brief description of what this server does"
                            />

                            <TextField
                                label="Command"
                                value={formData.command}
                                onChange={(e) => setFormData({ ...formData, command: e.target.value })}
                                fullWidth
                                required
                                helperText="Command to execute (e.g., 'npx', 'uvx', 'python')"
                            />

                            <TextField
                                label="Arguments"
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
                                fullWidth
                                multiline
                                rows={3}
                                helperText="One argument per line. Use ${input:var_name} for configurable values"
                            />

                            {/* Dynamic input variable fields */}
                            {detectedInputVars.length > 0 && (
                                <Box sx={{ 
                                    p: 2, 
                                    bgcolor: 'action.hover', 
                                    borderRadius: 1,
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 2
                                }}>
                                    <Typography variant="subtitle2" color="text.secondary">
                                        Configure Input Variables
                                    </Typography>
                                    {detectedInputVars.map((varName) => (
                                        <TextField
                                            key={varName}
                                            label={varName}
                                            value={inputVarValues[varName] || ''}
                                            onChange={(e) => setInputVarValues({ 
                                                ...inputVarValues, 
                                                [varName]: e.target.value 
                                            })}
                                            fullWidth
                                            size="small"
                                            helperText={`Value for \${input:${varName}}`}
                                        />
                                    ))}
                                </Box>
                            )}

                            <TextField
                                label="Type"
                                value={formData.type}
                                onChange={(e) => setFormData({ ...formData, type: e.target.value })}
                                fullWidth
                                helperText="Connection type (usually 'stdio')"
                            />

                            <TextField
                                label="Environment Variables (JSON)"
                                value={formData.env}
                                onChange={(e) => setFormData({ ...formData, env: e.target.value })}
                                fullWidth
                                multiline
                                rows={4}
                                helperText='Optional JSON object for environment variables (e.g., {"AWS_PROFILE": "default"})'
                            />
                        </Box>
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={handleCloseDialog}>Cancel</Button>
                        <Button onClick={handleSave} variant="contained" disabled={!formData.name || !formData.command}>
                            {editingServer ? 'Update' : 'Add'}
                        </Button>
                    </DialogActions>
                </Dialog>

                {/* Add/Edit LLM Dialog */}
                <Dialog open={openLLMDialog} onClose={handleCloseLLMDialog} maxWidth="sm" fullWidth>
                    <DialogTitle>
                        {editingLLM ? `Edit LLM: ${editingLLM}` : 'Add New LLM'}
                    </DialogTitle>
                    <DialogContent>
                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2 }}>
                            <TextField
                                label="Display Name"
                                value={llmFormData.name}
                                onChange={(e) => setLLMFormData({ ...llmFormData, name: e.target.value })}
                                disabled={!!editingLLM}
                                fullWidth
                                required
                                helperText="A friendly name for this LLM configuration"
                            />

                            <TextField
                                label="Icon"
                                value={llmFormData.icon}
                                onChange={(e) => setLLMFormData({ ...llmFormData, icon: e.target.value })}
                                fullWidth
                                helperText="Emoji icon (e.g., 🧠, ⚡, 🤖)"
                            />

                            <FormControl fullWidth>
                                <InputLabel>Provider</InputLabel>
                                <Select
                                    value={llmFormData.provider}
                                    label="Provider"
                                    onChange={(e) => setLLMFormData({ 
                                        ...llmFormData, 
                                        provider: e.target.value,
                                        model: '' // Reset model when provider changes
                                    })}
                                >
                                    {PROVIDERS.map(provider => (
                                        <MenuItem key={provider} value={provider}>{provider}</MenuItem>
                                    ))}
                                </Select>
                            </FormControl>

                            {llmFormData.provider === 'Custom' ? (
                                <TextField
                                    label="Model Name"
                                    value={llmFormData.model}
                                    onChange={(e) => setLLMFormData({ ...llmFormData, model: e.target.value })}
                                    fullWidth
                                    placeholder="Enter custom model name"
                                    helperText="Enter the model identifier"
                                />
                            ) : (
                                <Autocomplete
                                    freeSolo
                                    options={getModelOptions(llmFormData.provider)}
                                    value={llmFormData.model}
                                    onChange={(e, newValue) => setLLMFormData({ ...llmFormData, model: newValue || '' })}
                                    onInputChange={(e, newInputValue) => setLLMFormData({ ...llmFormData, model: newInputValue })}
                                    renderInput={(params) => (
                                        <TextField
                                            {...params}
                                            label="Model"
                                            placeholder={llmFormData.provider === 'Ollama' ? 'e.g., llama3.2:latest' : 'Select or type model name'}
                                            helperText="Select from suggestions or type a custom model name"
                                        />
                                    )}
                                />
                            )}

                            {llmFormData.provider === 'Azure OpenAI' && (
                                <TextField
                                    label="Endpoint URL"
                                    value={llmFormData.endpoint || ''}
                                    onChange={(e) => setLLMFormData({ ...llmFormData, endpoint: e.target.value })}
                                    fullWidth
                                    placeholder="https://your-resource.openai.azure.com"
                                    helperText="Your Azure OpenAI endpoint URL"
                                />
                            )}

                            {llmFormData.provider === 'Ollama' && (
                                <TextField
                                    label="Base URL"
                                    value={llmFormData.baseUrl || 'http://localhost:11434'}
                                    onChange={(e) => setLLMFormData({ ...llmFormData, baseUrl: e.target.value })}
                                    fullWidth
                                    helperText="Ollama server URL (default: http://localhost:11434)"
                                />
                            )}

                            {/* API Key Section */}
                            {llmFormData.provider !== 'Ollama' && (
                                <Box sx={{ 
                                    p: 2, 
                                    bgcolor: 'action.hover', 
                                    borderRadius: 1,
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 2
                                }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                        <KeyIcon fontSize="small" color="primary" />
                                        <Typography variant="subtitle2">
                                            API Key
                                        </Typography>
                                    </Box>
                                    
                                    {existingApiKey ? (
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                            <Chip 
                                                icon={<CheckIcon />}
                                                label={`Key configured: ${existingApiKey}`}
                                                size="small" 
                                                color="success" 
                                                variant="outlined" 
                                            />
                                            <Button 
                                                size="small" 
                                                onClick={() => setExistingApiKey(null)}
                                            >
                                                Update Key
                                            </Button>
                                        </Box>
                                    ) : (
                                        <TextField
                                            label="API Key"
                                            value={llmFormData.apiKey}
                                            onChange={(e) => setLLMFormData({ ...llmFormData, apiKey: e.target.value })}
                                            fullWidth
                                            size="small"
                                            type={showApiKey ? 'text' : 'password'}
                                            placeholder="sk-..."
                                            helperText="Your API key will be securely stored locally"
                                            InputProps={{
                                                endAdornment: (
                                                    <InputAdornment position="end">
                                                        <IconButton
                                                            onClick={() => setShowApiKey(!showApiKey)}
                                                            edge="end"
                                                            size="small"
                                                        >
                                                            {showApiKey ? <VisibilityOffIcon /> : <VisibilityIcon />}
                                                        </IconButton>
                                                    </InputAdornment>
                                                ),
                                            }}
                                        />
                                    )}
                                </Box>
                            )}
                        </Box>
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={handleCloseLLMDialog}>Cancel</Button>
                        <Button 
                            onClick={handleSaveLLM} 
                            variant="contained" 
                            disabled={!llmFormData.name || !llmFormData.model}
                        >
                            {editingLLM ? 'Update' : 'Add'}
                        </Button>
                    </DialogActions>
                </Dialog>
            </Container>
        </Box>
    );
};

export default Settings;
