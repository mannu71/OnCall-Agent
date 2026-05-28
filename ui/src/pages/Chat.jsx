import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
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
  Calendar,
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

// Use Vite's environment check for development mode
const DEV_MODE = import.meta.env.DEV;

// Message types
const MESSAGE_TYPES = {
  USER: 'user',
  AGENT: 'agent',
  SYSTEM: 'system'
};

// Premium IconChip component for visual wow factor
function IconChip({ tint = "violet", children }) {
  const styles = {
    violet: "bg-violet-50 text-violet-600 border-violet-100/60 dark:bg-violet-500/10 dark:text-violet-400 dark:border-violet-500/20",
    blue: "bg-blue-50 text-blue-600 border-blue-100/60 dark:bg-blue-500/10 dark:text-blue-400 dark:border-blue-500/20",
    emerald: "bg-emerald-50 text-emerald-600 border-emerald-100/60 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/20",
    rose: "bg-rose-50 text-rose-600 border-rose-100/60 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/20",
  };
  return (
    <div className={`w-9 h-9 rounded-xl border flex items-center justify-center flex-shrink-0 transition-all duration-300 shadow-sm ${styles[tint] || styles.violet}`}>
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
              className={isUser ? "text-white font-bold" : "text-red-600 font-bold"}
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

  // Starting Conversation State pre-populated with high-fidelity mockup messages
  const [messages, setMessages] = useState([
    {
      id: "init-1",
      type: MESSAGE_TYPES.SYSTEM,
      content: 'Welcome to OnCall Agent chat! Select an orchestrator from the sidebar to begin diagnostics.',
      timestamp: new Date(Date.now() - 30 * 60000).toISOString()
    },
    {
      id: "init-2",
      type: MESSAGE_TYPES.USER,
      content: "Why did profile-validation fail at 22:00 last night?",
      timestamp: new Date(Date.now() - 25 * 60000).toISOString()
    },
    {
      id: "init-3",
      type: MESSAGE_TYPES.AGENT,
      content: "I'll trace the run. Pulling postgres errors and CloudWatch alarms for the time window…",
      tools: [
        { name: "postgres__query", t: "0.3s" },
        { name: "cloudwatch__get_alarms", t: "0.5s" }
      ],
      timestamp: new Date(Date.now() - 24 * 60000).toISOString()
    },
    {
      id: "init-4",
      type: MESSAGE_TYPES.AGENT,
      content: "**Root cause:** the postgres connection pool exhausted at 21:57:14. 3 profiles (ids `4f12-…`, `9a02-…`, `c81e-…`) timed out at the read step.\n\nThe pool is sized at 8 connections. The ado-release-monitor workflow held 6 of them during a long-running release diff — overlap was unavoidable with current scheduling.\n\n**Recommendation:** raise pool size to 16, _or_ move ado-release-monitor to 21:30.",
      citations: [
        "postgres__query: connection_pool_size",
        "cloudwatch__alarms: rds-conn-exhaust"
      ],
      timestamp: new Date(Date.now() - 22 * 60000).toISOString()
    },
    {
      id: "init-5",
      type: MESSAGE_TYPES.USER,
      content: "Raise pool size to 16 and re-run for the failed profiles.",
      timestamp: new Date(Date.now() - 20 * 60000).toISOString()
    }
  ]);

  // Stepper timeline trace steps - starts with high fidelity mock, updates dynamically on active runs
  const [traceSteps, setTraceSteps] = useState([
    { l: "think", t: "12ms", text: "Plan: query postgres for failed rows, then cross-ref CloudWatch." },
    { l: "tool",  t: "0.31s", text: "postgres__query · SELECT * FROM profiles WHERE status='failed'…" },
    { l: "tool",  t: "0.54s", text: "cloudwatch__get_alarms · ALARM_OK transitions, t-15m" },
    { l: "think", t: "8ms", text: "Correlate pool_exhausted timestamp ↔ failed profile ids." },
    { l: "answer", t: "—", text: "Returned root cause + recommendation." },
  ]);

  // Token Metrics state - defaults to high fidelity mock, updates dynamically on runs
  const [tokens, setTokens] = useState({
    input: 3402,
    output: 814,
    total: 4216
  });

  // Dynamic values based on selected agent workflow node structure
  const getAgentDetails = () => {
    if (!selectedAgent) {
      return {
        modelName: "claude-sonnet-4",
        toolsCount: 3,
        toolsList: ["postgres__query", "cloudwatch__get_alarms", "ado-release-monitor"]
      };
    }

    // Find LLM model from node data
    const llmNode = selectedAgent.nodes?.find(n => n.type === 'llm');
    const modelName = llmNode?.data?.model || llmNode?.data?.label || "claude-sonnet-4";

    // Find tool nodes connected to the agent node
    const agentNode = selectedAgent.nodes?.find(n => n.type === 'agent');
    let toolsList = [];
    if (agentNode && selectedAgent.edges && selectedAgent.nodes) {
      const connectedNodeIds = selectedAgent.edges
        .filter(edge => edge.target === agentNode.id)
        .map(edge => edge.source);
      
      toolsList = selectedAgent.nodes
        .filter(node => connectedNodeIds.includes(node.id) && (node.type === 'tool' || node.type === 'cloudwatchAnalyzer'))
        .map(node => node.data?.label || node.data?.name || node.type);
    }

    return {
      modelName,
      toolsCount: toolsList.length || 1,
      toolsList: toolsList.length > 0 ? toolsList : ["postgres__query", "cloudwatch__get_alarms"]
    };
  };

  const { modelName, toolsCount, toolsList } = getAgentDetails();

  // Load agent-type workflows
  useEffect(() => {
    if (DEV_MODE) {
      loadAgents();
    }
  }, []);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (DEV_MODE) {
      scrollToBottom();
    }
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
    addMessage(MESSAGE_TYPES.SYSTEM, `Selected agent: **${agent.name}**. Ready to run incident logs and trace executions.`);
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

    addMessage(MESSAGE_TYPES.USER, userMessage);

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
      await askAgent(firstAgent, userMessage);
      return;
    }

    // Send the question to the selected agent
    await askAgent(selectedAgent, userMessage);
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
      if (window.electronAPI?.runAgent) {
        const result = await window.electronAPI.runAgent(agent.name, question);

        if (result?.success) {
          updateMessage(thinkingId, {
            content: result.answer || 'Agent completed successfully.',
            isLoading: false,
            currentStatus: null
          });

          // Final successful trace step
          setTraceSteps(prev => [
            ...prev,
            { l: "answer", t: "—", text: "Returned final analysis + recommendations." }
          ]);

          // Compute dynamic token count based on input/output size
          const promptTokens = Math.floor(question.length * 1.3) + 1200;
          const completionTokens = Math.floor((result.answer || '').length * 0.4) + 150;
          setTokens({
            input: promptTokens,
            output: completionTokens,
            total: promptTokens + completionTokens
          });

        } else {
          updateMessage(thinkingId, {
            content: `Error: ${result?.error || 'Unknown error'}`,
            isLoading: false,
            isError: true,
            currentStatus: null
          });

          setTraceSteps(prev => [
            ...prev,
            { l: "answer", t: "—", text: `Run failed: ${result?.error || 'Execution aborted'}` }
          ]);
        }
      } else {
        // Fallback for demo when not inside Electron
        setTimeout(() => {
          updateMessage(thinkingId, {
            content: "Agent execution is only available in the desktop app container. Connect to the local Electron app for real runs.",
            isLoading: false,
            isError: true
          });
          setTraceSteps(prev => [
            ...prev,
            { l: "answer", t: "—", text: "Execution rejected: Electron environment missing." }
          ]);
        }, 1200);
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

  // Render message item
  const renderMessage = (message) => {
    const isUser = message.type === MESSAGE_TYPES.USER;
    const isSystem = message.type === MESSAGE_TYPES.SYSTEM;
    const textContent = message.content || message.text || "";

    if (isSystem) {
      return (
        <div key={message.id} className="flex justify-center my-4 animate-in fade-in duration-300">
          <div className="bg-slate-100/80 backdrop-blur-sm border border-slate-200/50 rounded-full px-4 py-1.5 max-w-xl text-center shadow-sm flex items-center gap-2">
            <Info className="w-3.5 h-3.5 text-slate-500" />
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
          <div className="font-sans text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 pl-1 pr-1">
            {isUser ? 'You' : 'Agent'} · {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>

          {/* Chat Bubble Card */}
          <div
            className={`w-full transition-all duration-300 p-4 leading-relaxed font-sans text-[13.5px] select-text shadow-sm ${
              isUser 
                ? 'bg-[#0f172a] text-white rounded-2xl rounded-tr-sm' 
                : 'bg-white text-slate-900 border border-slate-200/80 rounded-2xl rounded-tl-sm hover:border-slate-300/80 shadow-[0_1px_3px_0_rgb(15_23_42/0.04)]'
            }`}
          >
            <div className="whitespace-pre-wrap break-words">
              <FormattedText text={textContent} isUser={isUser} />
            </div>

            {/* Custom Tool usage info */}
            {message.tools && message.tools.length > 0 && (
              <div className="mt-3.5 flex gap-2 flex-wrap">
                {message.tools.map(t => (
                  <span 
                    key={t.name} 
                    className="inline-flex items-center gap-1.5 font-mono text-[10.5px] font-medium text-slate-600 bg-slate-50 border border-slate-200/60 px-2 py-0.5 rounded-lg hover:bg-slate-100/80 hover:text-slate-800 transition-colors shadow-sm cursor-help select-all"
                    title={`Tool run: ${t.name}`}
                  >
                    <Terminal className="w-3.5 h-3.5 text-slate-400" />
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
                            ? 'border-violet-500 text-violet-600 bg-violet-50/20' 
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
                  <Loader2 className="w-4 h-4 animate-spin text-violet-500" />
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

  // Under development view (beautifully redesigned)
  if (!DEV_MODE) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8 text-center bg-slate-50/40">
        <div className="relative mb-6">
          <div className="absolute inset-0 bg-amber-400/20 rounded-full blur-xl animate-pulse" />
          <div className="relative bg-white border border-slate-100 p-5 rounded-2xl shadow-md">
            <Construction className="w-10 h-10 text-amber-500" />
          </div>
        </div>
        <h1 className="text-2xl font-bold text-slate-900 mb-2">Agent Chat Redesign</h1>
        <p className="text-sm text-slate-500 max-w-md mb-6 leading-relaxed">
          The agent chat interface is optimized for local environments to enable direct CloudWatch and database integration.
        </p>
        <Card className="max-w-md bg-white border border-slate-200 rounded-2xl p-5 shadow-sm text-left">
          <div className="flex gap-3">
            <div className="w-5 h-5 rounded-full bg-violet-50 flex items-center justify-center text-violet-600 flex-shrink-0 mt-0.5">
              <Info className="w-3.5 h-3.5" />
            </div>
            <div>
              <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-1">Local Development</h3>
              <p className="text-xs text-slate-500 leading-relaxed mb-3">
                Launch the local dev workflow to enable Electron container tracing and tool executions:
              </p>
              <code className="block bg-slate-50 border border-slate-100 p-2 rounded-lg font-mono text-[11px] text-slate-700 select-all mb-3">
                npm run dev
              </code>
              <p className="text-[11px] text-slate-400 leading-relaxed">
                You can also design complex templates via the <span className="font-semibold text-slate-600">Workflow</span> designer screen.
              </p>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  // Filter agents by search query
  const filteredAgents = agents.filter(agent => 
    agent.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex h-screen bg-slate-50 select-none overflow-hidden w-full max-w-full">
      
      {/* Sub-Sidebar: Left - Agent List */}
      <Card className="w-[280px] border-r border-slate-200 rounded-none flex flex-col h-full bg-white flex-shrink-0">
        <div className="p-4 border-b border-slate-100 h-20 flex flex-col justify-center gap-0.5">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-slate-800 text-sm tracking-wide">Incident Agents</h2>
            <Button 
              size="icon" 
              variant="ghost" 
              onClick={loadAgents} 
              disabled={isLoading}
              title="Refresh Agents List"
              className="w-8 h-8 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-50"
            >
              <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
          <p className="text-[11px] text-slate-400 font-medium">
            Select an agent to run diagnostic trace
          </p>
        </div>

        {/* Sub-Sidebar Search */}
        <div className="px-4 py-2.5 border-b border-slate-100">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
            <Input 
              placeholder="Search agents..." 
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              className="pl-8 h-8 text-xs bg-slate-50/50 border-slate-200 focus-visible:bg-white focus:border-violet-300 focus:ring-0 hover:bg-slate-50/80 transition-all rounded-lg"
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
                    className={`w-full flex items-center justify-between px-3 py-3 rounded-xl transition-all duration-300 cursor-pointer border ${
                      isSelected 
                        ? 'bg-violet-50/40 text-slate-800 border-violet-100 shadow-sm' 
                        : 'text-slate-600 border-transparent hover:bg-slate-50/80'
                    } ${isLoading ? 'opacity-65 cursor-not-allowed' : ''}`}
                  >
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors ${
                        isSelected 
                          ? 'bg-violet-600 text-white shadow-md shadow-violet-100' 
                          : 'bg-slate-100 text-slate-500'
                      }`}>
                        <Bot className="w-4 h-4" />
                      </div>
                      <div className="flex-1 text-left min-w-0">
                        <div className="font-semibold text-xs text-slate-700 truncate">{agent.name}</div>
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
                        className={`w-7 h-7 rounded-md opacity-0 group-hover:opacity-100 transition-opacity hover:bg-slate-100 hover:text-slate-800 ${isSelected ? 'opacity-100' : ''}`}
                        onClick={(e) => { e.stopPropagation(); triggerAgent(agent); }}
                        disabled={isLoading}
                        title="Run Agent Workflow"
                      >
                        <Play className="w-3.5 h-3.5 text-violet-600 fill-violet-600/10" />
                      </Button>
                    </div>
                  </div>
                  
                  {/* Sidebar indicator bar */}
                  {isSelected && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-7 bg-violet-600 rounded-r-full shadow-md" />
                  )}
                </div>
              )
            })
          )}
        </div>
      </Card>

      {/* Main Column: Chat Panel */}
      <div className="flex-grow flex flex-col h-full bg-[#fafbfc] min-w-0 relative">
        
        {/* Top Header Bar */}
        <header className="h-20 px-8 border-b border-slate-200/80 bg-white flex items-center justify-between flex-shrink-0 z-10 shadow-[0_1px_2px_0_rgba(15,23,42,0.01)]">
          <div className="flex items-center gap-3 min-w-0">
            <IconChip tint="violet">
              <Brain className="w-5 h-5" />
            </IconChip>
            <div className="min-w-0">
              <h1 className="font-bold text-sm text-slate-800 tracking-wide truncate">
                {selectedAgent ? selectedAgent.name : "OnCall orchestrator"}
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
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500"></span>
            </span>
            Online
          </Badge>
        </header>

        {/* Scrollable Conversation Stream */}
        <div className="flex-grow overflow-auto px-8 py-6 flex flex-col gap-1 select-text">
          {messages.map(renderMessage)}
          <div ref={messagesEndRef} />
        </div>

        {/* Dynamic Tools Floating Drawer */}
        {showToolsList && (
          <div className="absolute bottom-28 left-8 z-20 w-80 bg-white border border-slate-200 rounded-2xl shadow-xl p-4 animate-in fade-in slide-in-from-bottom-3 duration-300">
            <div className="flex items-center justify-between border-b pb-2 mb-2">
              <h3 className="text-xs font-bold text-slate-700 uppercase tracking-wider flex items-center gap-1.5">
                <Wrench className="w-3.5 h-3.5 text-violet-500" />
                Connected Tools ({toolsCount})
              </h3>
              <Button 
                variant="ghost" 
                size="icon" 
                className="w-5 h-5 rounded-full hover:bg-slate-100" 
                onClick={() => setShowToolsList(false)}
              >
                <XCircle className="w-3.5 h-3.5 text-slate-400" />
              </Button>
            </div>
            <div className="space-y-1.5 max-h-52 overflow-auto pr-1">
              {toolsList.map(t => (
                <div key={t} className="flex items-center gap-2.5 p-2 hover:bg-slate-50 rounded-xl text-xs transition-colors border border-transparent hover:border-slate-100">
                  <div className="w-6 h-6 rounded-md bg-slate-50 flex items-center justify-center flex-shrink-0 text-slate-500">
                    <Terminal className="w-3.5 h-3.5" />
                  </div>
                  <span className="font-mono text-slate-700 font-semibold truncate">{t}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Input tray panel */}
        <div className="p-8 pt-0 bg-transparent flex-shrink-0 z-10">
          <div className="bg-white border border-slate-200 rounded-2xl p-3 flex flex-col gap-3 shadow-md hover:border-slate-300/80 transition-colors focus-within:border-violet-300 focus-within:ring-2 focus-within:ring-violet-500/10">
            
            {/* Multi-line chat Textarea */}
            <textarea 
              ref={inputRef}
              value={inputValue} 
              onChange={e => setInputValue(e.target.value)} 
              onKeyDown={handleKeyPress}
              placeholder="Ask the agent to investigate, summarize, or run a workflow…"
              disabled={isLoading}
              rows={2}
              className="w-full min-h-[44px] max-h-48 border-none outline-none resize-none font-sans text-sm text-slate-800 bg-transparent placeholder-slate-400 focus:ring-0 p-1"
            />
            
            {/* Input Footer row */}
            <div className="flex items-center justify-between border-t border-slate-50 pt-2.5">
              <div className="flex gap-2">
                <Button 
                  variant="ghost" 
                  size="sm" 
                  onClick={() => setShowToolsList(!showToolsList)}
                  className={`h-8 rounded-lg text-xs font-semibold text-slate-500 hover:bg-slate-50 hover:text-slate-700 ${showToolsList ? 'bg-violet-50 text-violet-600 hover:bg-violet-50 hover:text-violet-700' : ''}`}
                >
                  @ tools
                </Button>
                
                <Button 
                  variant="ghost" 
                  size="sm"
                  onClick={() => navigate('/scheduler')}
                  className="h-8 rounded-lg text-xs font-semibold text-slate-500 hover:bg-slate-50 hover:text-slate-700"
                >
                  <Calendar className="w-3.5 h-3.5 mr-1.5" />
                  Schedule
                </Button>
                
                <span className="font-mono text-[10px] text-slate-400 font-semibold bg-slate-50 border border-slate-100 rounded-md px-2 py-0.5 flex items-center select-none ml-1">
                  {modelName}
                </span>
              </div>
              
              <Button 
                onClick={handleSendMessage}
                disabled={!inputValue.trim() || isLoading}
                className="h-8 px-4 rounded-xl text-xs font-semibold bg-violet-600 hover:bg-violet-700 text-white flex items-center gap-1.5 shadow-md shadow-violet-600/10 cursor-pointer disabled:bg-slate-100 disabled:text-slate-400 disabled:shadow-none"
              >
                {isLoading ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                Send
              </Button>
            </div>

          </div>
        </div>

      </div>

      {/* Column 3: Run Inspector (Right panel) */}
      <aside className="w-[300px] border-l border-slate-200 bg-white flex flex-col h-full overflow-hidden flex-shrink-0 z-10 shadow-[0_-1px_3px_rgba(15,23,42,0.01)] animate-in slide-in-from-right duration-300">
        
        {/* Header */}
        <div className="p-4 border-b border-slate-100 flex items-center h-20 justify-between">
          <div className="font-bold text-xs text-slate-400 uppercase tracking-widest flex items-center gap-1.5">
            <Activity className="w-4 h-4 text-violet-500" />
            Trace · this turn
          </div>
          {isLoading && (
            <Badge variant="outline" className="px-2 py-0 text-[9px] uppercase tracking-wider text-violet-600 bg-violet-50 border-violet-100 border font-bold animate-pulse">
              Live Running
            </Badge>
          )}
        </div>
        
        {/* Timeline container */}
        <div className="flex-1 overflow-auto p-5 relative">
          <div className="flex flex-col gap-0 position-relative pl-5">
            
            {/* Connecting Timeline Thread */}
            <div className="absolute left-7 top-7 bottom-7 w-[1.5px] bg-slate-100" />
            
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
                <div key={i} className="relative pb-6 last:pb-2 group animate-in fade-in slide-in-from-bottom-2 duration-300">
                  {/* Dot icon indicator */}
                  <span className={`absolute -left-[19px] top-1.5 w-2.5 h-2.5 rounded-full border-2 border-white transition-all duration-300 ${colorClasses}`} />
                  
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
