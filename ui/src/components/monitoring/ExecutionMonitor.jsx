import React, { useState } from 'react';
import {
    Box,
    Paper,
    Typography,
    IconButton,
    Collapse,
    Alert,
    CircularProgress,
    Divider
} from '@mui/material';
import {
    ExpandMore as ExpandMoreIcon,
    ExpandLess as ExpandLessIcon,
    Refresh as RefreshIcon
} from '@mui/icons-material';
import { useWorkflowStream } from '../../hooks/useWorkflowStream';
import StatusBadge from './StatusBadge';
import ExecutionLog from './ExecutionLog';

/**
 * Execution monitor component for real-time workflow monitoring
 */
const ExecutionMonitor = ({ workflowName, autoStart = false, onClose }) => {
    const [expanded, setExpanded] = useState(true);
    const { status, events, error, isConnected, clearEvents } = useWorkflowStream(
        workflowName,
        autoStart
    );

    const handleToggle = () => {
        setExpanded(!expanded);
    };

    const handleRefresh = () => {
        clearEvents();
    };

    const getConnectionStatus = () => {
        if (isConnected) {
            return { text: 'Connected', color: 'success' };
        }
        if (error) {
            return { text: 'Error', color: 'error' };
        }
        return { text: 'Disconnected', color: 'default' };
    };

    const connectionStatus = getConnectionStatus();

    return (
        <Paper elevation={3} sx={{ mb: 2 }}>
            <Box
                sx={{
                    p: 2,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    cursor: 'pointer',
                    '&:hover': { bgcolor: 'action.hover' }
                }}
                onClick={handleToggle}
            >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flex: 1 }}>
                    <Typography variant="h6" sx={{ fontWeight: 600 }}>
                        {workflowName}
                    </Typography>
                    <StatusBadge status={status} />
                    {isConnected && status === 'running' && (
                        <CircularProgress size={20} />
                    )}
                </Box>

                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                        {connectionStatus.text}
                    </Typography>
                    <IconButton
                        size="small"
                        onClick={(e) => {
                            e.stopPropagation();
                            handleRefresh();
                        }}
                    >
                        <RefreshIcon />
                    </IconButton>
                    <IconButton size="small">
                        {expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                    </IconButton>
                </Box>
            </Box>

            <Collapse in={expanded}>
                <Divider />
                <Box sx={{ p: 2 }}>
                    {error && (
                        <Alert severity="error" sx={{ mb: 2 }}>
                            {error}
                        </Alert>
                    )}

                    {!isConnected && !error && (
                        <Alert severity="info" sx={{ mb: 2 }}>
                            Waiting for connection...
                        </Alert>
                    )}

                    <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 600 }}>
                        Execution Log
                    </Typography>
                    <ExecutionLog events={events} maxHeight={300} />

                    {events.length > 0 && (
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
                            {events.length} event{events.length !== 1 ? 's' : ''} received
                        </Typography>
                    )}
                </Box>
            </Collapse>
        </Paper>
    );
};

export default ExecutionMonitor;
