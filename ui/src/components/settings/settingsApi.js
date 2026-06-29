import {
    addDiscoveredModels,
    discoverModels,
} from '../../services/llmService';
import {
    testLLMConnection as testLLMConnectionAPI,
    testMCPServer as testMCPServerAPI,
} from '../../services/apiClient';

export async function discoverBedrockModels() {
    return discoverModels('AWS Bedrock');
}

export async function importDiscoveredModels(models, region) {
    return addDiscoveredModels(models, region);
}

export async function testMcpServer(serverName, serverConfig) {
    return testMCPServerAPI(serverName, serverConfig);
}

export async function testLlmConnection(llmName) {
    return testLLMConnectionAPI(llmName, {});
}

export function setConnectionResult(setter, key, result, fallbackError) {
    setter((prev) => ({
        ...prev,
        [key]: result.success
            ? { status: 'connected', message: result.message || 'Connected' }
            : { status: 'error', message: result.error || fallbackError },
    }));
}

export function setConnectionTesting(setter, key, message = 'Testing connection...') {
    setter((prev) => ({
        ...prev,
        [key]: { status: 'testing', message },
    }));
}

export function setConnectionError(setter, key, error, fallback = 'Connection test failed') {
    setter((prev) => ({
        ...prev,
        [key]: { status: 'error', message: error?.message || fallback },
    }));
}
