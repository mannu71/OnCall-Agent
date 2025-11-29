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

                // Discover tools
                try {
                    const toolsResp = await client.listTools();
                    this.tools[name] = (toolsResp?.tools || []).map(t => t.name);
                    console.log(`✅ Connected MCP Server: ${name} (${this.tools[name].length} tools)`);
                } catch (e) {
                    console.log(`✅ Connected MCP Server: ${name} (tools discovery failed)`);
                    this.tools[name] = [];
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
