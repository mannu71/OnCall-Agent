// multiserver-mcp-client.js
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

/**
 * Simple circuit breaker per server
 */
class CircuitBreaker {
  constructor({ failureThreshold = 5, cooldownMs = 30_000 } = {}) {
    this.failureThreshold = failureThreshold;
    this.cooldownMs = cooldownMs;
    this.failures = 0;
    this.openUntil = 0;
  }

  recordSuccess() {
    this.failures = 0;
    this.openUntil = 0;
  }

  recordFailure() {
    this.failures += 1;
    if (this.failures >= this.failureThreshold) {
      this.openUntil = Date.now() + this.cooldownMs;
    }
  }

  isOpen() {
    return Date.now() < this.openUntil;
  }

  getState() {
    return { failures: this.failures, openUntil: this.openUntil };
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Timeout helper using Promise.race
 */
function withTimeout(promise, ms, onTimeout) {
  if (ms == null) return promise;
  let timeout;
  const timeoutPromise = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      if (onTimeout) onTimeout();
      reject(new Error(`Operation timed out after ${ms}ms`));
    }, ms);
  });
  return Promise.race([promise.finally(() => clearTimeout(timeout)), timeoutPromise]);
}

export class MultiServerMCPClient {
  constructor(config = {}, opts = {}) {
    this.config = config;
    this.servers = {};
    this.tools = {}; // Map server -> tool names
    this.circuit = {}; // server -> CircuitBreaker
    this.defaultTimeout = opts.defaultTimeout ?? 900_000; // default 15 minutes for long DB queries
    this.defaultRetries = opts.defaultRetries ?? 2; // retry twice
    this.backoffBase = opts.backoffBase ?? 300; // ms
  }

  async connectAll() {
    for (const [name, cfg] of Object.entries(this.config)) {
      console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.connect.start", server: name }));

      try {
        const transport = new StdioClientTransport({
          command: cfg.command,
          args: cfg.args || [],
          env: { ...process.env, ...(cfg.env || {}) }
        });

        const client = new Client(
          { name, version: "1.0" },
          { 
            capabilities: {},
            requestTimeoutMs: this.defaultTimeout  // Override SDK's default 60s timeout
          }
        );

        // protect connect with timeout
        await withTimeout(client.connect(transport), 15_000, () => {
          console.warn(`⚠️ MCP connect timeout for ${name}`);
        });

        this.servers[name] = client;
        this.circuit[name] = new CircuitBreaker();

        // discover tools with small retry & timeout
        try {
          await sleep(500); // give server time to start
          const toolsResp = await withTimeout(client.listTools(), 8_000);
          this.tools[name] = (toolsResp?.tools || []).map(t => t.name);
          console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.connect.success", server: name, toolCount: this.tools[name].length }));
        } catch (e) {
          // fallback for known CloudWatch schema issue (backwards compatibility)
          if (name.includes('cloudwatch') && e.message && e.message.includes("can't resolve reference")) {
            console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.connect.fallback", server: name, reason: e.message }));
            this.tools[name] = [
              'get_metric_data',
              'get_metric_metadata',
              'get_recommended_metric_alarms',
              'analyze_metric',
              'get_active_alarms',
              'get_alarm_history',
              'describe_log_groups',
              'analyze_log_group',
              'execute_log_insights_query',
              'get_logs_insight_query_results',
              'cancel_logs_insight_query'
            ];
          } else {
            console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.connect.partial", server: name, reason: e.message }));
            this.tools[name] = [];
          }
        }
      } catch (err) {
        console.error(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.connect.fail", server: name, message: err?.message }));
      }
    }
  }

  /**
   * Main call helper - retries + timeout + circuit breaker
   * @param {string} serverName
   * @param {string} toolName
   * @param {object} params
   * @param {object} options - optional timeout/retries
   */
  async call(serverName, toolName, params = {}, options = {}) {
    const client = this.servers[serverName];
    if (!client) throw new Error(`Server not found: ${serverName}`);

    const circuit = this.circuit[serverName];
    if (circuit?.isOpen()) {
      const state = circuit.getState();
      throw new Error(`Circuit open for ${serverName} (failures=${state.failures}, openUntil=${new Date(state.openUntil).toISOString()})`);
    }

    const timeoutMs = options.timeout ?? this.defaultTimeout;
    const retries = options.retries ?? this.defaultRetries;

    const attemptCall = async (attempt) => {
      const start = Date.now();
      try {
        console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.call.start", server: serverName, tool: toolName, attempt }));
        // Pass timeout directly to SDK's callTool to override the default 60s timeout
        const raw = await client.callTool(
          { name: toolName, arguments: params },
          undefined,  // resultSchema - use default
          { 
            timeout: timeoutMs,
            maxTotalTimeout: timeoutMs,
            resetTimeoutOnProgress: true
          }
        );
        const duration = Date.now() - start;
        circuit?.recordSuccess();
        // try to extract text safely
        const content = raw?.content;
        const maybeText = Array.isArray(content) && content[0] && (content[0].text || content[0].json) ? (content[0].text ?? content[0].json) : raw;
        console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.call.success", server: serverName, tool: toolName, duration }));
        if (typeof maybeText === 'string') return maybeText;
        try {
          return JSON.stringify(maybeText, null, 2);
        } catch (e) {
          return String(maybeText);
        }
      } catch (err) {
        const duration = Date.now() - start;
        circuit?.recordFailure();
        console.error(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.call.error", server: serverName, tool: toolName, attempt, duration, message: err?.message }));
        throw err;
      }
    };

    let lastErr;
    for (let i = 0; i <= retries; i++) {
      try {
        return await attemptCall(i + 1);
      } catch (err) {
        lastErr = err;
        // if circuit opened as result of failure, break early
        if (this.circuit[serverName]?.isOpen()) break;
        const backoff = this.backoffBase * Math.pow(2, i);
        await sleep(backoff);
      }
    }

    throw lastErr;
  }

  getAvailableTools() {
    return this.tools;
  }

  async disconnectAll() {
    for (const [name, client] of Object.entries(this.servers)) {
      console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.disconnect.start", server: name }));
      try {
        await client.close();
        console.log(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.disconnect.success", server: name }));
      } catch (e) {
        console.warn(JSON.stringify({ ts: new Date().toISOString(), event: "mcp.disconnect.fail", server: name, message: e?.message }));
      }
    }
  }
}
