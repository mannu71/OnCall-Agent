import React, { useState } from 'react';
import PropTypes from 'prop-types';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Separator } from '@/components/ui/separator';
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { ChevronDown, ChevronUp, RefreshCw, Loader2 } from 'lucide-react';
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

    const handleRefresh = () => {
        clearEvents();
    };

    const getConnectionStatus = () => {
        if (isConnected) {
            return { text: 'Connected', color: 'text-green-600' };
        }
        if (error) {
            return { text: 'Error', color: 'text-red-600' };
        }
        return { text: 'Disconnected', color: 'text-gray-500' };
    };

    const connectionStatus = getConnectionStatus();

    return (
        <Card className="mb-4">
            <Collapsible open={expanded} onOpenChange={setExpanded}>
                <div className="p-4 flex items-center justify-between cursor-pointer hover:bg-accent/50"
                     onClick={() => setExpanded(!expanded)}>
                    <div className="flex items-center gap-3 flex-1">
                        <h3 className="text-lg font-semibold">{workflowName}</h3>
                        <StatusBadge status={status} />
                        {isConnected && status === 'running' && (
                            <Loader2 className="w-5 h-5 animate-spin" />
                        )}
                    </div>

                    <div className="flex items-center gap-2">
                        <span className={`text-xs ${connectionStatus.color}`}>
                            {connectionStatus.text}
                        </span>
                        <Button
                            size="icon"
                            variant="ghost"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleRefresh();
                            }}
                        >
                            <RefreshCw className="w-4 h-4" />
                        </Button>
                        <CollapsibleTrigger asChild>
                            <Button size="icon" variant="ghost">
                                {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                            </Button>
                        </CollapsibleTrigger>
                    </div>
                </div>

                <CollapsibleContent>
                    <Separator />
                    <CardContent className="pt-4">
                        {error && (
                            <Alert variant="destructive" className="mb-4">
                                <AlertDescription>{error}</AlertDescription>
                            </Alert>
                        )}

                        {!isConnected && !error && (
                            <Alert className="mb-4">
                                <AlertDescription>Waiting for connection...</AlertDescription>
                            </Alert>
                        )}

                        <h4 className="text-sm font-semibold mb-2">Execution Log</h4>
                        <ExecutionLog events={events} maxHeight={300} />

                        {events.length > 0 && (
                            <p className="text-xs text-muted-foreground mt-2">
                                {events.length} event{events.length === 1 ? '' : 's'} received
                            </p>
                        )}
                    </CardContent>
                </CollapsibleContent>
            </Collapsible>
        </Card>
    );
};

ExecutionMonitor.propTypes = {
    workflowName: PropTypes.string.isRequired,
    autoStart: PropTypes.bool,
    onClose: PropTypes.func,
};

export default ExecutionMonitor;
