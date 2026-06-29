import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsChat } from '../useRouteNeedsWorkflows';

export function useChatSessionsQuery(options = {}) {
  const routeEnabled = useRouteNeedsChat();
  const { includeArchived = false, limit = 100 } = options;
  return useQuery({
    queryKey: queryKeys.sessions({ includeArchived, limit }),
    queryFn: ({ signal }) => agentApiClient.listSessions({ includeArchived, limit, signal }),
    enabled: routeEnabled && (options.enabled !== false),
    ...options,
  });
}
