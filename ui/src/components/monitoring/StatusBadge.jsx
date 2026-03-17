import React from 'react';
import { Badge } from '@/components/ui/badge';
import { CheckCircle, XCircle, Clock, Play, Pause } from 'lucide-react';
import { cn } from '@/lib/utils';

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
                    className: 'bg-blue-100 text-blue-700 hover:bg-blue-100 border-blue-200',
                    icon: <Play className="w-3 h-3 mr-1" />
                };
            case 'completed':
            case 'success':
                return {
                    label: 'Completed',
                    className: 'bg-green-100 text-green-700 hover:bg-green-100 border-green-200',
                    icon: <CheckCircle className="w-3 h-3 mr-1" />
                };
            case 'failed':
            case 'error':
                return {
                    label: 'Failed',
                    className: 'bg-red-100 text-red-700 hover:bg-red-100 border-red-200',
                    icon: <XCircle className="w-3 h-3 mr-1" />
                };
            case 'pending':
            case 'queued':
                return {
                    label: 'Pending',
                    className: 'bg-yellow-100 text-yellow-700 hover:bg-yellow-100 border-yellow-200',
                    icon: <Clock className="w-3 h-3 mr-1" />
                };
            case 'paused':
                return {
                    label: 'Paused',
                    className: 'bg-gray-100 text-gray-700 hover:bg-gray-100 border-gray-200',
                    icon: <Pause className="w-3 h-3 mr-1" />
                };
            default:
                return {
                    label: status || 'Idle',
                    className: 'bg-gray-100 text-gray-700 hover:bg-gray-100 border-gray-200',
                    icon: null
                };
        }
    };

    const config = getStatusConfig(status);
    const sizeClass = size === 'small' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1';

    return (
        <Badge 
            variant="outline"
            className={cn(
                'inline-flex items-center font-medium',
                config.className,
                sizeClass
            )}
        >
            {config.icon}
            {config.label}
        </Badge>
    );
};

export default StatusBadge;
