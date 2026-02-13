import React from 'react';
import { Chip } from '@mui/material';
import {
    CheckCircle as CheckCircleIcon,
    Error as ErrorIcon,
    HourglassEmpty as HourglassIcon,
    PlayArrow as PlayArrowIcon,
    Pause as PauseIcon
} from '@mui/icons-material';

/**
 * Status badge component for workflow execution status
 */
const StatusBadge = ({ status, size = 'medium' }) => {
    const getStatusConfig = (status) => {
        switch (status?.toLowerCase()) {
            case 'running':
            case 'in_progress':
                return {
                    label: 'Running',
                    color: 'primary',
                    icon: <PlayArrowIcon fontSize="small" />
                };
            case 'completed':
            case 'success':
                return {
                    label: 'Completed',
                    color: 'success',
                    icon: <CheckCircleIcon fontSize="small" />
                };
            case 'failed':
            case 'error':
                return {
                    label: 'Failed',
                    color: 'error',
                    icon: <ErrorIcon fontSize="small" />
                };
            case 'pending':
            case 'queued':
                return {
                    label: 'Pending',
                    color: 'warning',
                    icon: <HourglassIcon fontSize="small" />
                };
            case 'paused':
                return {
                    label: 'Paused',
                    color: 'default',
                    icon: <PauseIcon fontSize="small" />
                };
            default:
                return {
                    label: status || 'Idle',
                    color: 'default',
                    icon: null
                };
        }
    };

    const config = getStatusConfig(status);

    return (
        <Chip
            label={config.label}
            color={config.color}
            size={size}
            icon={config.icon}
            sx={{ fontWeight: 500 }}
        />
    );
};

export default StatusBadge;
