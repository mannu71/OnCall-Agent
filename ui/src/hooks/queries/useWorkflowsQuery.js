import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsWorkflows } from '../useRouteNeedsWorkflows';

export function useWorkflowsQuery(options = {}) {
  const enabled = useRouteNeedsWorkflows();
  return useQuery({
    queryKey: queryKeys.workflows,
    queryFn: ({ signal }) => agentApiClient.listWorkflows({ signal }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled: enabled && (options.enabled !== false),
    ...options,
  });
}
