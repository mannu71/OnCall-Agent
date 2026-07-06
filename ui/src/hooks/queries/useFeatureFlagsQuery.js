import { useQuery } from '@tanstack/react-query';
import { getFeatureFlags } from '../../services/apiClient';
import { queryKeys } from '../../lib/queryKeys';
import { useRouteNeedsSettings } from '../useRouteNeedsWorkflows';

export function useFeatureFlagsQuery(options = {}) {
    const routeEnabled = useRouteNeedsSettings();
    return useQuery({
        queryKey: queryKeys.featureFlags,
        queryFn: () => getFeatureFlags(),
        enabled: routeEnabled && (options.enabled !== false),
        ...options,
    });
}
