import React from 'react';
import NodeGenerator from './NodeGenerator.jsx';
import OutputNode from './OutputNode.jsx';
import OrchestratorNode from './OrchestratorNode.jsx';

// Create wrapper components for each node type
const AgentNode = (props) => React.createElement(NodeGenerator, { ...props, type: "agent" });
const LLMNode = (props) => React.createElement(NodeGenerator, { ...props, type: "llm" });
const DatabaseNode = (props) => React.createElement(NodeGenerator, { ...props, type: "database" });
const TeamsNode = (props) => React.createElement(NodeGenerator, { ...props, type: "teams" });
const ToolNode = (props) => React.createElement(NodeGenerator, { ...props, type: "tool" });
const MemoryNode = (props) => React.createElement(NodeGenerator, { ...props, type: "memory" });
const ChatNode = (props) => React.createElement(NodeGenerator, { ...props, type: "chat" });
const SchedulerNode = (props) => React.createElement(NodeGenerator, { ...props, type: "scheduler" });
const CloudWatchAnalyzerNode = (props) => React.createElement(NodeGenerator, { ...props, type: "cloudwatchAnalyzer" });

export const nodeTypes = {
  agent: AgentNode,
  llm: LLMNode,
  database: DatabaseNode,
  teams: TeamsNode,
  tool: ToolNode,
  memory: MemoryNode,
  chat: ChatNode,
  output: OutputNode,
  orchestrator: OrchestratorNode,
  scheduler: SchedulerNode,
  cloudwatchAnalyzer: CloudWatchAnalyzerNode,
};

export { AgentNode, LLMNode, DatabaseNode, TeamsNode, ToolNode, MemoryNode, ChatNode, OutputNode, OrchestratorNode, SchedulerNode, CloudWatchAnalyzerNode };