import React, { useRef, useEffect } from 'react';
import {
    Box,
    Paper,
    Typography,
    List,
    ListItem,
    ListItemText,
    Chip
} from '@mui/material';

/**
 * Execution log component for displaying real-time logs
 */
const ExecutionLog = ({ events, maxHeight = 400 }) => {
    const logEndRef = useRef(null);

    // Auto-scroll to bottom when new events arrive
    useEffect(() => {
        logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [events]);

    const getEventColor = (type) => {
        switch (type) {
            case 'error':
                return 'error';
            case 'complete':
                return 'success';
            case 'progress':
                return 'info';
            case 'status':
                return 'primary';
            default:
                return 'default';
        }
    };

    const formatTimestamp = (timestamp) => {
        if (!timestamp) return '';
        const date = new Date(timestamp);
        return date.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        });
    };

    if (!events || events.length === 0) {
        return (
            <Paper sx={{ p: 2, bgcolor: '#f5f5f5' }}>
                <Typography variant="body2" color="text.secondary">
                    No events yet. Waiting for execution to start...
                </Typography>
            </Paper>
        );
    }

    return (
        <Paper
            sx={{
                maxHeight,
                overflow: 'auto',
                bgcolor: '#1e1e1e',
                color: '#d4d4d4',
                fontFamily: 'monospace',
                fontSize: '0.875rem'
            }}
        >
            <List dense>
                {events.map((event, index) => (
                    <ListItem
                        key={index}
                        sx={{
                            borderBottom: '1px solid #333',
                            '&:hover': { bgcolor: '#2d2d2d' }
                        }}
                    >
                        <ListItemText
                            primary={
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    <Typography
                                        component="span"
                                        sx={{ color: '#858585', minWidth: 80 }}
                                    >
                                        [{formatTimestamp(event.timestamp)}]
                                    </Typography>
                                    <Chip
                                        label={event.type}
                                        size="small"
                                        color={getEventColor(event.type)}
                                        sx={{ minWidth: 80 }}
                                    />
                                    <Typography component="span" sx={{ color: '#d4d4d4' }}>
                                        {event.message || event.step || JSON.stringify(event.data || {})}
                                    </Typography>
                                </Box>
                            }
                            secondary={
                                event.error && (
                                    <Typography
                                        component="span"
                                        sx={{ color: '#f48771', ml: 10 }}
                                    >
                                        Error: {event.error}
                                    </Typography>
                                )
                            }
                        />
                    </ListItem>
                ))}
                <div ref={logEndRef} />
            </List>
        </Paper>
    );
};

export default ExecutionLog;
