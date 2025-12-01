import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

export class MultiServerMCPClient {
    constructor(config = {}) {
        this.config = config;
        this.servers = {};
        this.tools = {};  // Map server -> tool names
    }

    async connectAll() {
        for (const [name, cfg] of Object.entries(this.config)) {
            console.log(`🔌 Starting MCP Server: ${name}`);

            try {
                const transport = new StdioClientTransport({
                    command: cfg.command,
                    args: cfg.args || [],
                    env: { ...process.env, ...(cfg.env || {}) }
                });

                const client = new Client(
                    { name, version: "1.0" },
                    { capabilities: {} }
                );

                await client.connect(transport);
                this.servers[name] = client;

                // Discover tools with retry for slow-starting servers
                try {
                    // Give the server a moment to fully initialize
                    await new Promise(resolve => setTimeout(resolve, 500));
                    
                    const toolsResp = await client.listTools();
                    this.tools[name] = (toolsResp?.tools || []).map(t => t.name);
                    console.log(`✅ Connected MCP Server: ${name} (${this.tools[name].length} tools)`);
                } catch (e) {
                    // CloudWatch server has a JSON schema issue - provide known tools as fallback
                    if (name.includes('cloudwatch') && e.message.includes("can't resolve reference")) {
                        console.log(`✅ Connected MCP Server: ${name} (using known CloudWatch tools)`);
                        // Actual tool names from AWS CloudWatch MCP Server documentation
                        this.tools[name] = [
                            // CloudWatch Metrics tools
                            'get_metric_data',
                            'get_metric_metadata',
                            'get_recommended_metric_alarms',
                            'analyze_metric',
                            // CloudWatch Alarms tools
                            'get_active_alarms',
                            'get_alarm_history',
                            // CloudWatch Logs tools
                            'describe_log_groups',
                            'analyze_log_group',
                            'execute_log_insights_query',
                            'get_logs_insight_query_results',
                            'cancel_logs_insight_query'
                        ];
                    } else {
                        console.log(`✅ Connected MCP Server: ${name} (tools discovery failed: ${e.message})`);
                        this.tools[name] = [];
                    }
                }
            } catch (err) {
                console.error(`❌ Failed to connect to MCP server ${name}:`, err.message);
            }
        }
    }

    async call(serverName, toolName, params = {}) {
        const client = this.servers[serverName];
        if (!client) throw new Error(`❌ Server not found: ${serverName}`);

        console.log(`🚀 Executing ${toolName} on ${serverName}`);
        
        try {
            const res = await client.callTool({ name: toolName, arguments: params });
            return res?.content?.[0]?.text || JSON.stringify(res);
        } catch (err) {
            console.error(`❌ Tool call failed:`, err.message);
            throw err;
        }
    }

    getAvailableTools() {
        return this.tools;
    }

    async disconnectAll() {
        for (const [name, client] of Object.entries(this.servers)) {
            console.log(`🔻 Disconnecting MCP: ${name}`);
            try {
                await client.close();
            } catch (e) {
                console.log(`⚠️ Close failed for ${name}:`, e.message);
            }
        }
    }
}
