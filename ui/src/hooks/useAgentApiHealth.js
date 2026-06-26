import { useCallback } from 'react';

import { useHealthQuery } from './queries/useHealthQuery';



/**

 * Agent API `/api/v1/health` state — same semantics as Settings "Check API".

 *

 * @param {object} [options]

 * @param {number | null | undefined} [options.pollIntervalMs] - Ignored when using TanStack (always 30s).

 * @param {boolean} [options.initialShowSpinner] - If true, first fetch toggles loading (Settings-style).

 */

export function useAgentApiHealth(options = {}) {

  const initialShowSpinner = options.initialShowSpinner ?? false;

  const { data: health, isLoading, isFetching, refetch } = useHealthQuery();



  const startedAtRef = { current: null };



  const apiHealth = health

    ? (() => {

        const isHealthy = health?.status === 'healthy';

        return {

          ...health,

          status: isHealthy ? 'healthy' : 'error',

          message: isHealthy ? undefined : health?.status || 'API reported unhealthy',

        };

      })()

    : null;



  const loading = initialShowSpinner ? isLoading : false;



  const recheckWithSpinner = useCallback(async () => {

    startedAtRef.current = performance.now();

    const result = await refetch();

    const latencyMs = Math.round(performance.now() - startedAtRef.current);

    if (result.data) {

      const isHealthy = result.data?.status === 'healthy';

      return {

        ...result.data,

        status: isHealthy ? 'healthy' : 'error',

        latencyMs,

        message: isHealthy ? undefined : result.data?.status || 'API reported unhealthy',

      };

    }

    return {

      status: 'error',

      latencyMs,

      message: result.error?.message || 'Failed to connect to API',

    };

  }, [refetch]);



  return {

    apiHealth: apiHealth && !isFetching ? { ...apiHealth, latencyMs: apiHealth.latencyMs } : apiHealth,

    loading,

    recheckWithSpinner,

  };

}


