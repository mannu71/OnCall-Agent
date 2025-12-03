// build-mcp-config.js
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// default path (same as you had)
const mcpConfigPath = path.resolve(__dirname, "../../data/config/mcp-servers.json");

function loadInputValues() {
  try {
    if (fs.existsSync(mcpConfigPath)) {
      const config = JSON.parse(fs.readFileSync(mcpConfigPath, "utf-8"));
      return config.inputValues || {};
    }
  } catch (err) {
    console.warn(`⚠️ Could not load MCP input values: ${err.message}`);
  }
  return {};
}

function resolveVariable(value, inputValues = {}) {
  if (typeof value !== 'string') return value;

  return value.replace(/\$\{(input|env):([^}]+)\}/g, (match, type, varName) => {
    if (type === 'input' && Object.prototype.hasOwnProperty.call(inputValues, varName)) {
      console.log(`✅ Resolved ${match} from config`);
      return String(inputValues[varName]);
    }

    // try multiple env variants
    const candidates = [
      varName,
      varName.toUpperCase(),
      varName.replace(/[:-]/g, '_'),
      varName.replace(/[:-]/g, '_').toUpperCase()
    ];

    for (const c of candidates) {
      if (process.env[c] !== undefined) {
        console.log(`✅ Resolved ${match} from environment variable ${c}`);
        return process.env[c];
      }
    }

    console.warn(`⚠️ Variable not found: ${match} - configure in data/config/mcp-servers.json or set env var ${varName.toUpperCase()}`);
    // leave unresolved value so caller can detect it
    return match;
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
    const mcp = node.data.mcpConfig || {};

    // validation
    if (!mcp.command) {
      console.warn(`⚠️ MCP server ${id} has no command configured - skipping`);
      continue;
    }

    config[id] = {
      command: resolveVariable(mcp.command, inputValues),
      args: resolveArgs(mcp.args || [], inputValues),
      cwd: mcp.cwd || process.cwd(),
      env: { ...(mcp.env || {}) },
      type: mcp.type || "stdio"
    };
  }

  return config;
}
