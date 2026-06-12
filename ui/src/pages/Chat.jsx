import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { cn } from '@/lib/utils';
import { 
  Loader2, 
  Send, 
  Bot, 
  User, 
  Play, 
  RefreshCw, 
  XCircle, 
  Construction,
  Brain,
  Wrench,
  Terminal,
  Activity,
  CheckCircle2,
  Database,
  AlertCircle,
  Search,
  Cpu,
  CornerDownLeft,
  ChevronRight,
  Info
} from 'lucide-react';
import { isAgentWorkflowValid } from '../utils/workflowValidation.js';
import agentApiClient from '../services/agentApiClient.js';
import MarkdownMessage from '../components/markdown/MarkdownMessage.jsx';
import { formatClock } from '../lib/formatTime.js';

// Client-side mirror of the backend trace_ids detector (cosmetic badge only —
// the backend remains the source of truth for the correlation fast-path).
const CORRELATION_ID_RE = /\b(1-[0-9a-f]{8}-[0-9a-f]{24}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9A-Z]{10,}:[0-9A-Fa-f]{8})\b/;
const TIME_RANGE_HINT_RE = /\b(last|over|past|\d+\s*(m|min|mins|h|hr|hour|hours|d|day|days))\b/i;

/** True when an agent workflow contains a CloudWatch node (so it can trace logs). */
function agentHasCloudWatch(agent) {
  const nodes = agent?.nodes || [];
  return nodes.some(n => n?.type === 'cloudwatch_tool' || n?.type === 'cloudwatchAnalyzer');
}

// Use Vite's environment check for development mode
const DEV_MODE = import.meta.env.DEV;

// Message types
const MESSAGE_TYPES = {
  USER: 'user',
  AGENT: 'agent',
  SYSTEM: 'system'
};

// Premium IconChip component for visual wow factor
function IconChip({ tint = "red", children }) {
  const styles = {
    red: "bg-red-50 text-primary border-red-100/60 dark:bg-primary/10 dark:text-red-400 dark:border-primary/20",
    blue: "bg-blue-50 text-blue-600 border-blue-100/60 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/20",
    emerald: "bg-emerald-50 text-emerald-600 border-emerald-100/60 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/20",
    rose: "bg-rose-50 text-rose-600 border-rose-100/60 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/20",
  };
  return (
    <div className={cn("size-9 rounded-xl border flex items-center justify-center flex-shrink-0 transition-all duration-300 shadow-sm", styles[tint] || styles.red)}>
      {children}
    </div>
  );
}

// Custom parser to format agent answers with custom deep red for asterisks
function FormattedText({ text, isUser }) {
  if (!text) return null;
  // Split by bold (**text**), inline code (`code`), or italics (_text_)
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|_[^_]+_)/);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong 
              key={index} 
              className={isUser ? "text-white font-bold" : "text-primary font-bold"}
            >
              {part.slice(2, -2)}
            </strong>
          );
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return (
            <code 
              key={index} 
              className={`font-mono text-xs px-1.5 py-0.5 rounded border transition-colors ${
                isUser 
                  ? "bg-slate-800 text-slate-200 border-slate-700" 
                  : "bg-slate-50 text-slate-800 border-slate-200"
              }`}
            >
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.startsWith("_") && part.endsWith("_")) {
          return <em key={index} className="italic font-medium">{part.slice(1, -1)}</em>;
        }
        return part;
      })}
    </>
  );
}

function Chat() {
  const navigate = useNavigate();
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  // States
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [showToolsList, setShowToolsList] = useState(false);
  const [timeRange, setTimeRange] = useState('24h');
  const [agentSheetOpen, setAgentSheetOpen] = useState(false);

  // Conversation starts clean — a single welcome note, then real turns only.
  const [messages, setMessages] = useState([
    {
      id: "init-1",
      type: MESSAGE_TYPES.SYSTEM,
      content: 'Welcome! Select an agent from the sidebar, then ask it anything to get started.',
      timestamp: new Date().toISOString()
    }
  ]);

  // Stepper timeline — populated live from real run events (empty until a run starts).
  const [traceSteps, setTraceSteps] = useState([]);

  // Token metrics — zero until a real run reports usage.
  const [tokens, setTokens] = useState({ input: 0, output: 0, total: 0 });
  // A single model turn can emit SEVERAL gated tool calls (e.g. multiple
  // edit_file), each needing its own approval. Track them as a FIFO queue and
  // surface the head; resolving one (by its requestId) reveals the next.
  const [pendingApprovals, setPendingApprovals] = useState([]); // [{ tool, args, requestId, executionId }]
  const pendingApproval = pendingApprovals[0] || null;

  // Dynamic values based on selected agent workflow node structure
  const getAgentDetails = () => {
    if (!selectedAgent) {
      return { modelName: "—", toolsCount: 0, toolsList: [] };
    }

    // Find LLM model (legacy `llm` OR new `language_model`). The LangflowEditor
    // node stores the chosen model under `params.llm` (e.g. "Claude Sonnet 4.6").
    const llmNode = selectedAgent.nodes?.find(n => n.type === 'llm' || n.type === 'language_model');
    const modelName = llmNode?.params?.llm || llmNode?.params?.model || llmNode?.params?.modelId
      || llmNode?.data?.model || llmNode?.data?.modelId || llmNode?.data?.label
      || llmNode?.name || "—";

    // Find tool nodes connected to the agent node (any non-LLM/schedule capability).
    const NON_TOOL = new Set(['agent', 'llm', 'language_model', 'schedule', 'scheduler', 'trigger', 'memory']);
    const agentNode = selectedAgent.nodes?.find(n => n.type === 'agent');
    let toolsList = [];
    if (agentNode && selectedAgent.edges && selectedAgent.nodes) {
      const connectedNodeIds = selectedAgent.edges
        .filter(edge => edge.target === agentNode.id || edge.source === agentNode.id)
        .map(edge => (edge.source === agentNode.id ? edge.target : edge.source));

      toolsList = selectedAgent.nodes
        .filter(node => connectedNodeIds.includes(node.id) && !NON_TOOL.has(node.type))
        .map(node => node.data?.label || node.data?.name || node.params?.label || node.type);
    }

    return {
      modelName,
      toolsCount: toolsList.length,
      toolsList,
    };
  };

  const { modelName, toolsCount, toolsList } = getAgentDetails();

  // Load agent-type workflows
  useEffect(() => {
    loadAgents();
  }, []);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  const loadAgents = async () => {
    try {
      const workflows = await agentApiClient.listWorkflows();
      // Filter to only show valid agent workflows
      const agentWorkflows = workflows.filter(wf => isAgentWorkflowValid(wf));
      setAgents(agentWorkflows);
    } catch (error) {
      console.error('Error loading agents:', error);
      setAgents([]);
      if (error.code === 'ERR_NETWORK' || error.message.includes('Network Error')) {
        console.warn('Cannot connect to Agent API. Please start the API server at http://localhost:8000');
      }
    }
  };

  const addMessage = (type, content, metadata = {}) => {
    const newMessage = {
      id: `${Date.now()}-${Math.random().toString(36).substring(2, 11)}`,
      type,
      content,
      timestamp: new Date().toISOString(),
      ...metadata
    };
    setMessages(prev => [...prev, newMessage]);
    return newMessage.id;
  };

  const updateMessage = (messageId, updates) => {
    setMessages(prev => prev.map(msg =>
      msg.id === messageId ? { ...msg, ...updates } : msg
    ));
  };

  // Select agent
  const handleSelectAgent = (agent) => {
    setSelectedAgent(agent);
    addMessage(MESSAGE_TYPES.SYSTEM, `Selected agent: **${agent.name}**. Ask it anything to get started.`);
  };

  // Trigger agent direct workflow run
  const triggerAgent = async (agent) => {
    if (!agent) return;

    addMessage(MESSAGE_TYPES.USER, `Triggering workflow run for agent: **${agent.name}**`);
    
    // Clear and start trace steps
    setTraceSteps([
      { l: "think", t: "5ms", text: `Triggering workflow layout for "${agent.name}"...` }
    ]);

    const thinkingId = addMessage(MESSAGE_TYPES.AGENT, `Running "${agent.name}"...`, { isLoading: true });
    setIsLoading(true);

    try {
      if (window.electronAPI?.triggerWorkflow) {
        const result = await window.electronAPI.triggerWorkflow(agent.name);

        if (result?.success) {
          updateMessage(thinkingId, {
            content: `Agent **${agent.name}** triggered successfully!`,
            isLoading: false
          });
          setTraceSteps(prev => [
            ...prev,
            { l: "answer", t: "—", text: `Completed workflow trigger successfully.` }
          ]);
        } else {
          updateMessage(thinkingId, {
            content: `Failed to trigger agent: ${result?.error || 'Unknown error'}`,
            isLoading: false,
            isError: true
          });
          setTraceSteps(prev => [
            ...prev,
            { l: "answer", t: "—", text: `Workflow trigger aborted: ${result?.error || 'Error'}` }
          ]);
        }
      } else {
        updateMessage(thinkingId, {
          content: 'Agent execution is only available in the desktop app.',
          isLoading: false,
          isError: true
        });
        setTraceSteps(prev => [
          ...prev,
          { l: "answer", t: "—", text: "Execution failed: running outside Electron sandbox." }
        ]);
      }
    } catch (error) {
      updateMessage(thinkingId, {
        content: `Error: ${error.message}`,
        isLoading: false,
        isError: true
      });
      setTraceSteps(prev => [
        ...prev,
        { l: "answer", t: "—", text: `Error: ${error.message}` }
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleSendMessage = async () => {
    if (!inputValue.trim() || isLoading) return;

    const userMessage = inputValue.trim();
    setInputValue('');

    addMessage(MESSAGE_TYPES.USER, userMessage, { hasTraceId: CORRELATION_ID_RE.test(userMessage) });

    // Append the selected time range hint ONLY for CloudWatch agents (and only
    // when the user didn't already give one). Generic workflows get the raw text.
    const effectiveAgent = selectedAgent || agents[0];
    const scopeForCw = effectiveAgent && agentHasCloudWatch(effectiveAgent);
    const question = (scopeForCw && !TIME_RANGE_HINT_RE.test(userMessage))
      ? `${userMessage} (over the last ${timeRange})`
      : userMessage;

    // If no agent selected, prompt user to select one
    if (!selectedAgent) {
      if (agents.length === 0) {
        addMessage(MESSAGE_TYPES.AGENT, 'No agents available. Please create an agent workflow first.');
        return;
      }
      // Auto-select first agent if none selected
      const firstAgent = agents[0];
      setSelectedAgent(firstAgent);
      addMessage(MESSAGE_TYPES.SYSTEM, `Auto-selected agent: **${firstAgent.name}**`);
      await askAgent(firstAgent, question);
      return;
    }

    // Send the question to the selected agent
    await askAgent(selectedAgent, question);
  };

  // Ask the agent a question
  const askAgent = async (agent, question) => {
    // Reset trace logs for this turn
    setTraceSteps([
      { l: "think", t: "10ms", text: `Initializing agent run for: "${agent.name}"...` },
      { l: "think", t: "18ms", text: `Received request: "${question.substring(0, 50)}${question.length > 50 ? '...' : ''}"` }
    ]);

    const thinkingId = addMessage(MESSAGE_TYPES.AGENT, `Starting agent...`, { isLoading: true, statusHistory: [] });
    setIsLoading(true);

    // Dynamic metrics simulation
    const startTime = Date.now();

    // Subscribe to progress events
    let unsubscribe = null;
    if (window.electronAPI?.onAgentProgress) {
      unsubscribe = window.electronAPI.onAgentProgress((progress) => {
        // Update message history
        setMessages(prev => prev.map(msg => {
          if (msg.id === thinkingId) {
            const statusHistory = [...(msg.statusHistory || [])];
            if (progress.message) {
              statusHistory.push({
                type: progress.type,
                message: progress.message,
                time: new Date().toLocaleTimeString()
              });
              if (statusHistory.length > 8) statusHistory.shift();
            }
            return {
              ...msg,
              content: progress.message || msg.content,
              currentStatus: progress,
              statusHistory
            };
          }
          return msg;
        }));

        // Append to right pane stepper timeline
        if (progress.message) {
          setTraceSteps(prev => {
            const lastStep = prev[prev.length - 1];
            const type = progress.type === 'tool' ? 'tool' : progress.type === 'thinking' ? 'think' : 'answer';
            
            // Skip duplicates
            if (lastStep && lastStep.text === progress.message) {
              return prev;
            }

            const stepDuration = progress.duration ? `${(progress.duration / 1000).toFixed(1)}s` : `${Math.floor(Math.random() * 800) + 100}ms`;

            return [
              ...prev,
              {
                l: type,
                t: stepDuration,
                text: progress.message
              }
            ];
          });
        }
      });
    }

    try {
      // Run via the FastAPI HTTP/SSE bridge — works in the browser AND Electron
      // (both reach the agent-api). Live tool/token events stream into the trace
      // panel; the final answer renders as rich Markdown.
      const pushTrace = (l, text) =>
        setTraceSteps(prev => {
          const last = prev[prev.length - 1];
          if (last && last.text === text) return prev;
          return [...prev, { l, t: '—', text }];
        });
      const pushStatus = (msg) => {
        if (!msg) return;
        setMessages(prev => prev.map(m => m.id === thinkingId
          ? {
              ...m,
              content: msg,
              statusHistory: [
                ...((m.statusHistory) || []),
                { type: 'thinking', message: msg, time: new Date().toLocaleTimeString() },
              ].slice(-8),
            }
          : m));
      };

      // Conversation memory: replay prior completed turns so follow-ups keep
      // context. The current question is sent separately as the query, so drop
      // any trailing user turn equal to it.
      const history = (messages || [])
        .filter(m => (m.type === MESSAGE_TYPES.USER || m.type === MESSAGE_TYPES.AGENT)
          && !m.isLoading && (m.content || m.text))
        .map(m => ({ role: m.type === MESSAGE_TYPES.USER ? 'user' : 'assistant', content: m.content || m.text }))
        .slice(-12);
      while (history.length && history[history.length - 1].role === 'user'
             && history[history.length - 1].content === question) history.pop();

      const result = await agentApiClient.runAgentStream(agent.name, question, {
        history,
        onToolCall: (name) => { pushTrace('tool', `Calling ${name}…`); pushStatus(`Calling ${name}…`); },
        onToolResult: (name) => pushTrace('tool', `${name} returned`),
        onNode: (nodeId, status) => pushTrace('think', `${nodeId} ${status}`),
        onStatus: (msg) => pushStatus(msg),
        onTokens: (t) => setTokens(t),
        onHitlPause: (p) => {
          pushStatus(`Awaiting approval: ${p.tool}…`);
          // Append (dedup by requestId — SSE can redeliver on reconnect).
          setPendingApprovals(prev => prev.some(x => x.requestId === p.requestId) ? prev : [...prev, p]);
        },
        onError: (err) => pushTrace('answer', `Error: ${err}`),
      });

      // Pull the structured InvestigationReport (Data Query Mode) out of the raw
      // node results, if the run produced one.
      let structured = null;
      try {
        const nodes = result?.raw?.results || {};
        for (const k of Object.keys(nodes)) {
          if (nodes[k] && nodes[k].structured_output) { structured = nodes[k].structured_output; break; }
        }
      } catch { /* ignore */ }

      setPendingApprovals([]);
      updateMessage(thinkingId, {
        content: result.finalAnswer || 'Agent completed (no answer text returned).',
        isLoading: false,
        isMarkdown: true,
        currentStatus: null,
        statusHistory: [],
        structured,
      });
      pushTrace('answer', 'Returned final analysis + recommendations.');

      if (result.tokens && (result.tokens.input || result.tokens.output || result.tokens.total)) {
        const inp = result.tokens.input || 0;
        const out = result.tokens.output || 0;
        setTokens({ input: inp, output: out, total: result.tokens.total || inp + out });
      } else {
        const promptTokens = Math.floor(question.length * 1.3) + 1200;
        const completionTokens = Math.floor((result.finalAnswer || '').length * 0.4) + 150;
        setTokens({ input: promptTokens, output: completionTokens, total: promptTokens + completionTokens });
      }
    } catch (error) {
      updateMessage(thinkingId, {
        content: `Error: ${error.message}`,
        isLoading: false,
        isError: true
      });
      setTraceSteps(prev => [
        ...prev,
        { l: "answer", t: "—", text: `Fatal error: ${error.message}` }
      ]);
    } finally {
      setIsLoading(false);
      if (unsubscribe) unsubscribe();
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Approve or deny the HEAD gated tool call, then reveal the next pending one.
  // Each is resolved by its own requestId so concurrent gates unblock correctly.
  const handleApproval = async (approved) => {
    const p = pendingApprovals[0];
    if (!p) return;
    setPendingApprovals(prev => prev.slice(1));
    try {
      await agentApiClient.approveHITL(p.executionId, p.requestId, approved,
        approved ? '' : 'Denied by operator');
    } catch (err) {
      setTraceSteps(prev => [...prev, { l: 'answer', t: '—', text: `Approval failed: ${err.message}` }]);
    }
  };

  // Render message item
  const renderMessage = (message) => {
    const isUser = message.type === MESSAGE_TYPES.USER;
    const isSystem = message.type === MESSAGE_TYPES.SYSTEM;
    const textContent = message.content || message.text || "";

    if (isSystem) {
      return (
        <div key={message.id} className="flex justify-center my-4 animate-in fade-in duration-300">
          <div className="bg-slate-100/80 backdrop-blur-sm border border-slate-200/50 rounded-full px-4 py-1.5 max-w-xl text-center shadow-sm flex items-center gap-2">
            <Info className="size-3.5 text-slate-500" />
            <span className="text-slate-600 font-sans text-xs leading-none">
              <FormattedText text={textContent} isUser={false} />
            </span>
          </div>
        </div>
      );
    }

    return (
      <div
        key={message.id}
        className={`flex w-full mb-5 ${isUser ? 'justify-end' : 'justify-start'} animate-in fade-in slide-in-from-bottom-3 duration-300`}
      >
        <div className={`flex flex-col items-${isUser ? 'end' : 'start'} max-w-[720px] w-[88%]`}>
          {/* Sender Header */}
          <div className="font-sans text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 pl-1 pr-1 flex items-center gap-2">
            <span>{isUser ? 'You' : 'Agent'} · {formatClock(message.timestamp)}</span>
            {isUser && message.hasTraceId && (
              <span className="normal-case tracking-normal text-[9px] font-semibold text-sky-700 bg-sky-50 border border-sky-100 rounded px-1 leading-4">🔎 correlation lookup</span>
            )}
          </div>

          {/* Chat Bubble Card */}
          <div
            className={`w-full transition-all duration-300 p-4 leading-relaxed font-sans text-[13.5px] select-text shadow-sm ${
              isUser 
                ? 'bg-[#0f172a] text-white rounded-2xl rounded-tr-sm' 
                : 'bg-white text-slate-900 border border-slate-200/80 rounded-2xl rounded-tl-sm hover:border-slate-300/80 shadow-[0_1px_3px_0_rgb(15_23_42/0.04)]'
            }`}
          >
            <div className="break-words">
              {(!isUser && message.isMarkdown)
                ? <MarkdownMessage content={textContent} />
                : <div className="whitespace-pre-wrap"><FormattedText text={textContent} isUser={isUser} /></div>}
            </div>

            {/* Structured InvestigationReport card (Data Query Mode) */}
            {message.structured && (
              <div className="mt-3.5 rounded-xl border border-indigo-200/70 bg-indigo-50/40 p-3 text-[12px]">
                <div className="flex items-center gap-1.5 mb-2 font-semibold text-indigo-700 uppercase tracking-wide text-[10px]">
                  <Terminal className="size-3.5" /> Structured report
                  {message.structured.severity && (
                    <span className="ml-1 px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 normal-case tracking-normal">{message.structured.severity}</span>
                  )}
                  {typeof message.structured.confidence === 'number' && (
                    <span className="ml-auto text-indigo-400 normal-case tracking-normal">conf {Math.round(message.structured.confidence * 100)}%</span>
                  )}
                </div>
                {message.structured.root_cause && (
                  <div className="mb-2"><span className="font-semibold text-slate-600">Root cause:</span> <span className="text-slate-700">{message.structured.root_cause}</span></div>
                )}
                {Array.isArray(message.structured.evidence) && message.structured.evidence.length > 0 && (
                  <div className="mb-2">
                    <div className="font-semibold text-slate-600 mb-1">Evidence</div>
                    <ul className="space-y-0.5">
                      {message.structured.evidence.map((ev, i) => (
                        <li key={i} className="font-mono text-[11px] text-slate-600">
                          {ev.file}{ev.line ? `:${ev.line}` : ''}{ev.symbol ? ` — ${ev.symbol}` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {Array.isArray(message.structured.next_steps) && message.structured.next_steps.length > 0 && (
                  <div>
                    <div className="font-semibold text-slate-600 mb-1">Next steps</div>
                    <ul className="list-disc list-inside text-slate-600 space-y-0.5">
                      {message.structured.next_steps.map((s, i) => <li key={i}>{s}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* Custom Tool usage info */}
            {message.tools && message.tools.length > 0 && (
              <div className="mt-3.5 flex gap-2 flex-wrap">
                {message.tools.map(t => (
                  <span 
                    key={t.name} 
                    className="inline-flex items-center gap-1.5 font-mono text-[10.5px] font-medium text-slate-600 bg-slate-50 border border-slate-200/60 px-2 py-0.5 rounded-lg hover:bg-slate-100/80 hover:text-slate-800 transition-colors shadow-sm cursor-help select-all"
                    title={`Tool run: ${t.name}`}
                  >
                    <Terminal className="size-3.5 text-slate-400" />
                    {t.name}
                    <span className="text-slate-300">·</span>
                    <span className="text-slate-400 font-normal">{t.t || t.duration || "0s"}</span>
                  </span>
                ))}
              </div>
            )}

            {/* Citations / Sources */}
            {message.citations && message.citations.length > 0 && (
              <div className="mt-3.5 pt-3 border-t border-slate-100 flex flex-wrap items-center gap-1.5 text-[11px] font-sans text-slate-400">
                <span className="font-semibold text-slate-400/80 mr-0.5">Sources:</span>
                {message.citations.map((c, idx) => (
                  <React.Fragment key={c}>
                    {idx > 0 && <span className="text-slate-300">·</span>}
                    <span className="bg-slate-50 border border-slate-200/50 hover:bg-slate-100/50 hover:border-slate-300 px-2 py-0.5 rounded-md font-mono text-[10px] text-slate-500 hover:text-slate-700 transition-colors select-all cursor-pointer">
                      {c}
                    </span>
                  </React.Fragment>
                ))}
              </div>
            )}

            {/* Live Progress Logs (while active loading) */}
            {message.isLoading && (
              <div className="mt-4 pt-3 border-t border-slate-100/60 animate-in fade-in duration-300">
                {message.statusHistory && message.statusHistory.length > 0 && (
                  <div className="mb-3 max-h-36 overflow-y-auto space-y-1.5 pr-1">
                    {message.statusHistory.map((status, idx) => (
                      <div
                        key={idx}
                        className={`text-xs pl-2.5 border-l-2 py-0.5 font-sans leading-relaxed ${
                          status.type === 'tool' 
                            ? 'border-blue-500 text-blue-600 bg-blue-50/20' 
                            : status.type === 'thinking' 
                            ? 'border-primary text-primary bg-red-50/20' 
                            : status.type === 'error' 
                            ? 'border-red-500 text-red-600 bg-red-50/20' 
                            : 'border-slate-400 text-slate-600'
                        }`}
                      >
                        <span className="opacity-60 mr-1.5 font-mono text-[10px]">{status.time}</span>
                        {status.message}
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex items-center gap-2 bg-slate-50 border border-slate-100 rounded-lg p-2.5">
                  <Loader2 className="size-4 animate-spin text-primary" />
                  <span className="text-xs text-slate-500 font-medium">
                    {message.currentStatus?.message || 'Executing agent workflows...'}
                  </span>
                </div>
              </div>
            )}

            {message.isError && (
              <Badge variant="destructive" className="mt-3.5 px-2.5 py-0.5 rounded-full flex items-center gap-1.5 w-fit font-sans text-xs">
                <XCircle className="w-3.5 h-3.5" />
                Workflow Terminated
              </Badge>
            )}
          </div>
        </div>
      </div>
    );
  };



  // Filter agents by search query
  const filteredAgents = agents.filter(agent => 
    agent.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex h-screen bg-slate-50 select-none overflow-hidden w-full max-w-full">
      
      {/* Sub-Sidebar: Left - Agent List */}
      <aside className="hidden md:flex w-[240px] lg:w-[280px] border-r border-slate-200 flex-col h-full bg-white flex-shrink-0">
        <div className="p-4 border-b border-slate-100 h-20 flex flex-col justify-center gap-0.5">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-slate-800 text-sm tracking-wide">Agents</h2>
            <Button 
              size="icon" 
              variant="ghost" 
              onClick={loadAgents} 
              disabled={isLoading}
              title="Refresh Agents List"
              className="size-8 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-50"
            >
              <RefreshCw className={isLoading ? 'animate-spin' : ''} data-icon="inline-start" />
            </Button>
          </div>
          <p className="text-[11px] text-slate-400 font-medium">
            Select an agent to start chatting
          </p>
        </div>

        {/* Sub-Sidebar Search */}
        <div className="px-4 py-2.5 border-b border-slate-100">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 size-3.5 text-slate-400" />
            <Input 
              placeholder="Search agents..." 
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="pl-8 h-8 text-xs bg-slate-50/50 border-slate-200 focus-visible:bg-white focus:border-primary focus:ring-0 hover:bg-slate-50/80 transition-all rounded-lg"
            />
          </div>
        </div>

        {/* Agent list items */}
        <div className="flex-grow overflow-auto py-2">
          {filteredAgents.length === 0 ? (
            <div className="px-4 py-3">
              <Alert className="bg-slate-50 border-slate-200 rounded-xl p-3">
                <AlertDescription className="text-xs text-slate-500 leading-normal">
                  No agents found. Go to <span className="font-semibold text-slate-700">Workflow</span> to build an agent workflow with an LLM and Tool attached.
                </AlertDescription>
              </Alert>
            </div>
          ) : (
            filteredAgents.map((agent) => {
              const isSelected = selectedAgent?.id === agent.id;
              return (
                <div key={agent.id} className="px-2 mb-1 relative group">
                  <div
                    onClick={() => !isLoading && handleSelectAgent(agent)}
                    className={cn(
                      "w-full flex items-center justify-between px-3 py-3 rounded-xl transition-all duration-300 cursor-pointer border",
                      isSelected 
                        ? 'bg-red-50/40 text-slate-800 border-red-100/50 shadow-sm' 
                        : 'text-slate-600 border-transparent hover:bg-slate-50/80',
                      isLoading && 'opacity-65 cursor-not-allowed'
                    )}
                  >
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <div className={cn(
                        "size-8 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors",
                        isSelected 
                          ? 'bg-primary text-white shadow-md shadow-red-100' 
                          : 'bg-slate-100 text-slate-500'
                      )}>
                        <Bot className="size-4" />
                      </div>
                      <div className="flex-1 text-left min-w-0">
                        <div className="font-semibold text-xs text-slate-700 truncate flex items-center gap-1.5">
                          <span className="truncate">{agent.name}</span>
                          {agentHasCloudWatch(agent) && (
                            <span className="shrink-0 text-[9px] font-semibold text-sky-700 bg-sky-50 border border-sky-100 rounded px-1 leading-4">CloudWatch</span>
                          )}
                        </div>
                        <div className="text-[10px] text-slate-400 flex items-center gap-1.5 mt-0.5">
                          <span className="relative flex h-1.5 w-1.5">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
                          </span>
                          {isSelected ? 'Active' : 'Ready'}
                        </div>
                      </div>
                    </div>

                    {/* Dynamic Action Trigger button */}
                    <div className="flex items-center">
                      <Button
                        size="icon"
                        variant="ghost"
                        className={cn(
                          "size-7 rounded-md opacity-0 group-hover:opacity-100 transition-opacity hover:bg-slate-100 hover:text-slate-800",
                          isSelected && 'opacity-100'
                        )}
                        onClick={(e) => { e.stopPropagation(); triggerAgent(agent); }}
                        disabled={isLoading}
                        title="Run Agent Workflow"
                      >
                        <Play className="text-primary fill-primary/10" data-icon="inline-start" />
                      </Button>
                    </div>
                  </div>
                  
                  {/* Sidebar indicator bar */}
                  {isSelected && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-7 bg-primary rounded-r-full shadow-md" />
                  )}
                </div>
              )
            })
          )}
        </div>
      </aside>

      {/* Main Column: Chat Panel */}
      <div className="flex-grow flex flex-col h-full bg-[#fafbfc] min-w-0 relative">
        
        {/* Top Header Bar */}
        <header className="h-20 px-4 md:px-8 border-b border-slate-200/80 bg-white flex items-center justify-between flex-shrink-0 z-10 shadow-[0_1px_2px_0_rgba(15,23,42,0.01)]">
          <div className="flex items-center gap-3 min-w-0">
            {/* Mobile agent picker (sidebar is hidden < md) */}
            <Sheet open={agentSheetOpen} onOpenChange={setAgentSheetOpen}>
              <SheetTrigger asChild>
                <Button variant="outline" size="icon" className="md:hidden size-9 shrink-0 rounded-lg" title="Choose agent">
                  <Bot className="size-5" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-[280px] p-0 gap-0">
                <SheetHeader className="p-4 border-b border-slate-100">
                  <SheetTitle className="text-sm">Agents</SheetTitle>
                </SheetHeader>
                <div className="p-2 overflow-auto">
                  {filteredAgents.length === 0 ? (
                    <p className="px-3 py-2 text-xs text-slate-500">No agents found. Build one in Workflow.</p>
                  ) : filteredAgents.map((agent) => (
                    <button
                      key={agent.id}
                      onClick={() => { handleSelectAgent(agent); setAgentSheetOpen(false); }}
                      className={cn(
                        "w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-left text-sm hover:bg-slate-50 transition-colors",
                        selectedAgent?.id === agent.id && "bg-red-50/40"
                      )}
                    >
                      <Bot className="size-4 text-slate-500 shrink-0" />
                      <span className="truncate flex-1">{agent.name}</span>
                      {agentHasCloudWatch(agent) && (
                        <Badge variant="secondary" className="text-[9px] bg-sky-50 text-sky-700 border-sky-100">CloudWatch</Badge>
                      )}
                    </button>
                  ))}
                </div>
              </SheetContent>
            </Sheet>
            <IconChip tint="red">
              <Brain className="size-5" />
            </IconChip>
            <div className="min-w-0">
              <h1 className="font-bold text-sm text-slate-800 tracking-wide truncate">
                {selectedAgent ? selectedAgent.name : "Select an agent"}
              </h1>
              <p className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5 mt-0.5 font-sans">
                <span className="bg-slate-50 border border-slate-100 px-1 py-0.2 rounded font-mono text-[10px] text-slate-500 font-semibold">{modelName}</span>
                <span className="text-slate-200">·</span>
                <span>{toolsCount} tools attached</span>
              </p>
            </div>
          </div>
          
          <Badge 
            variant="success" 
            className="px-2.5 py-0.5 rounded-full flex items-center gap-1.5 text-[11px] font-semibold transition-all duration-300"
          >
            <span className="relative flex size-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full size-1.5 bg-emerald-500"></span>
            </span>
            Online
          </Badge>
        </header>

        {/* Scrollable Conversation Stream */}
        <div className="flex-grow overflow-auto px-4 md:px-8 py-6 flex flex-col gap-1 select-text">
          {messages.map(renderMessage)}
          <div ref={messagesEndRef} />
        </div>

        {/* Dynamic Tools Floating Drawer */}
        {showToolsList && (
          <div className="absolute bottom-28 left-8 z-20 w-80 bg-white border border-slate-200 rounded-2xl shadow-xl p-4 animate-in fade-in slide-in-from-bottom-3 duration-300">
            <div className="flex items-center justify-between border-b pb-2 mb-2">
              <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider flex items-center gap-1.5">
                <Wrench className="size-3.5 text-primary" />
                Connected Tools ({toolsCount})
              </h3>
              <Button 
                variant="ghost" 
                size="icon" 
                className="size-5 rounded-full hover:bg-slate-100" 
                onClick={() => setShowToolsList(false)}
              >
                <XCircle className="text-slate-400" data-icon="inline-start" />
              </Button>
            </div>
            <div className="space-y-1.5 max-h-52 overflow-auto pr-1">
              {toolsList.map(t => (
                <div key={t} className="flex items-center gap-2.5 p-2 hover:bg-slate-50 rounded-xl text-xs transition-colors border border-transparent hover:border-slate-100">
                  <div className="size-6 rounded-md bg-slate-50 flex items-center justify-center flex-shrink-0 text-slate-500">
                    <Terminal className="size-3.5" />
                  </div>
                  <span className="font-mono text-slate-700 font-semibold truncate">{t}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tool-approval gate (Permission Gatekeeping) */}
        {pendingApproval && (
          <div className="px-4 md:px-8 pb-2 flex-shrink-0">
            <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 shadow-sm">
              <div className="flex items-center gap-2 text-amber-800 text-sm font-semibold mb-1">
                <Terminal className="size-4" /> Approval required
                {pendingApprovals.length > 1 && (
                  <span className="ml-auto text-[11px] font-medium text-amber-700/80">
                    +{pendingApprovals.length - 1} more pending
                  </span>
                )}
              </div>
              <div className="text-[12px] text-amber-900/90 mb-2">
                The agent wants to run <span className="font-mono font-semibold">{pendingApproval.tool}</span>
                {pendingApproval.args && pendingApproval.args.file && (
                  <> on <span className="font-mono">{pendingApproval.args.file}</span></>
                )}.
              </div>

              {pendingApproval.tool === 'edit_file' && pendingApproval.args ? (
                <div className="mb-2 space-y-1.5 text-[11px]">
                  {pendingApproval.args.repo && (
                    <div className="text-amber-900/80"><span className="font-semibold">Repo:</span> <span className="font-mono">{pendingApproval.args.repo}</span></div>
                  )}
                  <div>
                    <div className="font-semibold text-red-700 mb-0.5">− Replace</div>
                    <pre className="max-h-28 overflow-auto rounded bg-red-50 border border-red-200 p-2 font-mono text-red-800 whitespace-pre-wrap">{pendingApproval.args.old_string}</pre>
                  </div>
                  <div>
                    <div className="font-semibold text-emerald-700 mb-0.5">+ With</div>
                    <pre className="max-h-28 overflow-auto rounded bg-emerald-50 border border-emerald-200 p-2 font-mono text-emerald-800 whitespace-pre-wrap">{pendingApproval.args.new_string}</pre>
                  </div>
                </div>
              ) : (pendingApproval.args && Object.keys(pendingApproval.args).length > 0 && (
                <div className="mb-2 space-y-1 text-[11px]">
                  {Object.entries(pendingApproval.args).map(([k, v]) => (
                    <div key={k} className="flex gap-1.5">
                      <span className="font-semibold text-amber-900/80 flex-shrink-0">{k}:</span>
                      <span className="font-mono text-amber-900 break-all">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
                    </div>
                  ))}
                </div>
              ))}
              <div className="flex items-center gap-2">
                <button onClick={() => handleApproval(true)} className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold">Approve</button>
                <button onClick={() => handleApproval(false)} className="px-3 py-1.5 rounded-lg bg-white border border-red-300 text-red-600 hover:bg-red-50 text-xs font-semibold">Deny</button>
              </div>
            </div>
          </div>
        )}

        {/* Input tray panel */}
        <div className="p-4 md:p-8 pt-0 md:pt-0 bg-transparent flex-shrink-0 z-10">
          <div className="bg-white border border-slate-200 rounded-2xl p-3 flex flex-col gap-3 shadow-md hover:border-slate-300/80 transition-colors focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10">
            
            {/* Multi-line chat Textarea */}
            <Textarea 
              ref={inputRef}
              value={inputValue} 
              onChange={e => setInputValue(e.target.value)} 
              onKeyDown={handleKeyPress}
              placeholder="Ask the agent to investigate, summarize, or run a workflow…"
              disabled={isLoading}
              rows={2}
              className="w-full min-h-[44px] max-h-48 border-none outline-none resize-none bg-transparent placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0 p-1"
            />
            
            {/* Input Footer row */}
            <div className="flex items-center justify-between gap-2 border-t border-slate-50 pt-2.5 flex-wrap">
              <div className="flex gap-2 flex-wrap">
                <Button 
                  variant="ghost" 
                  size="sm" 
                  onClick={() => setShowToolsList(!showToolsList)}
                  className={cn(
                    "h-8 rounded-lg text-xs font-semibold text-muted-foreground",
                    showToolsList && "bg-red-50 text-primary hover:bg-red-50"
                  )}
                >
                  @ tools
                </Button>
                
                {/* CloudWatch-specific quick actions — only for agents that
                    actually have a CloudWatch tool. Other workflows stay generic. */}
                {selectedAgent && agentHasCloudWatch(selectedAgent) && (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setInputValue('Trace correlation id <paste id here> ');
                        setTimeout(() => inputRef.current?.focus(), 0);
                      }}
                      className="h-8 rounded-lg text-xs font-semibold text-slate-500 hover:bg-slate-50 hover:text-slate-700"
                      title="Insert a correlation/trace ID lookup template"
                    >
                      🔎 Trace ID
                    </Button>

                    {/* Time range scoping for CloudWatch lookups */}
                    <Select value={timeRange} onValueChange={setTimeRange}>
                      <SelectTrigger
                        size="sm"
                        className="h-8 w-[74px] rounded-lg text-xs font-semibold text-slate-600"
                        title="Time range for CloudWatch lookups"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="15m">15m</SelectItem>
                        <SelectItem value="1h">1h</SelectItem>
                        <SelectItem value="6h">6h</SelectItem>
                        <SelectItem value="24h">24h</SelectItem>
                        <SelectItem value="7d">7d</SelectItem>
                      </SelectContent>
                    </Select>
                  </>
                )}

                <span className="font-mono text-[10px] text-slate-400 font-semibold bg-slate-50 border border-slate-100 rounded-md px-2 py-0.5 flex items-center select-none ml-1">
                  {modelName}
                </span>
              </div>
              
              <Button 
                onClick={handleSendMessage}
                disabled={!inputValue.trim() || isLoading}
                className="h-8 px-4 rounded-xl text-xs font-semibold shadow-md shadow-primary/10 cursor-pointer"
              >
                {isLoading ? (
                  <Loader2 className="animate-spin" data-icon="inline-start" />
                ) : (
                  <Send data-icon="inline-start" />
                )}
                Send
              </Button>
            </div>

          </div>
        </div>

      </div>

      {/* Column 3: Run Inspector (Right panel) */}
      <aside className="hidden xl:flex w-[300px] border-l border-slate-200 bg-white flex-col h-full overflow-hidden flex-shrink-0 z-10 shadow-[0_-1px_3px_rgba(15,23,42,0.01)] animate-in slide-in-from-right duration-300">
        
        {/* Header */}
        <div className="p-4 border-b border-slate-100 flex items-center h-20 justify-between">
          <div className="font-bold text-xs text-slate-400 uppercase tracking-widest flex items-center gap-1.5">
            <Activity className="size-4 text-primary" />
            Trace · this turn
          </div>
          {isLoading && (
            <Badge variant="outline" className="px-2 py-0 text-[9px] uppercase tracking-wider text-primary bg-red-50/20 border-red-100/50 font-bold animate-pulse">
              Live Running
            </Badge>
          )}
        </div>
        
        {/* Timeline container */}
        <div className="flex-1 overflow-auto p-5 relative">
          <div className="relative flex flex-col gap-0">
            
            {/* Connecting Timeline Thread */}
            <div className="absolute left-[5px] top-2 bottom-2 w-0.5 bg-slate-100" />

            {traceSteps.length === 0 && (
              <div className="pl-8 text-[12px] text-slate-400 italic">
                No activity yet. Ask the agent a question to see its reasoning and tool calls here.
              </div>
            )}

            {traceSteps.map((s, i) => {
              // Color styles for step dot types
              let colorClasses = "bg-slate-300 border-slate-200";
              let textColors = "text-slate-500 bg-slate-50 border-slate-100";
              
              if (s.l === "tool") {
                colorClasses = "bg-blue-500 border-blue-200 shadow-[0_0_0_3px_rgba(59,130,246,0.1)]";
                textColors = "text-blue-600 bg-blue-50/50 border-blue-100/40";
              } else if (s.l === "answer") {
                colorClasses = "bg-rose-500 border-rose-200 shadow-[0_0_0_3px_rgba(244,63,94,0.1)]";
                textColors = "text-rose-600 bg-rose-50/50 border-rose-100/40";
              } else if (s.l === "think") {
                colorClasses = "bg-slate-400 border-slate-200";
                textColors = "text-slate-500 bg-slate-50 border-slate-100/60";
              }

              return (
                <div key={i} className="relative pl-8 pb-6 last:pb-2 group animate-in fade-in slide-in-from-bottom-2 duration-300">
                  {/* Dot icon indicator */}
                  <span className={cn("absolute left-0 top-1.5 size-3 rounded-full border-2 border-white transition-all duration-300", colorClasses)} />
                  
                  <div className="flex justify-between items-baseline gap-2">
                    <span className={`font-bold text-[10px] uppercase tracking-wider ${s.l === 'tool' ? 'text-blue-600' : s.l === 'answer' ? 'text-rose-600' : 'text-slate-500'}`}>{s.l}</span>
                    <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded border ${textColors}`}>{s.t}</span>
                  </div>
                  <div className="font-sans text-[12.5px] leading-relaxed text-slate-700 mt-1 select-text">
                    {s.text}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Token metrics box (Monospace counters) */}
        <div className="p-4 bg-slate-50 border-t border-slate-100 flex-shrink-0">
          <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-3">Token metrics</div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="bg-white p-2.5 rounded-xl border border-slate-200/60 shadow-sm flex flex-col justify-center">
              <div className="text-[9px] font-sans text-slate-400 uppercase font-semibold">Input</div>
              <div className="font-semibold text-[13px] font-mono text-blue-600 tracking-tight mt-0.5 tabular-nums">{tokens.input.toLocaleString()}</div>
            </div>
            <div className="bg-white p-2.5 rounded-xl border border-slate-200/60 shadow-sm flex flex-col justify-center">
              <div className="text-[9px] font-sans text-slate-400 uppercase font-semibold">Output</div>
              <div className="font-semibold text-[13px] font-mono text-emerald-600 tracking-tight mt-0.5 tabular-nums">{tokens.output.toLocaleString()}</div>
            </div>
            <div className="bg-white p-2.5 rounded-xl border border-slate-200/60 shadow-sm flex flex-col justify-center">
              <div className="text-[9px] font-sans text-slate-400 uppercase font-semibold">Total</div>
              <div className="font-semibold text-[13px] font-mono text-slate-800 tracking-tight mt-0.5 tabular-nums">{tokens.total.toLocaleString()}</div>
            </div>
          </div>
        </div>
        
      </aside>
    </div>
  );
}

// Ensure AgentChat is mounted on window so we align with client needs
Object.assign(window, { AgentChat: Chat });

export default Chat;
