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
    CircularProgress
} from '@mui/material';
import { 
    Add as AddIcon, 
    Edit as EditIcon, 
    Delete as DeleteIcon,
    PlayArrow as StartIcon,
    Stop as StopIcon
} from '@mui/icons-material';
import { getMCPServers, addMCPServer, updateMCPServer, deleteMCPServer } from '../services/mcpService';

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
    const [saveMessage, setSaveMessage] = useState('');
    
    // Docker state
    const [dockerAvailable, setDockerAvailable] = useState(false);
    const [dockerLoading, setDockerLoading] = useState(false);
    const [dockerContainers, setDockerContainers] = useState([]);
    const [dockerError, setDockerError] = useState(null);

    // Load MCP servers on mount
    useEffect(() => {
        loadServers();
        if (window.electronAPI) {
            checkDockerStatus();
        }
    }, []);

    const loadServers = async () => {
        const mcpServers = await getMCPServers();
        setServers(mcpServers);
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

    const handleOpenDialog = (serverName = null) => {
        if (serverName) {
            // Edit mode
            const server = servers[serverName];
            setEditingServer(serverName);
            setFormData({
                name: serverName,
                command: server.command || '',
                args: Array.isArray(server.args) ? server.args.join(', ') : '',
                type: server.type || 'stdio',
                icon: server.icon || '🔧',
                description: server.description || '',
                env: server.env ? JSON.stringify(server.env, null, 2) : ''
            });
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
        }
        setOpenDialog(true);
    };

    const handleCloseDialog = () => {
        setOpenDialog(false);
        setEditingServer(null);
    };

    const handleSave = async () => {
        try {
            // Parse args and env
            const args = formData.args
                .split(',')
                .map(arg => arg.trim())
                .filter(arg => arg.length > 0);

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
                ...(Object.keys(env).length > 0 && { env })
            };

            if (editingServer) {
                // Update existing server
                await updateMCPServer(formData.name, serverConfig);
                setSaveMessage(`Updated server: ${formData.name}`);
            } else {
                // Add new server
                await addMCPServer(formData.name, serverConfig);
                setSaveMessage(`Added new server: ${formData.name}`);
            }

            await loadServers();
            handleCloseDialog();

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
                        <Button
                            variant="contained"
                            startIcon={<AddIcon />}
                            onClick={() => handleOpenDialog()}
                        >
                            Add Server
                        </Button>
                    </Box>

                    <TableContainer>
                        <Table>
                            <TableHead>
                                <TableRow>
                                    <TableCell>Icon</TableCell>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Description</TableCell>
                                    <TableCell>Command</TableCell>
                                    <TableCell>Type</TableCell>
                                    <TableCell align="right">Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {Object.entries(servers).map(([name, config]) => (
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
                                            <Chip label={config.type} size="small" />
                                        </TableCell>
                                        <TableCell align="right">
                                            <IconButton
                                                size="small"
                                                onClick={() => handleOpenDialog(name)}
                                                color="primary"
                                            >
                                                <EditIcon fontSize="small" />
                                            </IconButton>
                                            <IconButton
                                                size="small"
                                                onClick={() => handleDelete(name)}
                                                color="error"
                                            >
                                                <DeleteIcon fontSize="small" />
                                            </IconButton>
                                        </TableCell>
                                    </TableRow>
                                ))}
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

                {/* Add/Edit Dialog */}
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
                                onChange={(e) => setFormData({ ...formData, args: e.target.value })}
                                fullWidth
                                multiline
                                rows={2}
                                helperText="Comma-separated arguments (e.g., '-y, @playwright/mcp@latest')"
                            />

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
            </Container>
        </Box>
    );
};

export default Settings;
