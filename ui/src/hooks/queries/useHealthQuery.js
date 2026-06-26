import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';

export function useHealthQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => agentApiClient.getHealth({ signal }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    ...options,
  });
}
