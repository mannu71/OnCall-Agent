// generic-multi-step-workflow.js
import { StateGraph, MessagesAnnotation, END, START } from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { DynamicStructuredTool } from "@langchain/core/tools";
import { z } from "zod";
import { AIMessage } from "@langchain/core/messages";
import { MultiServerMCPClient } from "./multiserver-mcp-client.js";
import { buildMCPConfigFromJSON } from "./build-mcp-config.js";
import { loadLLM } from "./llm-loader.js";
import crypto from "crypto";

function makeCorrelationId() {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return `cid-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

/**
 * Create LangChain tools with validation, timeout, and structured logs.
 * - toolName format: server__tool
 */
function createLangChainTools(mcpClient, toolsByServer, opts = {}) {
  const tools = [];

  for (const [serverName, toolNames] of Object.entries(toolsByServer)) {
    for (const toolName of toolNames) {
      const uniqueToolName = `${serverName}__${toolName}`.replace(/[^a-zA-Z0-9_]/g, '_');

      // a minimal zod schema placeholder - you can replace per-tool with a real schema registry
      const schema = z.record(z.any()).describe("Parameters to pass to the MCP tool");

      const wrappedFunc = async (params = {}) => {
        const cid = params._cid || makeCorrelationId();
        const start = Date.now();
        // validate params (won't throw on unknown keys if schema is broad)
        try {
          schema.parse(params);
        } catch (e) {
          const errMsg = `Invalid params for ${uniqueToolName}: ${e.message}`;
          console.warn(JSON.stringify({ ts: new Date().toISOString(), event: "tool.validation.fail", tool: uniqueToolName, message: errMsg, cid }));
          // return structured error for agent to reason about
          return JSON.stringify({ tool_error: true, message: errMsg });
        }

        // perform call with mcp client's built-in retry/timeout
        try {
          console.log(JSON.stringify({ ts: new Date().toISOString(), event: "tool.call.start", tool: uniqueToolName, cid }));
          const res = await mcpClient.call(serverName, toolName, params);
          const duration = Date.now() - start;
          console.log(JSON.stringify({ ts: new Date().toISOString(), event: "tool.call.success", tool: uniqueToolName, cid, duration }));
          return typeof res === 'string' ? res : JSON.stringify(res, null, 2);
        } catch (err) {
          const duration = Date.now() - start;
          console.error(JSON.stringify({ ts: new Date().toISOString(), event: "tool.call.error", tool: uniqueToolName, cid, duration, message: err?.message }));
          // return structured error so agent can plan fallback
          return JSON.stringify({ tool_error: true, message: err?.message, tool: uniqueToolName });
        }
      };

      const langchainTool = new DynamicStructuredTool({
        name: uniqueToolName,
        description: `MCP Tool: ${toolName} on server ${serverName}. Use this to interact with ${serverName}. For database queries, pass {"sql": "SELECT ..."} (this agent enforces timeouts/retries).`,
        schema,
        func: wrappedFunc
      });

      tools.push(langchainTool);
    }
  }

  return tools;
}

function shouldContinue(state) {
  const messages = state.messages;
  const lastMessage = messages[messages.length - 1];

  if (lastMessage instanceof AIMessage && lastMessage.tool_calls?.length > 0) {
    return "tools";
  }
  return END;
}

export async function buildDynamicWorkflow(agentJSON, options = {}) {

  const mcpConfig = buildMCPConfigFromJSON(agentJSON);
  const mcpClient = new MultiServerMCPClient(mcpConfig, {
    defaultTimeout: options.defaultTimeout,
    defaultRetries: options.defaultRetries
  });
  await mcpClient.connectAll();

  const toolsByServer = mcpClient.getAvailableTools();
  console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.tools.discovered", toolsByServer }));

  const llmNode = agentJSON.nodes.find(n => n.type === "llm");
  const llm = loadLLM(llmNode);

  const agentNode = agentJSON.nodes.find(n => n.type === "agent");
  const agentInstructions = agentNode?.data?.instructions || "";

  const langchainTools = createLangChainTools(mcpClient, toolsByServer);
  console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.tools.created", count: langchainTools.length }));

  const llmWithTools = llm.bindTools(langchainTools);

  const systemPrompt = `You are an expert investigator and problem solver.

${agentInstructions}

AVAILABLE_TOOLS:
${Object.entries(toolsByServer).map(([server, tools]) =>
    tools.length > 0
        ? `- Server "${server}": ${tools.map(t => `${server}__${t}`).join(', ')}`
        : `- Server "${server}": (no tools available - skip)`
).join('\n')}

INSTRUCTIONS:
1. Analyze the user's question carefully
2. Use the available tools to gather information
3. For database queries, use SQL in the params (e.g., {"sql": "SELECT * FROM ..."})
4. Reason through the results step by step
5. If a tool returns { "tool_error": true, ... } consider fallback strategies
6. Provide a clear final answer with root cause analysis if applicable

Think step by step and use tools as needed to answer the question thoroughly.`;

  async function callModel(state) {
    const messages = state.messages;
    const messagesWithSystem = [
      { role: "system", content: systemPrompt },
      ...messages
    ];

    console.log(JSON.stringify({ ts: new Date().toISOString(), event: "agent.think.start" }));
    const response = await llmWithTools.invoke(messagesWithSystem);

    if (response.tool_calls?.length > 0) {
      console.log(JSON.stringify({ ts: new Date().toISOString(), event: "agent.plan.tool_calls", count: response.tool_calls.length }));
      response.tool_calls.forEach((tc, i) => console.log(JSON.stringify({ ts: new Date().toISOString(), event: "agent.plan.call", index: i + 1, name: tc.name })));
    }
    return { messages: [response] };
  }

  const toolNode = new ToolNode(langchainTools);

  const graph = new StateGraph(MessagesAnnotation)
    .addNode("agent", callModel)
    .addNode("tools", toolNode)
    .addEdge(START, "agent")
    .addConditionalEdges("agent", shouldContinue, ["tools", END])
    .addEdge("tools", "agent");

  const compiledGraph = graph.compile();
  console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.compiled" }));

  const workflow = {
    async invoke({ userQuery }) {
      const cid = makeCorrelationId();
      console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.invoke.start", userQuery: userQuery?.slice?.(0,200), cid }));

      const result = await compiledGraph.invoke({
        messages: [{ role: "user", content: userQuery }]
      });

      const messages = result.messages || [];
      const lastMessage = messages[messages.length - 1];
      const finalAnswer = lastMessage?.content || "No response generated";

      console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.invoke.done", cid }));
      return { userQuery, messages, finalAnswer, cid };
    }
  };

  return { workflow, mcpClient };
}
