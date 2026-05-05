import React, { useRef, useEffect } from 'react';
import PropTypes from 'prop-types';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CheckCircle2, XCircle, Play, Wrench, MessageSquare, AlertCircle } from 'lucide-react';

/**
 * Execution log component for displaying real-time logs in a user-friendly format
 */
const ExecutionLog = ({ events, maxHeight = 400 }) => {
    const logEndRef = useRef(null);

    // Auto-scroll to bottom when new events arrive
    useEffect(() => {
        logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [events]);

    const getEventIcon = (eventType) => {
        switch (eventType) {
            case 'workflow_started':
            case 'node_started':
                return <Play className="w-3.5 h-3.5 text-blue-500" />;
            case 'workflow_completed':
            case 'node_completed':
            case 'agent_complete':
                return <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />;
            case 'workflow_failed':
            case 'node_failed':
            case 'agent_error':
                return <XCircle className="w-3.5 h-3.5 text-red-500" />;
            case 'tool_call':
            case 'tool_result':
                return <Wrench className="w-3.5 h-3.5 text-purple-500" />;
            case 'llm_token':
                return <MessageSquare className="w-3.5 h-3.5 text-cyan-500" />;
            default:
                return <AlertCircle className="w-3.5 h-3.5 text-gray-500" />;
        }
    };

    const getEventColor = (eventType) => {
        if (eventType?.includes('completed') || eventType?.includes('complete')) {
            return 'bg-green-100 text-green-700 hover:bg-green-100 border-green-200';
        }
        if (eventType?.includes('failed') || eventType?.includes('error')) {
            return 'bg-red-100 text-red-700 hover:bg-red-100 border-red-200';
        }
        if (eventType?.includes('started') || eventType?.includes('start')) {
            return 'bg-blue-100 text-blue-700 hover:bg-blue-100 border-blue-200';
        }
        if (eventType?.includes('tool')) {
            return 'bg-purple-100 text-purple-700 hover:bg-purple-100 border-purple-200';
        }
        return 'bg-gray-100 text-gray-700 hover:bg-gray-100 border-gray-200';
    };

    const formatEventMessage = (event) => {
        const eventType = event.event_type || event.type;
        const data = event.data || event;

        switch (eventType) {
            case 'workflow_started':
                return `Workflow started: ${data.workflow_name || 'Unknown'}`;
            
            case 'workflow_completed':
                const duration = data.duration ? ` (${data.duration.toFixed(2)}s)` : '';
                return `Workflow completed successfully${duration}`;
            
            case 'workflow_failed':
                return `Workflow failed: ${data.error || 'Unknown error'}`;
            
            case 'node_started':
                return `Node started: ${data.node_id || data.node_name || 'Unknown'}`;
            
            case 'node_completed':
                return `Node completed: ${data.node_id || data.node_name || 'Unknown'}`;
            
            case 'node_failed':
                return `Node failed: ${data.node_id || data.node_name || 'Unknown'} - ${data.error || 'Unknown error'}`;
            
            case 'tool_call':
                const toolName = data.tool_name || data.tool || 'Unknown tool';
                const argsPreview = data.args ? ` with ${Object.keys(data.args).length} parameter(s)` : '';
                return `Calling tool: ${toolName}${argsPreview}`;
            
            case 'tool_result':
                const resultTool = data.tool_name || data.tool || 'Tool';
                const resultPreview = data.result ? String(data.result).substring(0, 100) : 'completed';
                return `${resultTool} returned: ${resultPreview}${String(data.result || '').length > 100 ? '...' : ''}`;
            
            case 'agent_complete':
                const msgCount = data.metadata?.message_count || data.message_count || 0;
                const toolCount = data.metadata?.tool_call_count || data.tool_call_count || 0;
                return `Agent completed (${msgCount} messages, ${toolCount} tool calls)`;
            
            case 'agent_error':
                return `Agent error: ${data.error || 'Unknown error'}`;
            
            case 'llm_token':
                // Skip token events in the log - they're shown in Agent Output section
                return null;
            
            case 'connected':
                return `Connected to execution: ${data.execution_id || ''}`;
            
            case 'keepalive':
                // Skip keepalive events
                return null;
            
            default:
                // For unknown events, show a clean message if available
                if (event.message) return event.message;
                if (event.step) return event.step;
                // Only show data if it's a simple string or has a meaningful message
                if (typeof data === 'string') return data;
                if (data.message) return data.message;
                if (data.status) return `Status: ${data.status}`;
                return `Event: ${eventType}`;
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

    const formatEventType = (eventType) => {
        if (!eventType) return 'event';
        return eventType
            .replace(/_/g, ' ')
            .replace(/\b\w/g, l => l.toUpperCase());
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

    // Filter out events that shouldn't be shown
    const visibleEvents = events.filter(event => {
        const eventType = event.event_type || event.type;
        const message = formatEventMessage(event);
        return message !== null && eventType !== 'keepalive' && eventType !== 'llm_token';
    });

    return (
        <div
            className="overflow-auto bg-[#1e1e1e] text-[#d4d4d4] rounded-lg border border-[#333]"
            style={{ maxHeight: `${maxHeight}px` }}
        >
            <div className="divide-y divide-[#333]">
                {visibleEvents.map((event, index) => {
                    const eventType = event.event_type || event.type;
                    const message = formatEventMessage(event);
                    
                    return (
                        <div
                            key={index}
                            className="px-3 py-2.5 hover:bg-[#2d2d2d] transition-colors"
                        >
                            <div className="flex items-start gap-2.5">
                                <span className="text-[#858585] text-xs min-w-[70px] mt-0.5">
                                    {formatTimestamp(event.timestamp)}
                                </span>
                                <div className="flex-shrink-0 mt-0.5">
                                    {getEventIcon(eventType)}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 flex-wrap mb-1">
                                        <Badge
                                            variant="outline"
                                            className={`text-xs ${getEventColor(eventType)}`}
                                        >
                                            {formatEventType(eventType)}
                                        </Badge>
                                    </div>
                                    <div className="text-sm text-[#d4d4d4] break-words">
                                        {message}
                                    </div>
                                    {event.error && (
                                        <div className="text-[#f48771] text-xs mt-1.5 p-2 bg-red-900/20 rounded border border-red-800/30">
                                            <span className="font-semibold">Error:</span> {event.error}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    );
                })}
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
