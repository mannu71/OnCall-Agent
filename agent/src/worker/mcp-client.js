import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import logger from '../shared/logger.js';
import { DEFAULT_CLIENT_META } from '../shared/constants.js';

// Constants for better maintainability
const DEFAULT_DISCOVERY_TIMEOUT_MS = 10_000;
const DEFAULT_RAW_CAPTURE_TIMEOUT_MS = 5_000;
const DEFAULT_CALL_TIMEOUT_MS = 900_000; // 15 minutes for long DB queries

/**
 * Hybrid functional MCP client factory
 * - closure-based state
 * - robust discovery with SDK + raw transport capture fallback
 */
export function createMCPClient(serverConfig, opts = {}) {
  // config & defaults
  const meta = opts.clientMeta || DEFAULT_CLIENT_META;
  const discoveryTimeoutMs = opts.discoveryTimeoutMs ?? DEFAULT_DISCOVERY_TIMEOUT_MS;
  const rawCaptureTimeoutMs = opts.rawCaptureTimeoutMs ?? DEFAULT_RAW_CAPTURE_TIMEOUT_MS;
  const callTimeoutMs = opts.callTimeoutMs ?? DEFAULT_CALL_TIMEOUT_MS;

  // internal state
  let client = null;
  let transport = null;
  let isConnected = false;
  const tools = new Map();

  // keep original onmessage if we replace it during capture
  let _originalTransportOnMessage;

  /* -----------------------
     Helpers
     ----------------------- */

  const _safeClose = async () => {
    try { if (client?.close) await client.close(); } catch (e) { logger.debug('client.close failed:', e?.message || e); }
    try { if (transport?.close) await transport.close(); } catch (e) { logger.debug('transport.close failed:', e?.message || e); }
    isConnected = false;
  };

  const _storeToolsFromArray = (arr = [], assumeInvalidSchema = false) => {
    if (!Array.isArray(arr)) return;
    for (const t of arr) {
      try {
        const name = t?.name || t?.id;
        if (!name) continue;
        tools.set(name, {
          name,
          description: t.description || 'No description',
          inputSchema: assumeInvalidSchema ? {} : (t.inputSchema || {}),
          hasValidSchema: !assumeInvalidSchema && !!t.inputSchema,
        });
        logger.debug(`Stored tool: ${name}`);
      } catch (err) {
        logger.warn('Failed to store tool:', err?.message || err);
      }
    }
  };

  const _extractToolsArray = (raw) => {
    if (!raw) return null;
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw.tools)) return raw.tools;
    if (raw.result && Array.isArray(raw.result.tools)) return raw.result.tools;
    return null;
  };

  const _requestWithTimeout = async (payload, timeoutMs = discoveryTimeoutMs) => {
    return await Promise.race([
      client.request(payload, { meta: {} }).catch(err => {
        logger.debug('client.request error (ignored):', err?.message || err);
        return null;
      }),
      new Promise((_, rej) => setTimeout(() => rej(new Error('request timeout')), timeoutMs))
    ]);
  };

  const _captureTransportResponse = (timeoutMs = rawCaptureTimeoutMs) => {
    if (!transport) return Promise.reject(new Error('No transport to capture from'));

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error('capture timeout'));
      }, timeoutMs);

      let messageBuffer = [];

      function cleanup() {
        clearTimeout(timer);
        if (_originalTransportOnMessage !== undefined) {
          try { transport.onmessage = _originalTransportOnMessage; } catch (_) {}
          _originalTransportOnMessage = undefined;
        }
      }

      try {
        // Store original handler
        if (typeof transport.onmessage === 'function') {
          _originalTransportOnMessage = transport.onmessage;
        }

        // Replace with our interceptor
        transport.onmessage = (message) => {
          try {
            messageBuffer.push(message);
            
            // Try to parse and check for tools
            const payload = (typeof message === 'string') ? JSON.parse(message) : message;
            
            // Check if this message contains tools list response
            if (payload?.result?.tools || payload?.tools) {
              cleanup();
              resolve(payload.result || payload);
              return;
            }
            
            // Call original handler to maintain normal SDK flow
            if (_originalTransportOnMessage) {
              try { 
                _originalTransportOnMessage.call(transport, message); 
              } catch (e) {
                logger.debug('Original handler error (ignored):', e?.message);
              }
            }
          } catch (parseErr) {
            // If parse fails, still call original handler
            if (_originalTransportOnMessage) {
              try { 
                _originalTransportOnMessage.call(transport, message); 
              } catch (e) {
                logger.debug('Original handler error (ignored):', e?.message);
              }
            }
          }
        };

        // Trigger the request AFTER setting up the interceptor
        client.request({ method: 'tools/list' }, { meta: {} }).catch(err => {
          logger.debug('Request error (expected with schema issues):', err?.message);
        });

      } catch (err) {
        cleanup();
        reject(err);
      }
    });
  };

  /* -----------------------
     Public API
     ----------------------- */

  const connect = async () => {
    logger.info(`Connecting to MCP server: ${serverConfig?.label || 'unknown'}`);

    const cfg = serverConfig?.mcpConfig;
    if (!cfg?.command) {
      const msg = `Missing mcpConfig.command for server: ${serverConfig?.label || 'unknown'}`;
      logger.error(msg);
      throw new Error(msg);
    }

    transport = new StdioClientTransport({
      command: cfg.command,
      args: cfg.args || [],
      env: { ...process.env, ...(cfg.env || {}) }
    });

    client = new Client(meta, { 
      capabilities: {},
      requestTimeoutMs: callTimeoutMs  // Override SDK's default 60s timeout
    });

    try {
      await client.connect(transport);
      isConnected = true;
      logger.info(`Connected to MCP server: ${serverConfig.label}`);

      // best-effort discovery
      try {
        await discoverTools();
      } catch (err) {
        logger.warn(`Tool discovery failed (non-fatal): ${err?.message || err}`);
      }

      return true;
    } catch (err) {
      logger.error(`Failed to connect to MCP server [${serverConfig.label}]:`, err);
      await _safeClose();
      throw err;
    }
  };

  /**
   * Robust discovery:
   * 1) Try SDK listTools() first (best case - has validation)
   * 2) If SDK fails, use transport message capture to bypass schema validation
   * 3) Store tools even when schemas are invalid
   */
  const discoverTools = async () => {
    // Attempt 1: SDK with validation
    try {
      const resp = await client.listTools();
      if (resp?.tools && Array.isArray(resp.tools)) {
        _storeToolsFromArray(resp.tools, false);
        logger.info(`Discovered ${tools.size} tools from ${serverConfig.label} (sdk)`);
        return;
      }
    } catch (sdkErr) {
      logger.warn('SDK tool discovery failed:', sdkErr?.message || sdkErr);
    }

    // Attempt 2: Raw transport capture (bypasses schema validation)
    logger.info('Attempting raw transport capture to bypass schema validation...');
    try {
      const captured = await _captureTransportResponse(rawCaptureTimeoutMs);
      const arr = _extractToolsArray(captured);
      
      if (arr && arr.length > 0) {
        _storeToolsFromArray(arr, true); // assumeInvalidSchema = true
        logger.info(`Discovered ${tools.size} tools from ${serverConfig.label} (raw capture - schema validation bypassed)`);
        return;
      }
    } catch (captureErr) {
      logger.warn('Transport capture failed:', captureErr?.message || captureErr);
    }

    logger.info(`Tool discovery completed for ${serverConfig.label} (no tools found)`);
  };

  const callTool = async (toolName, params = {}) => {
    if (!isConnected) throw new Error(`MCP server not connected: ${serverConfig?.label}`);

    if (!tools.has(toolName)) {
      logger.warn(`Tool ${toolName} not discovered — attempting to call anyway`);
    }

    try {
      logger.info(`Calling tool: ${toolName} on ${serverConfig.label} (timeout: ${callTimeoutMs/1000}s)`);
      logger.debug('Tool params:', params);

      // Pass timeout directly to SDK's callTool to override the default 60s timeout
      const response = await client.callTool(
        { name: toolName, arguments: params },
        undefined,  // resultSchema - use default
        { timeout: callTimeoutMs, maxTotalTimeout: callTimeoutMs }
      );

      logger.info(`Tool call successful: ${toolName}`);
      return response;
    } catch (err) {
      logger.error(`Tool call failed [${toolName}]:`, err);
      throw err;
    }
  };

  const getTools = () => Array.from(tools.values());
  const hasTool = (name) => tools.has(name);

  const disconnect = async () => {
    await _safeClose();
    logger.info(`Disconnected from MCP server: ${serverConfig?.label}`);
  };

  // expose the API
  return {
    connect,
    discoverTools,
    callTool,
    getTools,
    hasTool,
    disconnect,
    // for tests and advanced usage:
    _internals: { get client() { return client; }, get transport() { return transport; }, isConnected: () => isConnected }
  };
}

/* -----------------------
   Manager (functional)
   ----------------------- */

export function createMCPClientManager() {
  const clients = new Map();

  const connectAll = async (toolNodes = []) => {
    const promises = toolNodes.map(async (node) => {
      const client = createMCPClient(node.data);
      try {
        await client.connect();
        clients.set(node.id, client);
        return { id: node.id, success: true };
      } catch (err) {
        logger.error(`Failed to connect to ${node.data?.label || node.id}:`, err);
        return { id: node.id, success: false, error: err };
      }
    });

    const results = await Promise.all(promises);
    const successCount = results.filter(r => r.success).length;
    logger.info(`Connected to ${successCount}/${toolNodes.length} MCP servers`);

    if (successCount === 0) {
      throw new Error('Failed to connect to any MCP servers');
    }

    return results;
  };

  const getClient = (id) => clients.get(id);
  const disconnectAll = async () => {
    await Promise.allSettled(Array.from(clients.values()).map(c => c.disconnect()));
    clients.clear();
    logger.info('Disconnected all MCP clients');
  };

  return { connectAll, getClient, disconnectAll };
}
