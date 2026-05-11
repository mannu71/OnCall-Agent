import { useState, useEffect, useCallback, useRef } from 'react';
import agentApiClient from '../services/agentApiClient';

/**
 * Custom hook for streaming workflow execution events via SSE
 * 
 * @param {string} workflowName - Name of the workflow to monitor
 * @param {boolean} enabled - Whether to enable streaming
 * @returns {Object} - { status, events, error, isConnected, clearEvents, agentTokens, agentToolCalls }
 */
export function useWorkflowStream(workflowName, enabled = true) {
    const [status, setStatus] = useState('idle');
    const [events, setEvents] = useState([]);
    const [error, setError] = useState(null);
    const [isConnected, setIsConnected] = useState(false);
    const [agentTokens, setAgentTokens] = useState({});
    const [agentToolCalls, setAgentToolCalls] = useState([]);
    const eventSourceRef = useRef(null);

    const clearEvents = useCallback(() => {
        setEvents([]);
        setError(null);
        setStatus('idle');
        setAgentTokens({});
        setAgentToolCalls([]);
    }, []);

    useEffect(() => {
        if (!enabled || !workflowName) {
            return;
        }

        try {
            const eventSource = agentApiClient.streamWorkflowExecution(workflowName);
            eventSourceRef.current = eventSource;

            eventSource.onopen = () => {
                setIsConnected(true);
                setError(null);
            };

            // Backend emits canonical event names via "event: <type>" SSE prefix
            // agent_* events use named SSE events; workflow/node events use unnamed (onmessage)

            const handleAgentToken = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const nodeId = data.data?.node_id || 'default';
                    const token = data.data?.token || '';

                    setAgentTokens(prev => ({
                        ...prev,
                        [nodeId]: (prev[nodeId] || '') + token,
                    }));

                    setEvents(prev => [...prev, {
                        ...data,
                        event_type: 'agent_token',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse agent_token event:', err);
                }
            };

            const handleAgentToolCall = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    setAgentToolCalls(prev => [...prev, {
                        type: 'call',
                        tool: data.data?.tool,
                        args: data.data?.args,
                        nodeId: data.data?.node_id,
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);

                    setEvents(prev => [...prev, {
                        ...data,
                        event_type: 'agent_tool_call',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse agent_tool_call event:', err);
                }
            };

            const handleAgentToolResult = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    setAgentToolCalls(prev => [...prev, {
                        type: 'result',
                        tool: data.data?.tool,
                        result: data.data?.result,
                        nodeId: data.data?.node_id,
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);

                    setEvents(prev => [...prev, {
                        ...data,
                        event_type: 'agent_tool_result',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse agent_tool_result event:', err);
                }
            };

            const handleAgentError = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    setEvents(prev => [...prev, {
                        ...data,
                        event_type: 'agent_error',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse agent_error event:', err);
                }
            };

            const handleAgentComplete = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    setEvents(prev => [...prev, {
                        ...data,
                        event_type: 'agent_complete',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse agent_complete event:', err);
                }
            };

            // Register named SSE event listeners (canonical agent_* names from backend)
            eventSource.addEventListener('agent_token', handleAgentToken);
            eventSource.addEventListener('agent_tool_call', handleAgentToolCall);
            eventSource.addEventListener('agent_tool_result', handleAgentToolResult);
            eventSource.addEventListener('agent_error', handleAgentError);
            eventSource.addEventListener('agent_complete', handleAgentComplete);

            // onmessage fires for unnamed SSE messages (workflow/node level events)
            // Agent-level events (agent_*) are sent as named events and handled above
            eventSource.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const eventType = data.event_type || data.data?.event_type;

                    // Skip agent events here — they're handled by named listeners above
                    if (eventType && eventType.startsWith('agent_')) return;

                    setEvents(prev => [...prev, {
                        ...data,
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);

                    if (data.type === 'status') {
                        setStatus(data.status);
                    } else if (data.type === 'complete') {
                        setStatus('completed');
                    } else if (data.type === 'error') {
                        setStatus('failed');
                        setError(data.error || 'Unknown error');
                    }

                    if (eventType === 'workflow_started') {
                        setStatus('running');
                    } else if (eventType === 'workflow_completed') {
                        setStatus('completed');
                    } else if (eventType === 'workflow_failed') {
                        setStatus('failed');
                        setError(data.data?.error || 'Workflow failed');
                    }
                } catch (err) {
                    console.error('[SSE] Failed to parse event:', err);
                }
            };

            eventSource.onerror = (err) => {
                console.error(`[SSE] Connection error for ${workflowName}:`, err);
                setIsConnected(false);
                setError('Connection lost');

                if (eventSourceRef.current) {
                    eventSourceRef.current.close();
                    eventSourceRef.current = null;
                }
            };

        } catch (err) {
            console.error('[SSE] Failed to create EventSource:', err);
            setError(err.message);
            setIsConnected(false);
        }

        return () => {
            if (eventSourceRef.current) {
                eventSourceRef.current.close();
                eventSourceRef.current = null;
            }
            setIsConnected(false);
        };
    }, [workflowName, enabled]);

    return {
        status,
        events,
        error,
        isConnected,
        clearEvents,
        agentTokens,
        agentToolCalls
    };
}

export default useWorkflowStream;
