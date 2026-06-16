import axios from 'axios';

/**
 * Agent-API Client
 * 
 * Provides methods to communicate directly with the agent-api service.
 */

const AGENT_API_URL = import.meta.env.VITE_AGENT_API_URL || 'http://localhost:8000';

const client = axios.create({
    baseURL: AGENT_API_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

// Add response interceptor for global error logging
client.interceptors.response.use(
    response => response,
    error => {
        const message = error.response?.data?.detail || error.message;
        console.error(`[API Error] ${error.config.method.toUpperCase()} ${error.config.url}:`, message);
        return Promise.reject(error);
    }
);

// ── Result extraction helpers ────────────────────────────────────────────────
// A visual-workflow execute response is keyed by node id, e.g.
//   { workflow_name, "agent_…": { final_answer, … }, "cloudwatch_tool_…": { output }, … }
// plus top-level input_tokens / output_tokens / total_tokens. These dig out the
// agent's answer (preferring the ReAct agent node) and the token counts.
function _nodeContainers(data) {
    if (!data || typeof data !== 'object') return [];
    return [data, data.result, data.results, data.output].filter(
        (c) => c && typeof c === 'object'
    );
}

export function extractFinalAnswer(data) {
    if (!data) return '';
    if (typeof data === 'string') return data;
    if (typeof data.final_answer === 'string' && data.final_answer) return data.final_answer;

    const containers = _nodeContainers(data);
    // Prefer the agent / ReAct node's final answer.
    for (const c of containers) {
        for (const v of Object.values(c)) {
            if (v && typeof v === 'object' && typeof v.final_answer === 'string' && v.final_answer) {
                return v.final_answer;
            }
        }
    }
    // Next: an explicit top-level string output.
    if (typeof data.output === 'string' && data.output.trim()) return data.output;
    // Fallback: a substantial node `output` (e.g. the CloudWatch deterministic report).
    let best = '';
    for (const c of containers) {
        for (const v of Object.values(c)) {
            if (v && typeof v === 'object' && typeof v.output === 'string' && v.output.length > best.length) {
                best = v.output;
            }
        }
    }
    return best;
}

export function extractTokens(data) {
    if (!data || typeof data !== 'object') return null;
    if (data.input_tokens != null || data.output_tokens != null || data.total_tokens != null) {
        const input = data.input_tokens || 0;
        const output = data.output_tokens || 0;
        return { input, output, total: data.total_tokens || input + output };
    }
    for (const c of _nodeContainers(data)) {
        for (const v of Object.values(c)) {
            if (v && typeof v === 'object' && (v.input_tokens != null || v.output_tokens != null)) {
                const input = v.input_tokens || 0;
                const output = v.output_tokens || 0;
                return { input, output, total: v.total_tokens || input + output };
            }
        }
    }
    return null;
}

export const agentApiClient = {
    /**
     * List all workflows
     */
    async listWorkflows() {
        const response = await client.get('/api/v1/workflows');
        return response.data;
    },

    /**
     * Get a specific workflow by name
     */
    async getWorkflow(workflowName) {
        const response = await client.get(`/api/v1/workflows/${encodeURIComponent(workflowName)}`);
        return response.data;
    },

    /**
     * Create a new workflow
     */
    async createWorkflow(workflow) {
        const response = await client.post('/api/v1/workflows', {
            ...workflow,
            type: workflow.type || 'workflow'
        });
        return response.data;
    },

    /**
     * Update an existing workflow
     */
    async updateWorkflow(workflowName, updates) {
        const response = await client.put(`/api/v1/workflows/${encodeURIComponent(workflowName)}`, updates);
        return response.data;
    },

    /**
     * Delete a workflow
     * @param {string} workflowName - Name of the workflow
     * @param {boolean} deleteScripts - Also delete SQL scripts (default: true)
     */
    async deleteWorkflow(workflowName, deleteScripts = true) {
        await client.delete(`/api/v1/workflows/${encodeURIComponent(workflowName)}`, {
            params: { delete_scripts: deleteScripts }
        });
    },

    /**
     * Execute a workflow manually.
     * @param {string} workflowName
     * @param {boolean|{background?:boolean, userQuery?:string}} opts
     *   Legacy boolean = background flag. Object form adds `userQuery` to drive an
     *   agent chat turn (sent as `query` → normalized to inputs.user_query backend-side).
     */
    async executeWorkflow(workflowName, opts = false) {
        const o = typeof opts === 'boolean' ? { background: opts } : (opts || {});
        const params = { background: !!o.background };
        if (o.userQuery) params.query = o.userQuery;
        if (o.outputMode) params.output_mode = o.outputMode;
        if (o.permissionMode) params.permission_mode = o.permissionMode;
        // Conversation memory: prior turns sent in the body as inputs.history so
        // follow-up questions keep context across messages.
        const body = (Array.isArray(o.history) && o.history.length)
            ? { history: o.history }
            : null;
        const response = await client.post(
            `/api/v1/workflows/${encodeURIComponent(workflowName)}/execute`,
            body,
            { params },
        );
        return response.data;
    },

    /**
     * Stream workflow execution events (SSE)
     */
    streamWorkflowExecution(workflowName) {
        return new EventSource(`${AGENT_API_URL}/api/v1/workflows/${encodeURIComponent(workflowName)}/stream`);
    },

    /**
     * Run an agent with a chat query and stream live progress over SSE.
     *
     * The SYNCHRONOUS execute response is the authoritative source of the final
     * answer (the by-name SSE stream can miss the terminal event on fast runs,
     * which previously surfaced as a bogus "stream connection error"). The SSE
     * stream is opened only for live progress and is fully best-effort — its
     * errors never fail the turn.
     *
     * handlers: { onToolCall(name,args), onToolResult(name,result), onToken(t),
     *             onNode(nodeId,status), onStatus(msg), onError(err) }
     * Returns: Promise<{ finalAnswer, tokens, raw }>
     */
    async runAgentStream(workflowName, userQuery, handlers = {}) {
        const h = handlers || {};

        // 1) Best-effort live progress via SSE (never fatal).
        let es = null;
        try {
            es = this.streamWorkflowExecution(workflowName);
            // ExecutionEvent serializes as { event_type, data: {...}, timestamp };
            // unwrap the nested `data` so handlers can read fields (tool, token,
            // node_id, message) directly. Flat events (connected/stream_end) with
            // no nested object pass through unchanged.
            const parse = (evt) => {
                try {
                    const o = JSON.parse(evt.data);
                    return (o && typeof o.data === 'object' && o.data) ? { ...o, ...o.data } : o;
                } catch { return {}; }
            };
            es.onmessage = (evt) => { const d = parse(evt); if (d && d.message) h.onStatus && h.onStatus(d.message); };
            const on = (type, cb) => es.addEventListener(type, cb);
            on('tool_call',    (e) => { const d = parse(e); const nm = d.tool || d.name; if (nm) h.onToolCall && h.onToolCall(nm, d.args || {}); });
            on('tool_started', (e) => { const d = parse(e); const nm = d.tool || d.name; if (nm) h.onToolCall && h.onToolCall(nm, d.args || {}); });
            on('tool_result',  (e) => { const d = parse(e); const nm = d.tool || d.name; if (nm) h.onToolResult && h.onToolResult(nm, d.result || d.output); });
            on('llm_token',    (e) => { const d = parse(e); h.onToken && h.onToken(d.token || d.text || ''); });
            on('node_started',   (e) => { const d = parse(e); h.onNode && h.onNode(d.node_id || d.nodeId, 'started'); });
            on('node_completed', (e) => { const d = parse(e); h.onNode && h.onNode(d.node_id || d.nodeId, 'completed'); });
            on('agent_progress', (e) => { const d = parse(e); h.onStatus && h.onStatus(d.message || ''); });
            on('token_usage_delta', (e) => {
                const d = parse(e);
                h.onTokens && h.onTokens({
                    input: d.input_tokens || 0,
                    output: d.output_tokens || 0,
                    total: d.total_tokens || ((d.input_tokens || 0) + (d.output_tokens || 0)),
                });
            });
            on('hitl_pause', (e) => {
                const d = parse(e);
                // Tool-approval gate: surface tool + args so the user can approve/deny.
                h.onHitlPause && h.onHitlPause({
                    executionId: d.execution_id,
                    requestId: d.request_id,
                    tool: d.tool,
                    args: d.args,
                    type: d.type,
                    message: d.message,
                });
            });
            es.onerror = () => { /* progress stream dropped — non-fatal; the sync result still resolves */ };
        } catch { /* SSE unavailable — proceed without live progress */ }

        // 2) Synchronous run → the response carries the authoritative result.
        try {
            const data = await this.executeWorkflow(workflowName, {
                background: false, userQuery, history: h.history,
            });
            if (data && data.status === 'already_running') {
                throw new Error(`Agent "${workflowName}" is already running. Wait for it to finish.`);
            }
            const finalAnswer = extractFinalAnswer(data);
            const tokens = extractTokens(data);
            return { finalAnswer, tokens, raw: data };
        } finally {
            if (es) { try { es.close(); } catch { /* noop */ } }
        }
    },

    /**
     * Get execution history for a workflow
     */
    async getWorkflowExecutions(workflowName, limit = 50) {
        const response = await client.get(`/api/v1/workflows/${encodeURIComponent(workflowName)}/executions`, {
            params: { limit },
        });
        return response.data;
    },

    /**
     * Get all execution history
     */
    async listAllExecutions(limit = 100) {
        const response = await client.get('/api/v1/executions', {
            params: { limit },
        });
        return response.data;
    },

    async listActiveWorkflows() {
        const response = await client.get('/api/v1/executions/active');
        return response.data;
    },

    /**
     * Delete all execution history
     */
    async deleteAllExecutions() {
        const response = await client.delete('/api/v1/executions');
        return response.data;
    },

    /**
     * Check agent-api health
     */
    async getHealth() {
        const response = await client.get('/api/v1/health');
        return response.data;
    },

    /**
     * Detailed system status (scheduler, jobs, executions)
     */
    async getStatus() {
        const response = await client.get('/api/v1/status');
        return response.data;
    },

    /**
     * Application settings (timeouts, embedding config, etc.)
     */
    async getSettings() {
        const response = await client.get('/api/v1/settings');
        return response.data;
    },

    /**
     * Stream execution events by execution ID (SSE)
     */
    streamExecution(executionId) {
        return new EventSource(`${AGENT_API_URL}/api/v1/executions/${encodeURIComponent(executionId)}/stream`);
    },

    /**
     * Approve or reject a HITL pause request
     * @param {string} executionId - The execution ID waiting for approval
     * @param {string} requestId - The HITL request ID from the hitl_pause event
     * @param {boolean} approved - true to approve, false to reject
     * @param {string} [reason] - Optional rejection reason
     */
    async approveHITL(executionId, requestId, approved = true, reason = '') {
        const response = await client.post(`/api/v1/executions/${encodeURIComponent(executionId)}/approve`, {
            request_id: requestId,
            approved,
            reason,
        });
        return response.data;
    },

    /**
     * Clear in-memory data
     */
    async clearData(clearJobs = false) {
        const response = await client.post('/api/v1/clear', null, {
            params: { clear_jobs: clearJobs },
        });
        return response.data;
    },

    // ==================== MCP Configuration ====================

    /**
     * Get full MCP configuration
     */
    async getMCPConfig() {
        const response = await client.get('/api/v1/mcp-config');
        return response.data;
    },

    /**
     * Get all MCP servers
     */
    async getMCPServers() {
        const response = await client.get('/api/v1/mcp-config/servers');
        return response.data;
    },

    /**
     * Get a specific MCP server
     */
    async getMCPServer(serverName) {
        const response = await client.get(`/api/v1/mcp-config/servers/${encodeURIComponent(serverName)}`);
        return response.data;
    },

    /**
     * Create a new MCP server
     */
    async createMCPServer(serverData) {
        const response = await client.post('/api/v1/mcp-config/servers', serverData);
        return response.data;
    },

    /**
     * Update an MCP server
     */
    async updateMCPServer(serverName, updates) {
        const response = await client.put(`/api/v1/mcp-config/servers/${encodeURIComponent(serverName)}`, updates);
        return response.data;
    },

    /**
     * Delete an MCP server
     */
    async deleteMCPServer(serverName) {
        await client.delete(`/api/v1/mcp-config/servers/${encodeURIComponent(serverName)}`);
    },

    /**
     * Get MCP input values
     */
    async getMCPInputValues() {
        const response = await client.get('/api/v1/mcp-config/input-values');
        return response.data;
    },

    /**
     * Set MCP input values
     */
    async setMCPInputValues(values) {
        const response = await client.put('/api/v1/mcp-config/input-values', { values });
        return response.data;
    },

    /**
     * Update a single MCP input value
     */
    async updateMCPInputValue(inputId, value) {
        const response = await client.patch(`/api/v1/mcp-config/input-values/${encodeURIComponent(inputId)}`, { value });
        return response.data;
    },

    /**
     * Save full MCP configuration (for migration/import)
     */
    async saveMCPConfig(config) {
        const response = await client.post('/api/v1/mcp-config/save', config);
        return response.data;
    },

    // ==================== Certificate Management ====================

    /**
     * List all uploaded certificates
     */
    async listCertificates() {
        const response = await client.get('/api/v1/certificates');
        return response.data;
    },

    /**
     * Upload a certificate file
     */
    async uploadCertificate(file, name = null) {
        const formData = new FormData();
        formData.append('file', file);
        if (name) {
            formData.append('name', name);
        }
        const response = await client.post('/api/v1/certificates', formData, {
            headers: {
                'Content-Type': 'multipart/form-data',
            },
        });
        return response.data;
    },

    /**
     * Delete a certificate
     */
    async deleteCertificate(filename) {
        await client.delete(`/api/v1/certificates/${encodeURIComponent(filename)}`);
    },

    /**
     * Check if a certificate exists
     */
    async checkCertificateExists(filename) {
        const response = await client.get(`/api/v1/certificates/${encodeURIComponent(filename)}/exists`);
        return response.data;
    },

    /**
     * List repositories discovered under REPOS_BASE_PATH (the read-only
     * docker volume mount from the host's repo root). Used by the
     * Configure CodeAnalyzer Node UI to populate a discovery picker.
     *
     * Returns:
     *   {
     *     base_path: string,
     *     base_exists: boolean,
     *     repos: [{
     *       name: string,
     *       path: string,                 // container path
     *       is_git: boolean,
     *       detected_languages: string[], // e.g. ["python","typescript"]
     *       suggested_language: string,   // single best-default
     *       file_count_sample: number,    // capped at 200
     *     }]
     *   }
     *
     * Never throws when the mount is missing — ``base_exists=false`` and
     * ``repos=[]`` so the UI can render a configuration hint instead of
     * an error.
     *
     * @param {Object} [opts]
     * @param {boolean} [opts.refresh=false] - Bypass the backend's 60s
     *   TTL cache and force a fresh filesystem walk. Use after the
     *   user adds or removes a repo on the host.
     */
    async listCodeAnalyzerRepos({ refresh = false } = {}) {
        const params = refresh ? { refresh: 'true' } : undefined;
        const response = await client.get('/api/v1/code-analyzer/repos', { params });
        return response.data;
    },

    /**
     * List repositories that have already been indexed by the crawler
     * (stored in the ``repo_abstractions`` table).
     *
     * Returns:
     *   {
     *     count: number,
     *     repos: [{
     *       repo_name: string,
     *       files_indexed: number,
     *       model_id: string | null,
     *       generated_at: string | null,   // ISO-8601
     *     }]
     *   }
     *
     * Used by the "Code Analyzer" node config panel to let users select from
     * repos that are already crawled without needing REPOS_BASE_PATH mounted.
     */
    async listCrawlerRepos() {
        const response = await client.get('/api/v1/crawler/repos');
        return response.data;
    },

    /**
     * Force a full rebuild of a repository index (bypasses the skip-if-unchanged
     * SHA check). Returns the refreshed repo overview.
     */
    async reindexRepo(repo) {
        const response = await client.post(
            `/api/v1/crawler/index/${encodeURIComponent(repo)}`,
            { force: true }
        );
        return response.data;
    },

    /**
     * Delete the entire index for a repository (knowledge graph + abstractions).
     * Idempotent on the server: deleting an un-indexed repo is a no-op.
     */
    async deleteIndex(repo) {
        const response = await client.delete(
            `/api/v1/crawler/index/${encodeURIComponent(repo)}`
        );
        return response.data;
    },

    // ==================== Project Intelligence (read-only) ====================

    /**
     * Fetch the project brief + architecture overview for an indexed repo.
     * Returns the brief payload (e.g. { brief, architecture, domain_model }) or
     * an { error } shape when no intelligence has been generated yet.
     */
    async getRepoBrief(repo) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/brief`);
        return response.data;
    },

    /**
     * Fetch the coding standards (naming, layout, frameworks, error_handling)
     * inferred for an indexed repo.
     */
    async getRepoStandards(repo) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/standards`);
        return response.data;
    },

    /**
     * Fetch module docs for a repo. Without `path` returns the module list
     * (path + responsibility); with `path` returns that module's detail
     * (key_components / data_flow / depends_on).
     */
    async getRepoDocs(repo, path) {
        const params = path ? { path } : undefined;
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/docs`, { params });
        return response.data;
    },

    /**
     * Locate where a feature lives in a repo. `query` is optional.
     */
    async getRepoFeature(repo, query) {
        const params = query ? { query } : undefined;
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/feature`, { params });
        return response.data;
    },

    // ==================== Knowledge Graph & Codebase Explorer ====================

    /**
     * List all indexed files in a repository with parse stats.
     */
    async getRepoFiles(repo, language = null, withErrorsOnly = false, limit = 200) {
        const params = { limit };
        if (language) params.language = language;
        if (withErrorsOnly) params.with_errors_only = withErrorsOnly;
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/files`, { params });
        return response.data;
    },

    /**
     * Fetch all nodes and edges defined in a specific file.
     */
    async getFileNodes(repo, filePath) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/nodes`, {
            params: { file_path: filePath }
        });
        return response.data;
    },

    /**
     * Fetch metadata for a single knowledge graph node.
     */
    async getKGNode(repo, qualifiedName) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/node`, {
            params: { qualified_name: qualifiedName }
        });
        return response.data;
    },

    /**
     * Find callers of a symbol transitively up to a depth.
     */
    async getKGCallers(repo, symbol, depth = 2) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/callers`, {
            params: { symbol, depth }
        });
        return response.data;
    },

    /**
     * Find callees of a symbol transitively up to a depth.
     */
    async getKGCallees(repo, symbol, depth = 2) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/callees`, {
            params: { symbol, depth }
        });
        return response.data;
    },

    /**
     * Perform impact analysis on a symbol.
     */
    async getKGImpact(repo, symbol) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/impact`, {
            params: { symbol }
        });
        return response.data;
    },

    /**
     * Find references to a symbol grouped by relation kind.
     */
    async getKGReferences(repo, symbol, limit = 20) {
        const response = await client.get(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/references`, {
            params: { symbol, limit }
        });
        return response.data;
    },

    /**
     * Compute a deterministic impact tree for a list of modified files or symbols.
     */
    async getKGDiffImpact(repo, files = [], symbols = []) {
        const response = await client.post(`/api/v1/crawler/repos/${encodeURIComponent(repo)}/diff-impact`, {
            files,
            symbols
        });
        return response.data;
    },
};

export default agentApiClient;
