import React, { useRef, useEffect } from 'react';
import PropTypes from 'prop-types';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

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
                return 'bg-red-100 text-red-700 hover:bg-red-100 border-red-200';
            case 'complete':
                return 'bg-green-100 text-green-700 hover:bg-green-100 border-green-200';
            case 'progress':
                return 'bg-blue-100 text-blue-700 hover:bg-blue-100 border-blue-200';
            case 'status':
                return 'bg-purple-100 text-purple-700 hover:bg-purple-100 border-purple-200';
            default:
                return 'bg-gray-100 text-gray-700 hover:bg-gray-100 border-gray-200';
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
            <Card className="p-4 bg-gray-50">
                <p className="text-sm text-muted-foreground">
                    No events yet. Waiting for execution to start...
                </p>
            </Card>
        );
    }

    return (
        <div
            className="overflow-auto bg-[#1e1e1e] text-[#d4d4d4] font-mono text-sm rounded-lg"
            style={{ maxHeight: `${maxHeight}px` }}
        >
            <div className="divide-y divide-[#333]">
                {events.map((event, index) => (
                    <div
                        key={index}
                        className="px-3 py-2 hover:bg-[#2d2d2d] transition-colors"
                    >
                        <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-[#858585] min-w-[80px]">
                                [{formatTimestamp(event.timestamp)}]
                            </span>
                            <Badge
                                variant="outline"
                                className={`min-w-[80px] justify-center ${getEventColor(event.type)}`}
                            >
                                {event.type}
                            </Badge>
                            <span className="text-[#d4d4d4]">
                                {event.message || event.step || JSON.stringify(event.data || {})}
                            </span>
                        </div>
                        {event.error && (
                            <div className="text-[#f48771] ml-[88px] mt-1">
                                Error: {event.error}
                            </div>
                        )}
                    </div>
                ))}
                <div ref={logEndRef} />
            </div>
        </div>
    );
};

ExecutionLog.propTypes = {
    events: PropTypes.arrayOf(PropTypes.object),
    maxHeight: PropTypes.number,
};

export default ExecutionLog;
