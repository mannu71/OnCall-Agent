import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsExecutions } from '../useRouteNeedsWorkflows';

export function useExecutionsQuery(limit = 100, options = {}) {
  const enabled = useRouteNeedsExecutions();
  return useQuery({
    queryKey: queryKeys.executions(limit),
    queryFn: ({ signal }) => agentApiClient.listAllExecutions(limit, { signal }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled: enabled && (options.enabled !== false),
    ...options,
  });
}

export function useWorkflowExecutionsQuery(workflowName, limit = 20, options = {}) {
  return useQuery({
    queryKey: queryKeys.workflowExecutions(workflowName, limit),
    queryFn: ({ signal }) => agentApiClient.getWorkflowExecutions(workflowName, limit, { signal }),
    enabled: !!workflowName && (options.enabled !== false),
    ...options,
  });
}
