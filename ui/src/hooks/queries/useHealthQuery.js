import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';

export function useHealthQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: async ({ signal }) => {
      const start = performance.now();
      const data = await agentApiClient.getHealth({ signal });
      return { ...data, latencyMs: Math.round(performance.now() - start) };
    },
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    ...options,
  });
}
