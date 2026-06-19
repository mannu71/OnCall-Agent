/**
 * Log-watch API service.
 *
 * Centralises the `/log-watch/*` endpoints that LogWatchConfig previously called
 * with a hardcoded `http://localhost:8000/api/v1`. Base-URL resolution now goes
 * through `getApiBaseUrl()` (respects VITE_API_URL). Behaviour is preserved: each
 * call resolves to the parsed JSON body exactly as the old inline `fetch().json()`
 * did, so callers keep their existing `data.success` / `data.id` checks.
 */
import { getApiBaseUrl } from './apiClient';

const root = () => `${getApiBaseUrl()}/log-watch`;

async function getJson(path) {
    const response = await fetch(`${root()}${path}`);
    return response.json();
}

async function postJson(path, body) {
    const response = await fetch(`${root()}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    return response.json();
}

export const logWatchService = {
    getAlerts: () => getJson('/alerts'),
    getAlertSummary: () => getJson('/alerts/summary'),
    getKnownIssues: () => getJson('/known-issues'),
    getPatterns: () => getJson('/patterns'),
    getBaselines: () => getJson('/baselines'),
    watch: (body) => postJson('/watch', body),
    detectAnomalies: (body) => postJson('/detect-anomalies', body),
    createAlert: (body) => postJson('/alerts', body),
    acknowledgeAlert: (alertId, body) => postJson(`/alerts/${alertId}/acknowledge`, body),
    resolveAlert: (alertId, body) => postJson(`/alerts/${alertId}/resolve`, body),
    addPattern: (body) => postJson('/patterns', body),
    addBaseline: (body) => postJson('/baselines', body),
    addKnownIssue: (body) => postJson('/known-issues', body),
};

export default logWatchService;
