import React from 'react';
import { Badge } from '@/components/ui/badge';
import { CheckCircle, XCircle, Clock, Play, Pause } from 'lucide-react';
import { cn } from '@/lib/utils';
import { statusToVariant } from '@/lib/statusStyles';

/**
 * Status badge component for workflow execution status.
 * Color comes from the semantic `Badge` variant (see lib/statusStyles); this
 * component only owns the per-status label + icon.
 */
const STATUS_META = {
    running: { label: 'Running', icon: <Play className="w-3 h-3 mr-1" /> },
    in_progress: { label: 'Running', icon: <Play className="w-3 h-3 mr-1" /> },
    completed: { label: 'Completed', icon: <CheckCircle className="w-3 h-3 mr-1" /> },
    success: { label: 'Completed', icon: <CheckCircle className="w-3 h-3 mr-1" /> },
    failed: { label: 'Failed', icon: <XCircle className="w-3 h-3 mr-1" /> },
    error: { label: 'Failed', icon: <XCircle className="w-3 h-3 mr-1" /> },
    pending: { label: 'Pending', icon: <Clock className="w-3 h-3 mr-1" /> },
    queued: { label: 'Pending', icon: <Clock className="w-3 h-3 mr-1" /> },
    paused: { label: 'Paused', icon: <Pause className="w-3 h-3 mr-1" /> },
};

const StatusBadge = ({ status, size = 'medium' }) => {
    const key = status?.toLowerCase();
    const meta = STATUS_META[key] || { label: status || 'Idle', icon: null };
    const sizeClass = size === 'small' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1';

    return (
        <Badge
            variant={statusToVariant(status)}
            className={cn('inline-flex items-center font-medium', sizeClass)}
        >
            {meta.icon}
            {meta.label}
        </Badge>
    );
};

export default StatusBadge;
