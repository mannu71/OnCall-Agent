import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsActiveWorkflows } from '../useRouteNeedsWorkflows';

export function useActiveWorkflowsQuery(options = {}) {
  const enabled = useRouteNeedsActiveWorkflows();
  return useQuery({
    queryKey: queryKeys.activeWorkflows,
    queryFn: ({ signal }) => agentApiClient.listActiveWorkflows({ signal }),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    enabled: enabled && (options.enabled !== false),
    ...options,
  });
}
