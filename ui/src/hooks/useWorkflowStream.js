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
    // nodeStatuses: { [nodeId]: 'running' | 'completed' | 'failed' }
    const [nodeStatuses, setNodeStatuses] = useState({});
    // totalCost: cumulative USD cost from cost_update events
    const [totalCost, setTotalCost] = useState(0);
    // agentCosts: per-agent USD spend { [nodeId]: number }
    const [agentCosts, setAgentCosts] = useState({});
    // totalTokens: cumulative LLM token count (input + output) across all agent nodes
    const [totalTokens, setTotalTokens] = useState(0);
    // nodeTokens: per-node token counts { [nodeId]: number }
    const [nodeTokens, setNodeTokens] = useState({});
    const eventSourceRef = useRef(null);

    const clearEvents = useCallback(() => {
        setEvents([]);
        setError(null);
        setStatus('idle');
        setAgentTokens({});
        setAgentToolCalls([]);
        setNodeStatuses({});
        setTotalCost(0);
        setAgentCosts({});
        setTotalTokens(0);
        setNodeTokens({});
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

            const handleLlmToken = (event) => {
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
                        event_type: 'llm_token',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse llm_token event:', err);
                }
            };

            const handleToolCall = (event) => {
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
                        event_type: 'tool_call',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse tool_call event:', err);
                }
            };

            const handleToolResult = (event) => {
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
                        event_type: 'tool_result',
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse tool_result event:', err);
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

            const handleNodeStarted = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const nodeId = data.data?.node_id;
                    if (nodeId) {
                        setNodeStatuses(prev => ({ ...prev, [nodeId]: 'running' }));
                    }
                    setEvents(prev => [...prev, { ...data, event_type: 'node_started', timestamp: data.timestamp || new Date().toISOString() }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse node_started event:', err);
                }
            };

            const handleNodeCompleted = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const nodeId = data.data?.node_id;
                    if (nodeId) {
                        setNodeStatuses(prev => ({ ...prev, [nodeId]: 'completed' }));
                    }
                    const itok = data.data?.input_tokens  || 0;
                    const otok = data.data?.output_tokens || 0;
                    const ttok = data.data?.total_tokens  || (itok + otok);
                    if (ttok > 0) {
                        if (nodeId) setNodeTokens(prev => ({ ...prev, [nodeId]: ttok }));
                        setTotalTokens(prev => prev + ttok);
                    }
                    setEvents(prev => [...prev, { ...data, event_type: 'node_completed', timestamp: data.timestamp || new Date().toISOString() }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse node_completed event:', err);
                }
            };

            const handleNodeFailed = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const nodeId = data.data?.node_id;
                    if (nodeId) {
                        setNodeStatuses(prev => ({ ...prev, [nodeId]: 'failed' }));
                    }
                    setEvents(prev => [...prev, { ...data, event_type: 'node_failed', timestamp: data.timestamp || new Date().toISOString() }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse node_failed event:', err);
                }
            };

            const handleCostUpdate = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const delta = parseFloat(data.data?.cost_usd || data.data?.delta_usd || 0);
                    if (delta > 0) {
                        setTotalCost(prev => Math.round((prev + delta) * 1e6) / 1e6);

                        // Per-agent cost tracking — cost_update events may include
                        // a node_id to attribute spend to a specific agent.
                        const nodeId = data.data?.node_id;
                        if (nodeId) {
                            setAgentCosts(prev => ({
                                ...prev,
                                [nodeId]: Math.round(((prev[nodeId] || 0) + delta) * 1e6) / 1e6,
                            }));
                        }
                    }
                    setEvents(prev => [...prev, { ...data, event_type: 'cost_update', timestamp: data.timestamp || new Date().toISOString() }]);
                } catch (err) {
                    console.error('[SSE] Failed to parse cost_update event:', err);
                }
            };

            eventSource.addEventListener('llm_token', handleLlmToken);
            eventSource.addEventListener('tool_call', handleToolCall);
            eventSource.addEventListener('tool_result', handleToolResult);
            eventSource.addEventListener('agent_error', handleAgentError);
            eventSource.addEventListener('agent_complete', handleAgentComplete);
            eventSource.addEventListener('node_started', handleNodeStarted);
            eventSource.addEventListener('node_completed', handleNodeCompleted);
            eventSource.addEventListener('node_failed', handleNodeFailed);
            eventSource.addEventListener('cost_update', handleCostUpdate);

            eventSource.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

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

                    const eventType = data.event_type || data.data?.event_type;
                    if (eventType === 'workflow_completed') {
                        setStatus('completed');
                        const ttok = data.data?.total_tokens || 0;
                        if (ttok > 0) setTotalTokens(ttok);
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
        agentToolCalls,
        nodeStatuses,
        totalCost,
        agentCosts,
        totalTokens,
        nodeTokens,
    };
}

export default useWorkflowStream;
