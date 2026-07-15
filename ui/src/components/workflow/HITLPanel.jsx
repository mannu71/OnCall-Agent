import React, { useState, useEffect, useCallback, useRef } from 'react';
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
    Settings2,
    ShieldCheck,
    ShieldBan,
    HelpCircle,
} from 'lucide-react';
import { agentApiClient } from '../../services/agentApiClient';

// ─────────────────────────────────────────────────────────────────────────────
// Per-tool permission model — stored in localStorage
// Values: 'always_allow' | 'ask' | 'deny'
// ─────────────────────────────────────────────────────────────────────────────
const PERM_KEY = 'hitl_tool_permissions';

function loadPermissions() {
    try {
        return JSON.parse(localStorage.getItem(PERM_KEY) || '{}');
    } catch {
        return {};
    }
}

function savePermissions(perms) {
    try {
        localStorage.setItem(PERM_KEY, JSON.stringify(perms));
    } catch {
        /* localStorage unavailable */
    }
}

function useToolPermissions() {
    const [permissions, setPermissions] = useState(loadPermissions);

    const setPermission = useCallback((toolName, level) => {
        setPermissions(prev => {
            const next = { ...prev, [toolName]: level };
            savePermissions(next);
            return next;
        });
    }, []);

    const getPermission = useCallback((toolName) => {
        return permissions[toolName] || 'ask';
    }, [permissions]);

    return { permissions, setPermission, getPermission };
}

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
    const [showPermConfig, setShowPermConfig] = useState(false);
    const autoHandledRef = useRef(false);
    const lastScannedIndexRef = useRef(0);

    const { getPermission, setPermission } = useToolPermissions();

    // Watch for hitl_pause events (only scan newly appended events)
    useEffect(() => {
        if (!events || events.length === 0) return;
        if (events.length <= lastScannedIndexRef.current) return;

        const newEvents = events.slice(lastScannedIndexRef.current);
        lastScannedIndexRef.current = events.length;

        let last = null;
        for (let i = newEvents.length - 1; i >= 0; i--) {
            if ((newEvents[i].event_type || newEvents[i].type) === 'hitl_pause') {
                last = newEvents[i];
                break;
            }
        }
        if (!last) return;

        const data = last.data || last.payload || last;
        // Backend (tool_permissions.py) publishes `tool` + `args`; older/HITL
        // synthesis events may use `tool_name`/`tool_params`. Accept both so the
        // approval card always renders the real tool name + arguments.
        const toolName = data.tool || data.tool_name || null;
        const toolParams = data.args || data.tool_params || null;
        const newRequest = {
            requestId: data.request_id,
            executionId: data.execution_id || executionId,
            rootCause: data.root_cause || data.message || data.draft_answer || 'Analysis complete.',
            suggestions: Array.isArray(data.suggestions) ? data.suggestions : [],
            toolName,
            toolParams,
            // Action Supervisor advisory (present when the supervisor is enabled).
            riskTier: data.risk_tier || null,
            supervisorVerdict: data.supervisor_verdict || null,
            supervisorReasoning: data.supervisor_reasoning || null,
        };
        setHitlRequest(newRequest);
        setDecision(null);
        setError(null);
        autoHandledRef.current = false;

        // Auto-handle based on per-tool permission level. A high-risk action
        // (Action Supervisor tier) must never be silently always-allowed from a
        // saved client preference — a human has to see it. 'deny' still applies.
        if (toolName) {
            let perm = getPermission(toolName);
            if (perm === 'always_allow' && newRequest.riskTier === 'high') {
                perm = 'ask';
            }
            if (perm === 'always_allow') {
                autoHandledRef.current = true;
                setDecision('approved');
                agentApiClient.approveHITL(
                    newRequest.executionId || executionId,
                    newRequest.requestId,
                    true,
                ).catch(() => {});
                onDecision?.(true);
            } else if (perm === 'deny') {
                autoHandledRef.current = true;
                setDecision('rejected');
                agentApiClient.approveHITL(
                    newRequest.executionId || executionId,
                    newRequest.requestId,
                    false,
                ).catch(() => {});
                onDecision?.(false);
            }
        }
    }, [events, executionId, getPermission, onDecision]);

    // Shift+A — batch approve the current pending request
    useEffect(() => {
        const handleKey = (e) => {
            if (e.shiftKey && e.key === 'A' && hitlRequest && !decision && !submitting) {
                handleDecision(true);
            }
        };
        window.addEventListener('keydown', handleKey);
        return () => window.removeEventListener('keydown', handleKey);
    });

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
                {/* Analysis summary */}
                <div>
                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                        Analysis Summary
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
                        </div>

                        {/* edit_file → show the diff; other tools → key/value list */}
                        {hitlRequest.toolName === 'edit_file' && hitlRequest.toolParams ? (
                            <div className="mt-1.5 space-y-1.5 text-[11px]">
                                {hitlRequest.toolParams.repo && (
                                    <div className="text-muted-foreground">
                                        <span className="font-semibold">Repo:</span>{' '}
                                        <span className="font-mono">{hitlRequest.toolParams.repo}</span>
                                    </div>
                                )}
                                {hitlRequest.toolParams.file && (
                                    <div className="text-muted-foreground">
                                        <span className="font-semibold">File:</span>{' '}
                                        <span className="font-mono">{hitlRequest.toolParams.file}</span>
                                    </div>
                                )}
                                {hitlRequest.toolParams.old_string !== undefined && (
                                    <div>
                                        <div className="font-semibold text-red-700 mb-0.5">− Replace</div>
                                        <pre className="max-h-28 overflow-auto rounded bg-red-50 border border-red-200 p-2 font-mono text-red-800 whitespace-pre-wrap">{hitlRequest.toolParams.old_string}</pre>
                                    </div>
                                )}
                                {hitlRequest.toolParams.new_string !== undefined && (
                                    <div>
                                        <div className="font-semibold text-emerald-700 mb-0.5">+ With</div>
                                        <pre className="max-h-28 overflow-auto rounded bg-emerald-50 border border-emerald-200 p-2 font-mono text-emerald-800 whitespace-pre-wrap">{hitlRequest.toolParams.new_string}</pre>
                                    </div>
                                )}
                            </div>
                        ) : (hitlRequest.toolParams && Object.keys(hitlRequest.toolParams).length > 0 && (
                            <div className="mt-1.5 space-y-1 text-[11px]">
                                {Object.entries(hitlRequest.toolParams).map(([k, v]) => (
                                    <div key={k} className="flex gap-1.5">
                                        <span className="font-semibold text-muted-foreground flex-shrink-0">{k}:</span>
                                        <span className="font-mono break-all">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
                                    </div>
                                ))}
                            </div>
                        ))}
                    </div>
                )}

                {/* Action Supervisor advisory verdict */}
                {hitlRequest.supervisorVerdict && (
                    <div className={`mt-2 rounded border p-2 text-[11px] ${
                        hitlRequest.riskTier === 'high'
                            ? 'border-amber-400 bg-amber-50'
                            : 'border-blue-300 bg-blue-50'
                    }`}>
                        <div className="flex items-center gap-1.5 font-semibold">
                            <span className="uppercase tracking-wide">
                                {hitlRequest.riskTier === 'high' ? 'High risk' : 'Low risk'}
                            </span>
                            <span className="text-muted-foreground">·</span>
                            <span>Supervisor: {hitlRequest.supervisorVerdict}</span>
                        </div>
                        {hitlRequest.supervisorReasoning && (
                            <div className="mt-0.5 text-muted-foreground">{hitlRequest.supervisorReasoning}</div>
                        )}
                    </div>
                )}

                {/* Per-tool permission config */}
                {hitlRequest.toolName && (
                    <div>
                        <button
                            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                            onClick={() => setShowPermConfig(o => !o)}
                        >
                            <Settings2 className="w-3 h-3" />
                            Tool permissions for <span className="font-mono ml-0.5">{hitlRequest.toolName}</span>
                            {showPermConfig ? <ChevronDown className="w-3 h-3 ml-1" /> : <ChevronRight className="w-3 h-3 ml-1" />}
                        </button>

                        {showPermConfig && (
                            <div className="mt-2 flex gap-2">
                                {[
                                    { level: 'always_allow', label: 'Always allow', Icon: ShieldCheck, cls: 'text-green-600 border-green-400' },
                                    { level: 'ask',          label: 'Ask each time', Icon: HelpCircle,  cls: 'text-amber-600 border-amber-400' },
                                    { level: 'deny',         label: 'Always deny',  Icon: ShieldBan,   cls: 'text-red-600 border-red-400' },
                                ].map(({ level, label, Icon, cls }) => {
                                    const active = getPermission(hitlRequest.toolName) === level;
                                    // High-risk actions can't be blanket "always allowed" — a
                                    // human must review each one (the Action Supervisor tier).
                                    const blocked = level === 'always_allow' && hitlRequest.riskTier === 'high';
                                    return (
                                        <button
                                            key={level}
                                            disabled={blocked}
                                            title={blocked ? 'High-risk actions require review each time' : undefined}
                                            className={`flex items-center gap-1 text-xs px-2 py-1 rounded border transition-colors
                                                ${blocked ? 'border-muted text-muted-foreground/40 cursor-not-allowed'
                                                    : active ? `${cls} bg-opacity-10 font-semibold` : 'border-muted text-muted-foreground hover:border-foreground'}`}
                                            onClick={() => !blocked && setPermission(hitlRequest.toolName, level)}
                                        >
                                            <Icon className="w-3 h-3" />
                                            {label}
                                        </button>
                                    );
                                })}
                            </div>
                        )}
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
                        title="Approve (Shift+A)"
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
                <p className="text-xs text-muted-foreground text-right">Shift+A to approve</p>
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
