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
import { ChevronDown, ChevronUp, RefreshCw, Loader2, Wrench, Terminal, DollarSign } from 'lucide-react';
import { useWorkflowStream } from '../../hooks/useWorkflowStream';
import StatusBadge from './StatusBadge';
import ExecutionLog from './ExecutionLog';
import HITLPanel from '../workflow/HITLPanel';

const ExecutionMonitor = ({ workflowName, executionId, autoStart = false, onClose }) => {
    const [expanded, setExpanded] = useState(true);
    const [agentExpanded, setAgentExpanded] = useState(true);
    const { status, events, error, isConnected, clearEvents, agentTokens, agentToolCalls, totalCost } = useWorkflowStream(
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
    const hasAgentOutput = Object.keys(agentTokens).length > 0 || agentToolCalls.length > 0;

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
                        {totalCost > 0 && (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground" title="Estimated LLM cost">
                                <DollarSign className="w-3 h-3" />
                                {totalCost < 0.001
                                    ? `< $0.001`
                                    : `$${totalCost.toFixed(4)}`}
                            </span>
                        )}
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

                        {hasAgentOutput && (
                            <Collapsible open={agentExpanded} onOpenChange={setAgentExpanded} className="mb-4">
                                <div className="flex items-center gap-2 cursor-pointer" onClick={() => setAgentExpanded(!agentExpanded)}>
                                    <Terminal className="w-4 h-4 text-blue-500" />
                                    <h4 className="text-sm font-semibold">Agent Output</h4>
                                    {agentExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                                </div>
                                <CollapsibleContent>
                                    <div className="mt-2 space-y-3">
                                        {Object.entries(agentTokens).map(([nodeId, text]) => (
                                            <div key={nodeId} className="rounded-md border bg-[#1e1e1e] p-3">
                                                <div className="text-xs text-[#858585] mb-1">Agent: {nodeId}</div>
                                                <div className="text-sm text-[#d4d4d4] font-mono whitespace-pre-wrap break-words">
                                                    {text}
                                                    {status === 'running' && <span className="animate-pulse">▊</span>}
                                                </div>
                                            </div>
                                        ))}

                                        {agentToolCalls.length > 0 && (
                                            <div className="rounded-md border bg-muted/50 p-3">
                                                <div className="flex items-center gap-1.5 mb-2">
                                                    <Wrench className="w-3.5 h-3.5 text-muted-foreground" />
                                                    <span className="text-xs font-semibold text-muted-foreground">Tool Calls ({agentToolCalls.length})</span>
                                                </div>
                                                <div className="space-y-1 max-h-48 overflow-y-auto">
                                                    {agentToolCalls.map((tc, i) => (
                                                        <div key={i} className="text-xs font-mono">
                                                            {tc.type === 'call' ? (
                                                                <span className="text-blue-400">
                                                                    → {tc.tool}({tc.args ? Object.keys(tc.args).join(', ') : ''})
                                                                </span>
                                                            ) : (
                                                                <span className="text-green-400">
                                                                    ← {tc.tool}: {String(tc.result || '').substring(0, 150)}
                                                                    {String(tc.result || '').length > 150 ? '...' : ''}
                                                                </span>
                                                            )}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </CollapsibleContent>
                            </Collapsible>
                        )}

                        <HITLPanel events={events} executionId={executionId} />

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
    executionId: PropTypes.string,
    autoStart: PropTypes.bool,
    onClose: PropTypes.func,
};

export default ExecutionMonitor;
