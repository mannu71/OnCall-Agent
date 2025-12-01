import { buildDynamicWorkflow } from "./generic-multi-step-workflow.js";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load config paths
const workflowsPath = path.resolve(__dirname, "../../data/config/workflows.json");
const llmConfigPath = path.resolve(__dirname, "../../data/config/llm-config.json");

/**
 * Load LLM configurations from Settings
 */
function loadLLMConfig() {
    try {
        if (fs.existsSync(llmConfigPath)) {
            const config = JSON.parse(fs.readFileSync(llmConfigPath, "utf-8"));
            return config.llms || {};
        }
    } catch (err) {
        console.warn("⚠️ Could not load LLM config:", err.message);
    }
    return {};
}

/**
 * Merge API key from LLM config into workflow's LLM node
 */
function mergeApiKeyFromConfig(agent, llmConfigs) {
    const llmNode = agent.nodes.find(n => n.type === "llm");
    if (!llmNode) return agent;

    // Try to find matching LLM config by name or model
    const llmName = llmNode.data.llmName || llmNode.data.label;
    const model = llmNode.data.model;
    
    // Look for exact name match first
    let matchingConfig = llmConfigs[llmName];
    
    // If not found, try to match by model name
    if (!matchingConfig) {
        for (const [name, config] of Object.entries(llmConfigs)) {
            if (config.model === model) {
                matchingConfig = config;
                break;
            }
        }
    }

    if (matchingConfig) {
        console.log(`🔑 Found API key for LLM: ${llmName || model}`);
        llmNode.data = {
            ...llmNode.data,
            apiKey: matchingConfig.apiKey,
            provider: matchingConfig.provider || llmNode.data.provider,
            endpoint: matchingConfig.endpoint,
            baseUrl: matchingConfig.baseUrl
        };
    }

    return agent;
}

async function main() {
    // Get workflow name from command line args, default to first one
    const workflowName = process.argv[2];
    const userQuery = process.argv[3];

    // Load workflows and LLM config
    const workflows = JSON.parse(fs.readFileSync(workflowsPath, "utf-8"));
    const llmConfigs = loadLLMConfig();
    
    // Find the specified workflow or use the first one
    let agent;
    if (workflowName) {
        agent = workflows.find(w => w.name === workflowName || w.id === workflowName);
        if (!agent) {
            console.error(`❌ Workflow not found: ${workflowName}`);
            console.log("Available workflows:");
            workflows.forEach(w => console.log(`  - ${w.name} (${w.id})`));
            process.exit(1);
        }
    } else {
        agent = workflows[0];
    }

    // Merge API key from LLM config
    agent = mergeApiKeyFromConfig(agent, llmConfigs);

    console.log(`🚀 Running workflow: ${agent.name}`);
    
    const { workflow, mcpClient } = await buildDynamicWorkflow(agent);

    const result = await workflow.invoke({
        userQuery
    });

    console.log("\n🔥 FINAL ANSWER:\n");
    console.log(result.finalAnswer);

    await mcpClient.disconnectAll();
}

main();
