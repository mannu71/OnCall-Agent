import { StateGraph, Annotation, END, START } from "@langchain/langgraph";
import { MultiServerMCPClient } from "./multiserver-mcp-client.js";
import { buildMCPConfigFromJSON } from "./build-mcp-config.js";
import { loadLLM } from "./llm-loader.js";

export async function buildDynamicWorkflow(agentJSON) {

    // 1. MCP SERVERS
    const mcpConfig = buildMCPConfigFromJSON(agentJSON);
    const mcpClient = new MultiServerMCPClient(mcpConfig);
    await mcpClient.connectAll();

    // 2. LOAD LLM FROM JSON
    const llmNode = agentJSON.nodes.find(n => n.type === "llm");
    const llm = loadLLM(llmNode);

    // 3. Load Agent Instruction Text
    const agentNode = agentJSON.nodes.find(n => n.type === "agent");
    const agentInstructions = agentNode?.data?.instructions || "";

    // Define state schema using Annotation
    const StateAnnotation = Annotation.Root({
        userQuery: Annotation({ reducer: (_, b) => b, default: () => "" }),
        steps: Annotation({ reducer: (_, b) => b, default: () => [] }),
        results: Annotation({ reducer: (_, b) => b, default: () => [] }),
        finalAnswer: Annotation({ reducer: (_, b) => b, default: () => "" })
    });

    // STEP 1 — PLANNING
    async function planSteps(state) {
        console.log('\n📋 STEP 1: Planning investigation...');
        const available = Object.keys(mcpConfig);
        const toolsByServer = mcpClient.getAvailableTools();

        // Log available tools for debugging
        console.log('Available tools by server:');
        for (const [server, tools] of Object.entries(toolsByServer)) {
            console.log(`   ${server}: ${tools.length > 0 ? tools.join(', ') : '(none discovered)'}`);
        }

        const res = await llm.invoke([
            {
                role: "system",
                content: `
You are an expert investigator.

Agent Instruction Code: ${agentInstructions}

AVAILABLE MCP SERVERS AND TOOLS:
${Object.entries(toolsByServer).map(([server, tools]) =>
                    tools.length > 0 
                        ? `- ${server}: ${tools.join(', ')}`
                        : `- ${server}: (tools not discovered - skip this server)`
                ).join('\n')}

CRITICAL RULES:
1. ONLY use tools that are explicitly listed above
2. If a server shows "(tools not discovered)", DO NOT use that server
3. For postgres servers, the tool is usually "query" with params: { "sql": "SELECT ..." }
4. Do NOT invent tool names - use EXACTLY the names shown above

Plan the steps needed to answer the question.

Return ONLY valid JSON array (no markdown, no explanation):
[
  { "server": "<server>", "tool": "<exact_tool_name>", "params": { ... } }
]
                `
            },
            { role: "user", content: state.userQuery }
        ]);

        // Parse JSON from response, handling potential markdown code blocks
        let content = res.content;
        if (content.includes('```')) {
            content = content.replace(/```json?\n?/g, '').replace(/```/g, '');
        }
        const steps = JSON.parse(content.trim());
        console.log(`✅ Plan created with ${steps.length} steps:`);
        steps.forEach((step, i) => {
            console.log(`   ${i + 1}. [${step.server}] ${step.tool}`);
        });
        return { steps };
    }

    // STEP 2 — EXECUTION
    async function runSteps(state) {
        console.log('\n🔧 STEP 2: Executing plan...');
        const results = [];

        for (let i = 0; i < state.steps.length; i++) {
            const step = state.steps[i];
            console.log(`   ▶ Step ${i + 1}/${state.steps.length}: [${step.server}] ${step.tool}`);
            try {
                const result = await mcpClient.call(step.server, step.tool, step.params);
                console.log(`   ✅ Success`);
                results.push({ step, result, success: true });
            } catch (err) {
                console.log(`   ❌ Failed: ${err.message}`);
                results.push({ step, error: err.message, success: false });
            }
        }

        console.log(`\n✅ Execution complete: ${results.filter(r => r.success).length}/${results.length} steps succeeded`);
        return { results };
    }

    // STEP 3 — SUMMARIZE
    async function summarise(state) {
        console.log('\n📝 STEP 3: Generating summary...');
        const res = await llm.invoke([
            {
                role: "system",
                content: "Summarize everything and identify root cause clearly."
            },
            {
                role: "user",
                content: `
User query:
${state.userQuery}

Results:
${JSON.stringify(state.results, null, 2)}

Provide final root cause analysis and recommended fix.
                `
            }
        ]);

        return { finalAnswer: res.content };
    }

    // BUILD WORKFLOW GRAPH
    const graph = new StateGraph(StateAnnotation);

    graph.addNode("planSteps", planSteps);
    graph.addNode("runSteps", runSteps);
    graph.addNode("summarise", summarise);

    graph.addEdge(START, "planSteps");
    graph.addEdge("planSteps", "runSteps");
    graph.addEdge("runSteps", "summarise");
    graph.addEdge("summarise", END);

    return { workflow: graph.compile(), mcpClient };
}
