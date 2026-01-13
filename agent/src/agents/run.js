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
    if (!llmNode) {
        console.warn("⚠️ No LLM node found in workflow");
        return agent;
    }

    // Try to find matching LLM config by name or model
    const llmName = llmNode.data.llmName || llmNode.data.label;
    const model = llmNode.data.model;
    const provider = (llmNode.data.provider || "OpenAI").toLowerCase();
    
    console.log(`🔍 Looking for LLM config: name="${llmName}", model="${model}"`);
    console.log(`📋 Available LLM configs: ${Object.keys(llmConfigs).join(", ") || "(none)"}`);
    
    // Look for exact name match first
    let matchingConfig = llmConfigs[llmName];
    
    // If not found, try to match by model name
    if (!matchingConfig) {
        for (const [name, config] of Object.entries(llmConfigs)) {
            if (config.model === model) {
                matchingConfig = config;
                console.log(`🔗 Matched by model: "${model}" -> "${name}"`);
                break;
            }
        }
    }

    // If still not found, try to find any config with same provider
    if (!matchingConfig) {
        for (const [name, config] of Object.entries(llmConfigs)) {
            if ((config.provider || "").toLowerCase() === provider) {
                matchingConfig = config;
                console.log(`🔗 Matched by provider: "${provider}" -> "${name}"`);
                break;
            }
        }
    }

    if (matchingConfig && matchingConfig.apiKey) {
        console.log(`🔑 Found API key for LLM: ${llmName || model}`);
        llmNode.data = {
            ...llmNode.data,
            apiKey: matchingConfig.apiKey,
            provider: matchingConfig.provider || llmNode.data.provider,
            endpoint: matchingConfig.endpoint || llmNode.data.endpoint,
            baseUrl: matchingConfig.baseUrl || llmNode.data.baseUrl
        };
    } else {
        // Check for environment variables as fallback
        const envKey = getEnvApiKey(provider);
        if (envKey) {
            console.log(`🔑 Using API key from environment variable for ${provider}`);
            llmNode.data = {
                ...llmNode.data,
                apiKey: envKey
            };
        } else {
            console.warn(`⚠️ No API key found for LLM: ${llmName || model}. Configure in Settings or set environment variable.`);
        }
    }

    return agent;
}

/**
 * Get API key from environment variable based on provider
 */
function getEnvApiKey(provider) {
    switch (provider.toLowerCase()) {
        case "openai":
            return process.env.OPENAI_API_KEY;
        case "anthropic":
            return process.env.ANTHROPIC_API_KEY;
        case "google":
        case "gemini":
            return process.env.GOOGLE_API_KEY;
        case "groq":
            return process.env.GROQ_API_KEY;
        case "azure openai":
        case "azure":
            return process.env.AZURE_OPENAI_API_KEY;
        default:
            return null;
    }
}

async function main() {
    // Get workflow name from command line args, default to first one
    const workflowName = process.argv[2];
    const userQuery = process.argv[3];

    console.log(JSON.stringify({ ts: new Date().toISOString(), event: "run.start", workflowName }));

    // Load workflows and LLM config
    const workflows = JSON.parse(fs.readFileSync(workflowsPath, "utf-8"));
    const llmConfigs = loadLLMConfig();
    
    // Find the specified workflow or use the first one
    let agent;
    if (workflowName) {
        agent = workflows.find(w => w.name === workflowName || w.id === workflowName);
        if (!agent) {
            console.error(JSON.stringify({ ts: new Date().toISOString(), event: "run.error", message: `Workflow not found: ${workflowName}` }));
            console.log("Available workflows:");
            workflows.forEach(w => console.log(`  - ${w.name} (${w.id})`));
            process.exit(1);
        }
    } else {
        agent = workflows[0];
    }

    // Merge API key from LLM config
    agent = mergeApiKeyFromConfig(agent, llmConfigs);

    // Inject current date into agent instructions
    const now = new Date();
    const currentDateTime = now.toISOString();
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const yesterdayDateTime = yesterday.toISOString();
    
    // Replace placeholders in instructions if they exist
    const agentNode = agent.nodes.find(n => n.type === "agent");
    if (agentNode && agentNode.data.instructions) {
        let instructions = agentNode.data.instructions;
        
        // Replace date placeholders
        instructions = instructions.replace(/{{current_date_time}}/g, currentDateTime);
        instructions = instructions.replace(/{{yesterday_date_time}}/g, yesterdayDateTime);
        instructions = instructions.replace(/{{current_date}}/g, now.toISOString().split('T')[0]);
        
        agentNode.data.instructions = instructions;
    }

    console.log(JSON.stringify({ ts: new Date().toISOString(), event: "run.workflow.load", name: agent.name }));
    
    const { workflow, mcpClient } = await buildDynamicWorkflow(agent);

    console.log(JSON.stringify({ ts: new Date().toISOString(), event: "run.workflow.invoke", query: userQuery?.slice(0, 100) }));
    
    const result = await workflow.invoke({
        userQuery
    });

    console.log(JSON.stringify({ ts: new Date().toISOString(), event: "run.complete" }));
    console.log("\n🔥 FINAL ANSWER:\n");
    console.log(result.finalAnswer);

    await mcpClient.disconnectAll();
}

main();
