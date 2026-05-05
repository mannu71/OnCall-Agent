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
import { ChevronDown, ChevronUp, RefreshCw, Loader2, Wrench, Terminal, BarChart3, Clock, Zap } from 'lucide-react';
import { useWorkflowStream } from '../../hooks/useWorkflowStream';
import StatusBadge from './StatusBadge';
import ExecutionLog from './ExecutionLog';

const ExecutionMonitor = ({ workflowName, autoStart = false }) => {
    const [expanded, setExpanded] = useState(true);
    const [agentExpanded, setAgentExpanded] = useState(true);
    const [metadataExpanded, setMetadataExpanded] = useState(false);
    const { status, events, error, isConnected, clearEvents, agentTokens, agentToolCalls } = useWorkflowStream(
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

    // Extract metadata from completion events
    const getExecutionMetadata = () => {
        let metadata = {
            duration: null,
            tokenUsage: null,
            toolCallCount: 0,
            messageCount: 0,
            model: null,
            provider: null,
        };

        // Extract from workflow_completed event
        const workflowComplete = events.find(e => e.event_type === 'workflow_completed');
        if (workflowComplete?.data) {
            metadata.duration = workflowComplete.data.duration;
        }

        // Extract from agent_complete events
        const agentComplete = events.find(e => e.event_type === 'agent_complete');
        if (agentComplete?.data?.metadata) {
            const meta = agentComplete.data.metadata;
            if (meta.token_usage) {
                metadata.tokenUsage = meta.token_usage;
            }
            metadata.toolCallCount = meta.tool_call_count || 0;
            metadata.messageCount = meta.message_count || 0;
        }

        // Count tool calls from events
        const toolCallEvents = events.filter(e => e.event_type === 'tool_call');
        if (toolCallEvents.length > 0) {
            metadata.toolCallCount = toolCallEvents.length;
        }

        return metadata;
    };

    const connectionStatus = getConnectionStatus();
    const hasAgentOutput = Object.keys(agentTokens).length > 0 || agentToolCalls.length > 0;
    const executionMetadata = getExecutionMetadata();

    return (
        <Card className="mb-4 shadow-sm">
            <Collapsible open={expanded} onOpenChange={setExpanded}>
                <div className="p-4 flex items-center justify-between cursor-pointer hover:bg-accent/50 transition-colors"
                     onClick={() => setExpanded(!expanded)}>
                    <div className="flex items-center gap-3 flex-1">
                        <h3 className="text-lg font-semibold">{workflowName}</h3>
                        <StatusBadge status={status} />
                        {isConnected && status === 'running' && (
                            <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
                        )}
                    </div>

                    <div className="flex items-center gap-2">
                        <span className={`text-xs font-medium ${connectionStatus.color}`}>
                            {connectionStatus.text}
                        </span>
                        <Button
                            size="icon"
                            variant="ghost"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleRefresh();
                            }}
                            title="Clear events"
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
                    <CardContent className="pt-4 space-y-4">
                        {error && (
                            <Alert variant="destructive">
                                <AlertDescription className="flex items-center gap-2">
                                    <span className="font-semibold">Connection Error:</span>
                                    {error}
                                </AlertDescription>
                            </Alert>
                        )}

                        {!isConnected && !error && (
                            <Alert>
                                <AlertDescription className="flex items-center gap-2">
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                    Connecting to workflow execution...
                                </AlertDescription>
                            </Alert>
                        )}

                        {/* Execution Metrics Section - Show at top when available */}
                        {(executionMetadata.tokenUsage || executionMetadata.duration || executionMetadata.toolCallCount > 0) && (
                            <Collapsible open={metadataExpanded} onOpenChange={setMetadataExpanded}>
                                <div 
                                    className="flex items-center gap-2 cursor-pointer p-2 rounded-md hover:bg-accent/50 transition-colors" 
                                    onClick={() => setMetadataExpanded(!metadataExpanded)}
                                >
                                    <BarChart3 className="w-4 h-4 text-purple-500" />
                                    <h4 className="text-sm font-semibold">Execution Metrics</h4>
                                    {metadataExpanded ? <ChevronUp className="w-3 h-3 ml-auto" /> : <ChevronDown className="w-3 h-3 ml-auto" />}
                                </div>
                                <CollapsibleContent>
                                    <div className="mt-2 rounded-md border bg-gradient-to-br from-purple-50/50 to-blue-50/50 p-4">
                                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                            {executionMetadata.duration && (
                                                <div className="flex items-center gap-2">
                                                    <Clock className="w-4 h-4 text-blue-500" />
                                                    <div>
                                                        <div className="text-xs text-muted-foreground">Duration</div>
                                                        <div className="text-sm font-semibold">{executionMetadata.duration.toFixed(2)}s</div>
                                                    </div>
                                                </div>
                                            )}
                                            {executionMetadata.tokenUsage && (
                                                <>
                                                    <div className="flex items-center gap-2">
                                                        <Zap className="w-4 h-4 text-purple-500" />
                                                        <div>
                                                            <div className="text-xs text-muted-foreground">Total Tokens</div>
                                                            <div className="text-sm font-semibold text-purple-600">
                                                                {executionMetadata.tokenUsage.total_tokens?.toLocaleString() || 0}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <div className="w-4 h-4" />
                                                        <div>
                                                            <div className="text-xs text-muted-foreground">Prompt</div>
                                                            <div className="text-sm font-semibold">
                                                                {executionMetadata.tokenUsage.prompt_tokens?.toLocaleString() || 0}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <div className="w-4 h-4" />
                                                        <div>
                                                            <div className="text-xs text-muted-foreground">Completion</div>
                                                            <div className="text-sm font-semibold">
                                                                {executionMetadata.tokenUsage.completion_tokens?.toLocaleString() || 0}
                                                            </div>
                                                        </div>
                                                    </div>
                                                </>
                                            )}
                                            {executionMetadata.toolCallCount > 0 && (
                                                <div className="flex items-center gap-2">
                                                    <Wrench className="w-4 h-4 text-orange-500" />
                                                    <div>
                                                        <div className="text-xs text-muted-foreground">Tool Calls</div>
                                                        <div className="text-sm font-semibold">{executionMetadata.toolCallCount}</div>
                                                    </div>
                                                </div>
                                            )}
                                            {executionMetadata.messageCount > 0 && (
                                                <div className="flex items-center gap-2">
                                                    <Terminal className="w-4 h-4 text-cyan-500" />
                                                    <div>
                                                        <div className="text-xs text-muted-foreground">Messages</div>
                                                        <div className="text-sm font-semibold">{executionMetadata.messageCount}</div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                </CollapsibleContent>
                            </Collapsible>
                        )}

                        {/* Agent Output Section */}
                        {hasAgentOutput && (
                            <Collapsible open={agentExpanded} onOpenChange={setAgentExpanded}>
                                <div 
                                    className="flex items-center gap-2 cursor-pointer p-2 rounded-md hover:bg-accent/50 transition-colors" 
                                    onClick={() => setAgentExpanded(!agentExpanded)}
                                >
                                    <Terminal className="w-4 h-4 text-blue-500" />
                                    <h4 className="text-sm font-semibold">Agent Output</h4>
                                    {agentExpanded ? <ChevronUp className="w-3 h-3 ml-auto" /> : <ChevronDown className="w-3 h-3 ml-auto" />}
                                </div>
                                <CollapsibleContent>
                                    <div className="mt-2 space-y-3">
                                        {Object.entries(agentTokens).map(([nodeId, text]) => (
                                            <div key={nodeId} className="rounded-md border bg-[#1e1e1e] p-3">
                                                <div className="text-xs text-[#858585] mb-2 font-semibold">Agent: {nodeId}</div>
                                                <div className="text-sm text-[#d4d4d4] font-mono whitespace-pre-wrap break-words leading-relaxed">
                                                    {text}
                                                    {status === 'running' && <span className="animate-pulse ml-1">▊</span>}
                                                </div>
                                            </div>
                                        ))}

                                        {agentToolCalls.length > 0 && (
                                            <div className="rounded-md border bg-muted/50 p-3">
                                                <div className="flex items-center gap-1.5 mb-2">
                                                    <Wrench className="w-3.5 h-3.5 text-muted-foreground" />
                                                    <span className="text-xs font-semibold text-muted-foreground">
                                                        Tool Calls ({agentToolCalls.length})
                                                    </span>
                                                </div>
                                                <div className="space-y-1.5 max-h-48 overflow-y-auto">
                                                    {agentToolCalls.map((tc, i) => (
                                                        <div key={i} className="text-xs font-mono p-1.5 rounded bg-background/50">
                                                            {tc.type === 'call' ? (
                                                                <span className="text-blue-600 dark:text-blue-400">
                                                                    → {tc.tool}({tc.args ? Object.keys(tc.args).join(', ') : ''})
                                                                </span>
                                                            ) : (
                                                                <span className="text-green-600 dark:text-green-400">
                                                                    ← {tc.tool}: {String(tc.result || '').substring(0, 100)}
                                                                    {String(tc.result || '').length > 100 ? '...' : ''}
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

                        {/* Execution Log Section */}
                        <div>
                            <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                                <span>Execution Log</span>
                                {events.length > 0 && (
                                    <span className="text-xs text-muted-foreground font-normal">
                                        ({events.filter(e => e.event_type !== 'llm_token' && e.event_type !== 'keepalive').length} events)
                                    </span>
                                )}
                            </h4>
                            <ExecutionLog events={events} maxHeight={300} />
                        </div>
                    </CardContent>
                </CollapsibleContent>
            </Collapsible>
        </Card>
    );
};

ExecutionMonitor.propTypes = {
    workflowName: PropTypes.string.isRequired,
    autoStart: PropTypes.bool,
};

export default ExecutionMonitor;
