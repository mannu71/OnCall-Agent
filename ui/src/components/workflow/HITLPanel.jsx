import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import {
    AlertTriangle,
    CheckCircle2,
    XCircle,
    Loader2,
    ChevronDown,
    ChevronRight,
} from 'lucide-react';
import { agentApiClient } from '../../services/agentApiClient';

/**
 * HITLPanel — Human-in-the-Loop approval panel.
 *
 * Watches the events array from useWorkflowStream for `hitl_pause` events.
 * When one arrives the panel renders a blocking card showing the agent's
 * root-cause summary and ranked suggestions.  The operator can approve
 * (resume the investigation) or reject (stop the workflow).
 *
 * Props:
 *   events        — event array from useWorkflowStream
 *   executionId   — current execution ID (needed for the approve API call)
 *   onDecision    — optional callback(approved: bool) fired after submission
 */
const HITLPanel = ({ events, executionId, onDecision }) => {
    const [hitlRequest, setHitlRequest] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [decision, setDecision] = useState(null); // 'approved' | 'rejected'
    const [error, setError] = useState(null);
    const [suggestionsOpen, setSuggestionsOpen] = useState(true);

    // Watch for hitl_pause events
    useEffect(() => {
        if (!events || events.length === 0) return;

        const last = events[events.length - 1];
        if ((last.event_type || last.type) === 'hitl_pause') {
            const data = last.data || last.payload || last;
            setHitlRequest({
                requestId: data.request_id,
                executionId: data.execution_id || executionId,
                rootCause: data.root_cause || 'Root cause analysis complete.',
                suggestions: Array.isArray(data.suggestions) ? data.suggestions : [],
                toolName: data.tool_name || null,
                toolParams: data.tool_params || null,
            });
            setDecision(null);
            setError(null);
        }
    }, [events, executionId]);

    const handleDecision = useCallback(async (approved) => {
        if (!hitlRequest || submitting) return;

        setSubmitting(true);
        setError(null);

        try {
            await agentApiClient.approveHITL(
                hitlRequest.executionId || executionId,
                hitlRequest.requestId,
                approved,
            );
            setDecision(approved ? 'approved' : 'rejected');
            onDecision?.(approved);
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Request failed';
            setError(msg);
        } finally {
            setSubmitting(false);
        }
    }, [hitlRequest, executionId, submitting, onDecision]);

    // Nothing to show
    if (!hitlRequest) return null;

    // Show outcome badge after decision
    if (decision) {
        return (
            <Card className="mb-4 border-2 border-dashed border-muted">
                <CardContent className="pt-4 pb-3">
                    <div className="flex items-center gap-2">
                        {decision === 'approved' ? (
                            <>
                                <CheckCircle2 className="w-5 h-5 text-green-500" />
                                <span className="text-sm font-medium text-green-700">
                                    Investigation approved — agent resuming…
                                </span>
                            </>
                        ) : (
                            <>
                                <XCircle className="w-5 h-5 text-red-500" />
                                <span className="text-sm font-medium text-red-700">
                                    Execution rejected — workflow stopping.
                                </span>
                            </>
                        )}
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className="mb-4 border-2 border-amber-400 shadow-md">
            <CardHeader className="pb-2">
                <div className="flex items-center gap-2">
                    <AlertTriangle className="w-5 h-5 text-amber-500" />
                    <CardTitle className="text-base">Agent Paused — Approval Required</CardTitle>
                    <Badge variant="outline" className="ml-auto text-amber-600 border-amber-400">
                        HITL
                    </Badge>
                </div>
            </CardHeader>

            <CardContent className="space-y-4">
                {/* Root cause */}
                <div>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                        Root Cause Analysis
                    </p>
                    <div className="rounded-md bg-amber-50 border border-amber-200 p-3 text-sm leading-relaxed text-amber-900 whitespace-pre-wrap">
                        {hitlRequest.rootCause}
                    </div>
                </div>

                {/* Suggestions */}
                {hitlRequest.suggestions.length > 0 && (
                    <div>
                        <button
                            className="flex items-center gap-1 text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1 hover:text-foreground"
                            onClick={() => setSuggestionsOpen(o => !o)}
                        >
                            {suggestionsOpen
                                ? <ChevronDown className="w-3 h-3" />
                                : <ChevronRight className="w-3 h-3" />}
                            Suggestions ({hitlRequest.suggestions.length})
                        </button>
                        {suggestionsOpen && (
                            <ol className="space-y-1.5 pl-1">
                                {hitlRequest.suggestions.map((s, i) => (
                                    <li key={i} className="flex gap-2 text-sm">
                                        <span className="flex-shrink-0 font-semibold text-muted-foreground">
                                            {i + 1}.
                                        </span>
                                        <span>{s}</span>
                                    </li>
                                ))}
                            </ol>
                        )}
                    </div>
                )}

                {/* Pending tool action (if the pause is for a specific tool call) */}
                {hitlRequest.toolName && (
                    <div>
                        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                            Pending Tool Action
                        </p>
                        <div className="rounded-md bg-muted p-2 font-mono text-xs">
                            <span className="text-blue-600">{hitlRequest.toolName}</span>
                            {hitlRequest.toolParams && (
                                <span className="text-muted-foreground ml-1">
                                    ({JSON.stringify(hitlRequest.toolParams)})
                                </span>
                            )}
                        </div>
                    </div>
                )}

                {/* Error */}
                {error && (
                    <Alert variant="destructive">
                        <AlertDescription>{error}</AlertDescription>
                    </Alert>
                )}

                {/* Actions */}
                <div className="flex gap-3 pt-1">
                    <Button
                        className="flex-1 bg-green-600 hover:bg-green-700 text-white"
                        onClick={() => handleDecision(true)}
                        disabled={submitting}
                    >
                        {submitting ? (
                            <Loader2 className="w-4 h-4 animate-spin mr-1" />
                        ) : (
                            <CheckCircle2 className="w-4 h-4 mr-1" />
                        )}
                        Approve &amp; Continue
                    </Button>

                    <Button
                        variant="destructive"
                        className="flex-1"
                        onClick={() => handleDecision(false)}
                        disabled={submitting}
                    >
                        {submitting ? (
                            <Loader2 className="w-4 h-4 animate-spin mr-1" />
                        ) : (
                            <XCircle className="w-4 h-4 mr-1" />
                        )}
                        Reject &amp; Stop
                    </Button>
                </div>
            </CardContent>
        </Card>
    );
};

HITLPanel.propTypes = {
    events: PropTypes.array.isRequired,
    executionId: PropTypes.string,
    onDecision: PropTypes.func,
};

export default HITLPanel;
