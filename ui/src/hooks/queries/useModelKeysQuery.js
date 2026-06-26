import { useQuery } from '@tanstack/react-query';
import { getModelKeys } from '../../services/modelKeyService';
import { queryKeys } from '../../lib/queryKeys';

export function useModelKeysQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.modelKeys,
    queryFn: ({ signal }) => getModelKeys({ signal }),
    ...options,
  });
}
