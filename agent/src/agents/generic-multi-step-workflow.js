// generic-multi-step-workflow.js
import { StateGraph, MessagesAnnotation, END, START } from "@langchain/langgraph";
import { ToolNode } from "@langchain/langgraph/prebuilt";
import { DynamicStructuredTool, tool } from "@langchain/core/tools";
import { z } from "zod";
import { AIMessage } from "@langchain/core/messages";
import { MultiServerMCPClient } from "./multiserver-mcp-client.js";
import { buildMCPConfigFromJSON } from "./build-mcp-config.js";
import { loadLLM } from "./llm-loader.js";
import { cloudWatchTools } from "../tools/cloudwatch/cloudwatch-langchain-tools.js";
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

      const wrappedFunc = async (params = {}) => {
        const cid = params._cid || makeCorrelationId();
        const start = Date.now();

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

      // Use the tool() helper with a simple Zod object schema (Zod v3/v4 compatible)
      const langchainTool = tool(wrappedFunc, {
        name: uniqueToolName,
        description: `MCP Tool: ${toolName} on server ${serverName}. Use this to interact with ${serverName}. Pass parameters as a JSON object.`,
        schema: z.object({}).passthrough() // Accept any object with any properties
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

  const mcpTools = createLangChainTools(mcpClient, toolsByServer);
  const langchainTools = [...mcpTools, ...cloudWatchTools];
  
  console.log(JSON.stringify({ 
    ts: new Date().toISOString(), 
    event: "workflow.tools.created", 
    count: langchainTools.length,
    mcp: mcpTools.length,
    cloudwatch: cloudWatchTools.length
  }));

  const llmWithTools = llm.bindTools(langchainTools);

  const systemPrompt = `You are a tool-using agent.

${agentInstructions}

AVAILABLE_TOOLS:
${Object.entries(toolsByServer).map(([server, tools]) =>
    tools.length > 0
        ? `- Server "${server}": ${tools.map(t => `${server}__${t}`).join(', ')}`
        : `- Server "${server}": (no tools available - skip)`
).join('\n')}

RULES FOR TOOL USE:
- If the user asks for ANY factual information, run an MCP tool.
- NEVER guess. ALWAYS call a tool when information is external.
- When calling a tool, produce ONLY a tool call and no natural language.
- Pass parameters as a JSON object { ... }. NEVER pass null, undefined, or empty parameters.
- After receiving tool results, analyze them and either:
   (a) call another tool with DIFFERENT parameters, or
   (b) provide a final answer.
- If a tool fails with {"tool_error": true...}, check the error message:
   * If the error indicates missing/invalid parameters (e.g., "null or undefined"), DO NOT retry the same tool with the same parameters.
   * If you cannot provide valid parameters, explain to the user what information is needed.
   * If the circuit is open (server unavailable), try a different server or inform the user.
- If you've tried the same tool 2+ times with errors, STOP and provide a final answer explaining the issue.

NEVER answer directly when a tool could help you answer the question, BUT ALSO never retry the same failing tool call infinitely.`;

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

  const MAX_ITERATIONS = 15; // Prevent infinite loops

  const workflow = {
    async invoke({ userQuery }) {
      const cid = makeCorrelationId();
      console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.invoke.start", userQuery: userQuery?.slice?.(0,200), cid }));

      try {
        const result = await compiledGraph.invoke(
          { messages: [{ role: "user", content: userQuery }] },
          { recursionLimit: MAX_ITERATIONS }
        );

        const messages = result.messages || [];
        const lastMessage = messages[messages.length - 1];
        const finalAnswer = lastMessage?.content || "No response generated";

        console.log(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.invoke.done", cid, messageCount: messages.length }));
        return { userQuery, messages, finalAnswer, cid };
      } catch (err) {
        console.error(JSON.stringify({ ts: new Date().toISOString(), event: "workflow.invoke.error", cid, message: err?.message }));
        throw err;
      }
    }
  };

  return { workflow, mcpClient };
}
