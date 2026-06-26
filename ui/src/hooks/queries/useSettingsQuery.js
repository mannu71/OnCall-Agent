import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsSettings } from '../useRouteNeedsWorkflows';

export function useSettingsQuery(options = {}) {
  const routeEnabled = useRouteNeedsSettings();
  return useQuery({
    queryKey: queryKeys.settings,
    queryFn: ({ signal }) => agentApiClient.getSettings({ signal }),
    enabled: routeEnabled && (options.enabled !== false),
    ...options,
  });
}

export function useStatusQuery(options = {}) {
  const routeEnabled = useRouteNeedsSettings();
  return useQuery({
    queryKey: queryKeys.status,
    queryFn: ({ signal }) => agentApiClient.getStatus({ signal }),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    enabled: routeEnabled && (options.enabled !== false),
    ...options,
  });
}
