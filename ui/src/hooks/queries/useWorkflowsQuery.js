import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsWorkflows } from '../useRouteNeedsWorkflows';

export function useWorkflowsQuery(options = {}) {
  const routeEnabled = useRouteNeedsWorkflows();
  const { enabled: enabledOption, ...restOptions } = options;
  const queryEnabled = enabledOption ?? routeEnabled;

  return useQuery({
    queryKey: queryKeys.workflows,
    queryFn: ({ signal }) => agentApiClient.listWorkflows({ signal }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    ...restOptions,
    enabled: queryEnabled,
  });
}
