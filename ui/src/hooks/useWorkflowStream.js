import { useState, useEffect, useCallback, useRef } from 'react';
import agentApiClient from '../services/agentApiClient';

/**
 * Custom hook for streaming workflow execution events via SSE
 * 
 * @param {string} workflowName - Name of the workflow to monitor
 * @param {boolean} enabled - Whether to enable streaming
 * @returns {Object} - { status, events, error, isConnected, clearEvents }
 */
export function useWorkflowStream(workflowName, enabled = true) {
    const [status, setStatus] = useState('idle');
    const [events, setEvents] = useState([]);
    const [error, setError] = useState(null);
    const [isConnected, setIsConnected] = useState(false);
    const eventSourceRef = useRef(null);

    const clearEvents = useCallback(() => {
        setEvents([]);
        setError(null);
        setStatus('idle');
    }, []);

    useEffect(() => {
        if (!enabled || !workflowName) {
            return;
        }

        console.log(`[SSE] Connecting to workflow stream: ${workflowName}`);

        try {
            const eventSource = agentApiClient.streamWorkflowExecution(workflowName);
            eventSourceRef.current = eventSource;

            eventSource.onopen = () => {
                console.log(`[SSE] Connected to ${workflowName}`);
                setIsConnected(true);
                setError(null);
            };

            eventSource.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log(`[SSE] Event received:`, data);

                    setEvents(prev => [...prev, {
                        ...data,
                        timestamp: data.timestamp || new Date().toISOString()
                    }]);

                    // Update status based on event type
                    if (data.type === 'status') {
                        setStatus(data.status);
                    } else if (data.type === 'complete') {
                        setStatus('completed');
                    } else if (data.type === 'error') {
                        setStatus('failed');
                        setError(data.error || 'Unknown error');
                    }
                } catch (err) {
                    console.error('[SSE] Failed to parse event:', err);
                }
            };

            eventSource.onerror = (err) => {
                console.error(`[SSE] Connection error for ${workflowName}:`, err);
                setIsConnected(false);
                setError('Connection lost');

                // Close the connection
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

        // Cleanup on unmount or when dependencies change
        return () => {
            if (eventSourceRef.current) {
                console.log(`[SSE] Closing connection to ${workflowName}`);
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
        clearEvents
    };
}

export default useWorkflowStream;
