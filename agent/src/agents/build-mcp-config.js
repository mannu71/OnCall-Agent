import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load input values from mcp-servers.json
const mcpConfigPath = path.resolve(__dirname, "../../data/config/mcp-servers.json");

function loadInputValues() {
    try {
        if (fs.existsSync(mcpConfigPath)) {
            const config = JSON.parse(fs.readFileSync(mcpConfigPath, "utf-8"));
            return config.inputValues || {};
        }
    } catch (err) {
        console.warn("⚠️ Could not load MCP input values:", err.message);
    }
    return {};
}

/**
 * Resolve variables like ${input:var_name} or ${env:VAR_NAME}
 * First checks inputValues from config, then environment variables
 */
function resolveVariable(value, inputValues = {}) {
    if (typeof value !== 'string') return value;
    
    return value.replace(/\$\{(input|env):([^}]+)\}/g, (match, type, varName) => {
        // First try input values from config
        if (type === 'input' && inputValues[varName]) {
            console.log(`✅ Resolved ${varName} from config`);
            return inputValues[varName];
        }
        
        // Fall back to environment variables
        const envValue = process.env[varName] 
            || process.env[varName.toUpperCase()] 
            || process.env[varName.replace(/[:-]/g, '_').toUpperCase()];
        
        if (envValue) {
            console.log(`✅ Resolved ${varName} from environment`);
            return envValue;
        }
        
        console.warn(`⚠️ Variable not found: ${match} - configure in Settings or set env var ${varName.toUpperCase()}`);
        return match; // Keep original if not found
    });
}

function resolveArgs(args, inputValues) {
    if (!Array.isArray(args)) return args;
    return args.map(arg => resolveVariable(arg, inputValues));
}

export function buildMCPConfigFromJSON(agentJSON) {
    const config = {};
    const inputValues = loadInputValues();

    for (const node of agentJSON.nodes) {
        if (node.type !== "tool") continue;
        if (node.data.toolType !== "mcp-server") continue;

        const id = node.data.label;
        const mcp = node.data.mcpConfig;

        config[id] = {
            command: mcp.command,
            args: resolveArgs(mcp.args, inputValues),
            cwd: process.cwd(),
            env: mcp.env || {},
            type: mcp.type || "stdio"
        };
    }

    return config;
}
