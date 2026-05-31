import { useCallback, useEffect, useState } from 'react';
import agentApiClient from '../services/agentApiClient';

/**
 * Agent API `/api/v1/health` state — same semantics as Settings "Check API".
 *
 * @param {object} [options]
 * @param {number | null | undefined} [options.pollIntervalMs] - If > 0, re-fetch on this interval (silent: no spinner).
 * @param {boolean} [options.initialShowSpinner] - If true, first fetch toggles loading (Settings-style).
 */
export function useAgentApiHealth(options = {}) {
  const pollIntervalMs = options.pollIntervalMs ?? null;
  const initialShowSpinner = options.initialShowSpinner ?? false;

  const [apiHealth, setApiHealth] = useState(null);
  const [loading, setLoading] = useState(false);

  const checkHealth = useCallback(async ({ showSpinner = false } = {}) => {
    if (showSpinner) setLoading(true);
    const startedAt = performance.now();
    try {
      const health = await agentApiClient.getHealth();
      const latencyMs = Math.round(performance.now() - startedAt);
      const isHealthy = health?.status === 'healthy';

      setApiHealth({
        ...health,
        status: isHealthy ? 'healthy' : 'error',
        latencyMs,
        message: isHealthy ? undefined : health?.status || 'API reported unhealthy',
      });
    } catch (error) {
      setApiHealth({
        status: 'error',
        latencyMs: Math.round(performance.now() - startedAt),
        message: error.message || 'Failed to connect to API',
      });
    } finally {
      if (showSpinner) setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkHealth({ showSpinner: initialShowSpinner });

    if (pollIntervalMs == null || pollIntervalMs <= 0) {
      return undefined;
    }

    const id = setInterval(() => {
      if (!document.hidden) checkHealth({ showSpinner: false });
    }, pollIntervalMs);

    return () => clearInterval(id);
  }, [checkHealth, initialShowSpinner, pollIntervalMs]);

  const recheckWithSpinner = useCallback(() => {
    return checkHealth({ showSpinner: true });
  }, [checkHealth]);

  return {
    apiHealth,
    loading,
    recheckWithSpinner,
  };
}
