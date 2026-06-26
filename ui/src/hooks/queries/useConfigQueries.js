import { useQuery } from '@tanstack/react-query';
import agentApiClient from '../../services/agentApiClient';
import { getLLMs } from '../../services/llmService';
import { queryKeys } from '../../lib/queryKeys';

export function useMcpConfigQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.mcp,
    queryFn: ({ signal }) => agentApiClient.getMCPServers({ signal }),
    ...options,
  });
}

export function useMcpInputValuesQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.mcpInputValues,
    queryFn: ({ signal }) => agentApiClient.getMCPInputValues({ signal }),
    ...options,
  });
}

export function useLlmConfigQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.llm,
    queryFn: ({ signal }) => getLLMs({ signal }),
    ...options,
  });
}

export function useCertificatesQuery(options = {}) {
  return useQuery({
    queryKey: queryKeys.certificates,
    queryFn: ({ signal }) => agentApiClient.listCertificates({ signal }),
    ...options,
  });
}
