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
        const available = Object.keys(mcpConfig);
        const toolsByServer = mcpClient.getAvailableTools();

        const res = await llm.invoke([
            {
                role: "system",
                content: `
You are an expert investigator.

Agent Instruction Code: ${agentInstructions}

Available MCP Servers and their tools:
${Object.entries(toolsByServer).map(([server, tools]) => 
    `- ${server}: ${tools.length > 0 ? tools.join(', ') : '(tools not listed)'}`
).join('\n')}

Plan the steps needed to answer the question.

Return ONLY valid JSON array (no markdown, no explanation):
[
  { "server": "<server>", "tool": "<tool>", "params": {} }
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
        return { steps: JSON.parse(content.trim()) };
    }

    // STEP 2 — EXECUTION
    async function runSteps(state) {
        const results = [];

        for (const step of state.steps) {
            try {
                const result = await mcpClient.call(step.server, step.tool, step.params);
                results.push({ step, result, success: true });
            } catch (err) {
                results.push({ step, error: err.message, success: false });
            }
        }

        return { results };
    }

    // STEP 3 — SUMMARIZE
    async function summarise(state) {
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
