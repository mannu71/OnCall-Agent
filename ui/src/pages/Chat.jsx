import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
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
  Bot,
  User,
  RefreshCw,
  XCircle,
  Construction,
  Brain,
  Wrench,
  Terminal,
  Activity,
  Database,
  AlertCircle,
  Search,
  Cpu,
  CornerDownLeft,
  Info,
  Plus,
  History,
  Trash2,
  Pin,
  MessageSquare,
  Copy,
  Check,
  RotateCcw,
  ListTree,
  Square
} from 'lucide-react';
import { isAgentWorkflowValid } from '../utils/workflowValidation.js';
import agentApiClient, { isAbortError } from '../services/agentApiClient.js';
import ChatMessage, { MESSAGE_TYPES } from '../components/chat/ChatMessage.jsx';
import SendButton from '../components/chat/SendButton.jsx';
import DensityToggle, { DENSITIES } from '../components/chat/DensityToggle.jsx';
import TraceTimeline from '../components/chat/TraceTimeline.jsx';
import { formatClock } from '../lib/formatTime.js';
import { useChatSessionsQuery } from '../hooks/queries/useChatSessionsQuery.js';
import { queryKeys } from '../lib/queryKeys.js';

// localStorage key for the active chat session, so a refresh / revisit re-opens
// the same conversation (and its context) instead of starting a new session.
const SESSION_STORAGE_KEY = 'currentSessionId';

function persistSessionId(id) {
  if (typeof localStorage === 'undefined') return;
  try {
    if (id) localStorage.setItem(SESSION_STORAGE_KEY, id);
    else localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch { /* storage unavailable — best-effort */ }
}

// Client-side mirror of the backend trace_ids detector (cosmetic badge only —
// the backend remains the source of truth for the correlation fast-path).
const CORRELATION_ID_RE = /\b(1-[0-9a-f]{8}-[0-9a-f]{24}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9A-Z]{10,}:[0-9A-Fa-f]{8})\b/;
const TIME_RANGE_HINT_RE = /\b(last|over|past|\d+\s*(m|min|mins|h|hr|hour|hours|d|day|days))\b/i;

// Mirror of the backend conversational-intent heuristic (app/core/intent.py):
// don't scope a greeting/thanks with a CloudWatch time range. Biased to False so
// a real query is never mistaken for small talk.
const SMALL_TALK_RE = /^\s*(hi|hii+|hey|hello|hiya|yo|sup|good\s*(morning|afternoon|evening)|greetings|thanks(\s*you)?|thank\s*you|thx|ty|cheers|ok(ay)?|cool|nice|great|got\s*it|bye|goodbye|see\s*ya|who\s*are\s*you|what\s*are\s*you|what\s*can\s*you\s*do|what\s*do\s*you\s*do|help|how\s*are\s*you)[\s!.,?]*$/i;
const INVESTIGATION_HINT_RE = /\b(error|errors|fail|failed|failing|failure|exception|log|logs|trace|stack|crash|timeout|latency|slow|alarm|alert|5xx|4xx|500|503|throttl|outage|down|spike|spiking|anomaly|why|debug|investigate|root\s*cause|rca|repo|deploy|rollback|metric|cpu|memory|leak|query|database|sql|endpoint|api|status\s*code)\b/i;

/** True only for clearly conversational messages (no investigation signal). */
function isSmallTalk(text) {
  const t = (text || '').trim();
  if (!t || t.length > 64) return false;
  if (INVESTIGATION_HINT_RE.test(t)) return false;
  return SMALL_TALK_RE.test(t);
}

/** True when an agent workflow contains a CloudWatch node (so it can trace logs). */
function agentHasCloudWatch(agent) {
  const nodes = agent?.nodes || [];
  return nodes.some(n => n?.type === 'cloudwatch_tool' || n?.type === 'cloudwatchAnalyzer');
}

/** Human-readable model label for header/composer chips (handles arrays + comma lists). */
function formatModelLabel(raw) {
  if (raw == null || raw === '' || raw === '—') return '—';
  if (Array.isArray(raw)) return raw.filter(Boolean).join(', ');
  return String(raw).replace(/,\s*/g, ', ').trim();
}

// Use Vite's environment check for development mode
const DEV_MODE = import.meta.env.DEV;

// Message types imported from ChatMessage.jsx

// Fresh-conversation seed — a single welcome note, then real turns only.
const welcomeMessage = () => ({
  id: 'init-1',
  type: MESSAGE_TYPES.SYSTEM,
  content: 'Welcome! Select an agent from the sidebar, then ask it anything to get started.',
  timestamp: new Date().toISOString(),
});

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

function Chat() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
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

  // Persistent chat sessions (migration 022). `sessionId` is created lazily on
  // the first message of a fresh chat; resuming hydrates messages from the API.
  const [sessions, setSessions] = useState([]);
  const { data: sessionsData } = useChatSessionsQuery();

  useEffect(() => {
    if (sessionsData) setSessions(sessionsData);
  }, [sessionsData]);

  const invalidateSessions = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: queryKeys.sessions({}) });
  }, [queryClient]);
  const [sessionId, setSessionId] = useState(null);
  const [sessionSheetOpen, setSessionSheetOpen] = useState(false);
  const [traceDrawerOpen, setTraceDrawerOpen] = useState(false);

  // Conversation density (compact | comfortable | spacious), persisted locally.
  const [density, setDensity] = useState(() => {
    if (typeof localStorage === 'undefined') return 'comfortable';
    const v = localStorage.getItem('chatDensity');
    return DENSITIES.includes(v) ? v : 'comfortable';
  });
  const changeDensity = (d) => {
    setDensity(d);
    try { localStorage.setItem('chatDensity', d); } catch { /* ignore */ }
  };

  // Composer send-button launch animation (transient).
  const [sendLaunching, setSendLaunching] = useState(false);

  // Per-message copy feedback (id of the message just copied → ✓ for a moment).
  const [copiedId, setCopiedId] = useState(null);
  const copyMessage = useCallback(async (msg) => {
    try {
      await navigator.clipboard.writeText(msg.content || msg.text || '');
      setCopiedId(msg.id);
      setTimeout(() => setCopiedId((c) => (c === msg.id ? null : c)), 1500);
    } catch { /* clipboard may be blocked; ignore */ }
  }, []);

  // System-adaptive theme (native-macOS look). Dark mode is class-based
  // (.dark) app-wide, so we scope it to the Chat root only — following the OS
  // appearance via prefers-color-scheme without theming the rest of the app.
  const [sysDark, setSysDark] = useState(
    () => typeof window !== 'undefined'
      && window.matchMedia
      && window.matchMedia('(prefers-color-scheme: dark)').matches
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (e) => setSysDark(e.matches);
    mq.addEventListener?.('change', onChange);
    return () => mq.removeEventListener?.('change', onChange);
  }, []);
  // Toggle the class-based dark theme at the document root while the Chat page
  // is mounted, so dark: styles also reach Radix portals (the privacy Sheet,
  // selects) which render outside the Chat subtree. Reverted on unmount, so the
  // restyle stays scoped to the Chat page.
  useEffect(() => {
    const root = document.documentElement;
    const had = root.classList.contains('dark');
    if (sysDark) root.classList.add('dark');
    else root.classList.remove('dark');
    return () => { if (!had) root.classList.remove('dark'); };
  }, [sysDark]);

  // Conversation starts clean — a single welcome note, then real turns only.
  const [messages, setMessages] = useState([welcomeMessage()]);

  // Stepper timeline — populated live from real run events (empty until a run starts).
  const [traceSteps, setTraceSteps] = useState([]);

  // Token metrics — zero until a real run reports usage.
  const [tokens, setTokens] = useState({ input: 0, output: 0, total: 0 });
  // Session-cumulative tokens (finalized on the SESSION, not per-turn/workflow
  // run — see chat_sessions.total_* columns, migration 028). Seeded from the
  // persisted session on resume, incremented locally as each turn completes.
  const [sessionTokens, setSessionTokens] = useState({ input: 0, output: 0, cacheRead: 0, cacheCreation: 0 });
  // Context-window fullness for the LATEST turn (not cumulative) — how full
  // the model's context window is right now. Mirrors claude-code-main's
  // context bar: current-turn fullness, color-coded as it fills.
  const [contextUsage, setContextUsage] = useState({ pct: 0, used: 0, window: 0 });
  // A single model turn can emit SEVERAL gated tool calls (e.g. multiple
  // edit_file), each needing its own approval. Track them as a FIFO queue and
  // surface the head; resolving one (by its requestId) reveals the next.
  const [pendingApprovals, setPendingApprovals] = useState([]); // [{ tool, args, requestId, executionId }]
  const pendingApproval = pendingApprovals[0] || null;

  // ── Stuck-run recovery: abort handle + silence watchdog ──────────────────
  // The composer used to wait indefinitely on the synchronous /execute POST;
  // if the backend orphaned a run and never returned, isLoading stayed true
  // forever with no way to Stop or send a new message. Two independent
  // safeguards now exist:
  //  1. abortControllerRef lets handleStop cancel the in-flight POST directly
  //     from the client, so the composer unlocks immediately regardless of
  //     what the server-side cancel does.
  //  2. The watchdog flags a run as possibly-stuck after a period of total
  //     silence (no SSE token/tool/status event) so Stop is never the user's
  //     only recourse if they don't think to click it.
  const abortControllerRef = useRef(null);
  const lastActivityRef = useRef(0);
  const [watchdogStale, setWatchdogStale] = useState(false);
  const WATCHDOG_SILENCE_MS = 90_000;
  const WATCHDOG_POLL_MS = 15_000;

  useEffect(() => {
    if (!isLoading) { setWatchdogStale(false); return undefined; }
    const id = setInterval(() => {
      if (Date.now() - lastActivityRef.current > WATCHDOG_SILENCE_MS) {
        setWatchdogStale(true);
      }
    }, WATCHDOG_POLL_MS);
    return () => clearInterval(id);
  }, [isLoading]);

  const agentDetails = useMemo(() => {
    if (!selectedAgent) {
      return { modelName: '—', toolsCount: 0, toolsList: [] };
    }

    const llmNode = selectedAgent.nodes?.find(n => n.type === 'llm' || n.type === 'language_model');
    const rawModel = llmNode?.params?.llm || llmNode?.params?.model || llmNode?.params?.modelId
      || llmNode?.data?.model || llmNode?.data?.modelId || llmNode?.data?.label
      || llmNode?.name || '—';
    const modelName = formatModelLabel(rawModel);

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

    return { modelName, toolsCount: toolsList.length, toolsList };
  }, [selectedAgent]);

  const { modelName, toolsCount, toolsList } = agentDetails;

  // Load agent-type workflows + past chat sessions, then restore the last-active
  // session so a page refresh / revisit re-opens the same conversation (and its
  // context) instead of silently starting a new one.
  useEffect(() => {
    (async () => {
      const loadedAgents = await loadAgents();
      invalidateSessions();
      try {
        const stored = typeof localStorage !== 'undefined'
          ? localStorage.getItem(SESSION_STORAGE_KEY) : null;
        if (stored) await resumeSession(stored, loadedAgents);
      } catch (error) {
        console.warn('Could not restore previous chat session:', error);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      const agentWorkflows = workflows.filter(wf =>
        wf.type === 'agent' && wf.enabled && isAgentWorkflowValid(wf)
      );
      setAgents(agentWorkflows);
      return agentWorkflows;
    } catch (error) {
      console.error('Error loading agents:', error);
      setAgents([]);
      if (error.code === 'ERR_NETWORK' || error.message.includes('Network Error')) {
        console.warn('Cannot connect to Agent API. Please start the API server at http://localhost:8000');
      }
      return [];
    }
  };

  const ensureSession = async (agent, firstMessage) => {
    if (sessionId) return sessionId;
    try {
      const created = await agentApiClient.createSession({
        title: (firstMessage || 'New chat').slice(0, 60),
        workflowName: agent?.name || null,
      });
      setSessionId(created.id);
      persistSessionId(created.id);
      return created.id;
    } catch (error) {
      console.error('Error creating session:', error);
      return null;
    }
  };

  const startNewChat = () => {
    setSessionId(null);
    persistSessionId(null);
    setMessages([welcomeMessage()]);
    setTraceSteps([]);
    setTokens({ input: 0, output: 0, total: 0 });
    setSessionTokens({ input: 0, output: 0, cacheRead: 0, cacheCreation: 0 });
    setContextUsage({ pct: 0, used: 0, window: 0 });
    setPendingApprovals([]);
    setSessionSheetOpen(false);
  };

  const resumeSession = async (id, agentsList = agents) => {
    try {
      const data = await agentApiClient.getSession(id);
      const hydrated = (data.messages || []).map((m) => ({
        id: `db-${m.id}`,
        type: m.role === 'user' ? MESSAGE_TYPES.USER
          : m.role === 'assistant' ? MESSAGE_TYPES.AGENT : MESSAGE_TYPES.SYSTEM,
        content: m.content,
        timestamp: m.created_at,
        isMarkdown: m.role === 'assistant',
        privacyRedactions: m.metadata?.privacy_redactions || [],
        selectedSkills: m.metadata?.selected_skills || [],
      }));
      setSessionId(id);
      persistSessionId(id);
      setMessages(hydrated.length ? hydrated : [welcomeMessage()]);
      setTraceSteps([]);
      setTokens({ input: 0, output: 0, total: 0 });
      setSessionTokens({
        input: data.total_input_tokens || 0,
        output: data.total_output_tokens || 0,
        cacheRead: data.total_cache_read_tokens || 0,
        cacheCreation: data.total_cache_creation_tokens || 0,
      });
      // Context fullness is per-turn — restore it from the last assistant
      // message's metadata so a resumed session shows where it left off.
      const lastAssistantMsg = [...(data.messages || [])].reverse().find(m => m.role === 'assistant');
      const lastCtx = lastAssistantMsg?.metadata || {};
      setContextUsage({
        pct: lastCtx.context_used_pct || 0,
        used: lastCtx.context_used_tokens || 0,
        window: lastCtx.context_window_size || 0,
      });
      if (data.workflow_name) {
        const match = agentsList.find((a) => a.name === data.workflow_name);
        if (match) setSelectedAgent(match);
      }
      setSessionSheetOpen(false);
    } catch (error) {
      console.error('Error resuming session:', error);
      if (error?.response?.status === 404 || error?.status === 404) {
        persistSessionId(null);
        setSessionId(null);
      }
    }
  };

  const deleteSession = async (id, e) => {
    e?.stopPropagation();
    try {
      await agentApiClient.deleteSession(id);
      if (id === sessionId) startNewChat();
      invalidateSessions();
    } catch (error) {
      console.error('Error deleting session:', error);
    }
  };

  const togglePinSession = async (sess, e) => {
    e?.stopPropagation();
    try {
      await agentApiClient.updateSession(sess.id, { isImportant: !sess.is_important });
      invalidateSessions();
    } catch (error) {
      console.error('Error pinning session:', error);
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

  const handleSendMessage = async () => {
    if (!inputValue.trim() || isLoading) return;

    const userMessage = inputValue.trim();
    setInputValue('');

    addMessage(MESSAGE_TYPES.USER, userMessage, { hasTraceId: CORRELATION_ID_RE.test(userMessage) });

    // Append the selected time range hint ONLY for CloudWatch agents (and only
    // when the user didn't already give one). Generic workflows get the raw text.
    const effectiveAgent = selectedAgent || agents[0];
    const scopeForCw = effectiveAgent && agentHasCloudWatch(effectiveAgent);
    const question = (scopeForCw && !TIME_RANGE_HINT_RE.test(userMessage) && !isSmallTalk(userMessage))
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
    // Ensure a persisted session exists so this turn is recorded against it.
    const sid = await ensureSession(agent, question);

    // Reset trace logs for this turn
    setTraceSteps([
      { l: "think", t: "10ms", text: `Initializing agent run for: "${agent.name}"...` },
      { l: "think", t: "18ms", text: `Received request: "${question.substring(0, 50)}${question.length > 50 ? '...' : ''}"` }
    ]);

    const thinkingId = addMessage(MESSAGE_TYPES.AGENT, '', { isLoading: true, statusHistory: [], steps: [], streaming: false });
    setIsLoading(true);

    // Fresh abort handle for this turn — handleStop() calls .abort() on it to
    // cancel the in-flight POST client-side. Reset the watchdog clock too.
    const controller = new AbortController();
    abortControllerRef.current = controller;
    lastActivityRef.current = Date.now();
    setWatchdogStale(false);

    // Dynamic metrics simulation
    const startTime = Date.now();

    try {
      // Run via the FastAPI HTTP/SSE bridge. Live tool/token events stream into the trace
      // panel; the final answer renders as rich Markdown.
      const pushTrace = (l, text) =>
        setTraceSteps(prev => {
          const last = prev[prev.length - 1];
          if (last && last.text === text) return prev;
          return [...prev, { l, t: '—', text }];
        });
      const pushStatus = (msg) => {
        if (!msg) return;
        lastActivityRef.current = Date.now();
        setMessages(prev => prev.map(m => m.id === thinkingId
          ? {
              ...m,
              statusHistory: [
                ...((m.statusHistory) || []),
                { type: 'thinking', message: msg, time: new Date().toLocaleTimeString() },
              ].slice(-8),
              currentStatus: { message: msg },
            }
          : m));
      };

      // ── Live answer streaming (closure-local to this turn) ──────────────
      // Tokens arrive rapidly over SSE; accumulate and flush on a ~40ms timer
      // so React isn't re-rendered per token. The authoritative sync result
      // overwrites this at the end, repairing any tokens dropped before the
      // SSE stream attached (the backlog replay covers most, this covers the rest).
      let streamedContent = '';
      let flushTimer = null;
      const flushStream = () => {
        flushTimer = null;
        // Only apply while the turn is live — a late timer must never clobber the
        // authoritative final answer or an error message (both clear isLoading).
        setMessages(prev => prev.map(m => (m.id === thinkingId && m.isLoading)
          ? { ...m, content: streamedContent, streaming: true, isMarkdown: true }
          : m));
      };
      const onToken = (tok) => {
        if (!tok) return;
        lastActivityRef.current = Date.now();
        streamedContent += tok;
        if (!flushTimer) flushTimer = setTimeout(flushStream, 40);
      };

      // ── Inline tool steps (collapsible cards in the conversation) ───────
      const steps = [];
      const syncSteps = () => setMessages(prev => prev.map(m =>
        m.id === thinkingId ? { ...m, steps: [...steps] } : m));
      const onStepCall = (name, args) => {
        lastActivityRef.current = Date.now();
        steps.push({ id: `${name}-${steps.length}`, name, args, status: 'running', startedAt: Date.now() });
        syncSteps();
      };
      const onStepResult = (name, result) => {
        lastActivityRef.current = Date.now();
        for (let i = steps.length - 1; i >= 0; i--) {
          if (steps[i].status === 'running' && steps[i].name === name) {
            steps[i] = { ...steps[i], status: 'done', result, durationMs: Date.now() - steps[i].startedAt };
            break;
          }
        }
        syncSteps();
      };
      const onStepError = (err) => {
        for (let i = steps.length - 1; i >= 0; i--) {
          if (steps[i].status === 'running') {
            steps[i] = { ...steps[i], status: 'error', result: err, durationMs: Date.now() - steps[i].startedAt };
            break;
          }
        }
        syncSteps();
      };

      // Conversation memory: replay prior completed turns so follow-ups keep
      // context. Includes SYSTEM-type messages — a per-chat-session
      // compaction summary (see compact_chat_session_if_needed) collapses
      // older turns into one of these, and it must survive the replay or
      // that context is silently lost. The current question is sent
      // separately as the query, so drop any trailing user turn equal to it.
      const history = (messages || [])
        .filter(m => (m.type === MESSAGE_TYPES.USER || m.type === MESSAGE_TYPES.AGENT || m.type === MESSAGE_TYPES.SYSTEM)
          && !m.isLoading && (m.content || m.text))
        .map(m => ({
          role: m.type === MESSAGE_TYPES.USER ? 'user' : m.type === MESSAGE_TYPES.SYSTEM ? 'system' : 'assistant',
          content: m.content || m.text,
        }))
        .slice(-12);
      while (history.length && history[history.length - 1].role === 'user'
             && history[history.length - 1].content === question) history.pop();

      const result = await agentApiClient.runAgentStream(agent.name, question, {
        history,
        sessionId: sid,
        signal: controller.signal,
        onToken,
        onToolCall: (name, args) => { onStepCall(name, args); pushTrace('tool', `Calling ${name}…`); pushStatus(`Calling ${name}…`); },
        onToolResult: (name, res) => { onStepResult(name, res); pushTrace('tool', `${name} returned`); },
        onNode: (nodeId, status) => pushTrace('think', `${nodeId} ${status}`),
        onStatus: (msg) => pushStatus(msg),
        onTokens: (t) => {
          // Fires after EVERY LLM call now, not just once at run end — this is
          // what makes the counters move live during a run AND survive a
          // mid-run crash (the last live value simply stays, since nothing
          // here or in the catch block below ever resets it to zero).
          lastActivityRef.current = Date.now();
          setTokens({ input: t.input, output: t.output, total: t.total });
          if (t.context) setContextUsage(t.context);
        },
        onHitlPause: (p) => {
          pushStatus(`Awaiting approval: ${p.tool}…`);
          // Append (dedup by requestId — SSE can redeliver on reconnect).
          setPendingApprovals(prev => prev.some(x => x.requestId === p.requestId) ? prev : [...prev, p]);
        },
        onError: (err) => { onStepError(err); pushTrace('answer', `Error: ${err}`); },
      });

      // Stop any pending streamed-text flush — the sync result is authoritative.
      if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }

      // Pull the structured InvestigationReport (Data Query Mode) out of the raw
      // node results, if the run produced one.
      let structured = null;
      let todos = null;
      try {
        const nodes = result?.raw?.results || {};
        for (const k of Object.keys(nodes)) {
          if (nodes[k] && nodes[k].structured_output) { structured = nodes[k].structured_output; break; }
        }
        // Deep-agent plan checklist (planning capability), if the run produced one.
        for (const k of Object.keys(nodes)) {
          if (nodes[k] && Array.isArray(nodes[k].todos) && nodes[k].todos.length) {
            todos = nodes[k].todos; break;
          }
        }
      } catch { /* ignore */ }

      setPendingApprovals([]);
      updateMessage(thinkingId, {
        content: result.finalAnswer || streamedContent || 'Agent completed (no answer text returned).',
        isLoading: false,
        streaming: false,
        isMarkdown: true,
        currentStatus: null,
        statusHistory: [],
        structured,
        todos,
        privacyRedactions: result.privacyRedactions || [],
        selectedSkills: result.selectedSkills || [],
        steps: [...steps],
      });
      pushTrace('answer', 'Returned final analysis + recommendations.');

      // result.tokens carries the backend's real, measured per-run counts
      // (agent_runner.py's TokenUsageCallback). Show exactly what was
      // measured — including a genuine 0 for a no-LLM-call turn — rather
      // than fabricating an estimate from message length when the field is
      // merely absent (e.g. an error before any LLM call was made).
      const inp = result.tokens?.input || 0;
      const out = result.tokens?.output || 0;
      setTokens({ input: inp, output: out, total: result.tokens?.total || (inp + out) });

      // Session-cumulative tokens (finalized on the session, not per-turn) —
      // increment locally rather than refetching the session list every turn.
      const cacheRead = result.raw?.cache_read_tokens || 0;
      const cacheCreation = result.raw?.cache_creation_tokens || 0;
      setSessionTokens(prev => ({
        input: prev.input + inp,
        output: prev.output + out,
        cacheRead: prev.cacheRead + cacheRead,
        cacheCreation: prev.cacheCreation + cacheCreation,
      }));

      // Context-window fullness for THIS turn (not cumulative) — how full the
      // model's context window is right now.
      setContextUsage({
        pct: result.raw?.context_used_pct || 0,
        used: result.raw?.context_used_tokens || 0,
        window: result.raw?.context_window_size || 0,
      });
    } catch (error) {
      // handleStop() already set "Stopped by user." on this message and
      // unlocked the composer directly — don't clobber that with a second,
      // redundant "Error: canceled" render.
      if (!isAbortError(error)) {
        updateMessage(thinkingId, {
          content: `Error: ${error.message}`,
          isLoading: false,
          isError: true
        });
        setTraceSteps(prev => [
          ...prev,
          { l: "answer", t: "—", text: `Fatal error: ${error.message}` }
        ]);
      }
    } finally {
      setIsLoading(false);
      invalidateSessions();
      if (abortControllerRef.current === controller) abortControllerRef.current = null;
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Hover-action: re-ask the last user turn (regenerate the assistant answer).
  const regenerateLast = useCallback(() => {
    if (isLoading || !selectedAgent) return;
    const lastUser = [...messages].reverse().find((m) => m.type === MESSAGE_TYPES.USER);
    if (lastUser?.content) askAgent(selectedAgent, lastUser.content);
  }, [isLoading, selectedAgent, messages]);

  const editMessage = useCallback((msg) => {
    setInputValue(msg.content || msg.text || '');
    setTimeout(() => inputRef.current?.focus(), 0);
  }, []);

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




  // Filter agents by search query
  const filteredAgents = agents.filter(agent =>
    agent.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // Composer send-button phase, derived from the live run: processing before the
  // answer streams, receiving once tokens flow.
  const liveMsg = messages.find((m) => m.isLoading);
  const sendPhase = !isLoading ? 'idle' : (liveMsg && liveMsg.streaming ? 'receiving' : 'processing');
  const handleSendClick = () => {
    setSendLaunching(true);
    setTimeout(() => setSendLaunching(false), 450);
    handleSendMessage();
  };

  const [isStopping, setIsStopping] = useState(false);
  const handleStop = async () => {
    if (isStopping || !isLoading) return;
    setIsStopping(true);
    // Client-side abort FIRST — this alone unlocks the composer even if the
    // server never responds to the cancel call below (e.g. it's the very
    // thing that's stuck). The pending POST's rejection is caught by
    // askAgent's catch block and ignored there (isAbortError), since this
    // function is the one source of truth for the "Stopped by user." message.
    abortControllerRef.current?.abort();
    try {
      if (selectedAgent?.name) {
        // Server-side: actually cancels the backend asyncio.Task (see
        // execution_state.cancel_execution) so the orphaned run stops for
        // real instead of continuing after the client walks away — this is
        // what clears is_workflow_running so the NEXT message isn't rejected
        // with already_running.
        await agentApiClient.cancelWorkflow(selectedAgent.name);
      }
    } catch (_) {
      // Server-side cancel failed or execution already finished — still clean up UI
    } finally {
      // Mark the in-flight message as stopped and release the composer
      setMessages((prev) =>
        prev.map((m) =>
          m.isLoading
            ? { ...m, isLoading: false, isError: true, content: 'Stopped by user.' }
            : m
        )
      );
      setIsLoading(false);
      setIsStopping(false);
    }
  };

  return (
    <div className={`${sysDark ? 'dark ' : ''}flex h-screen bg-[#f5f5f7] dark:bg-[#161618] text-slate-800 dark:text-slate-100 select-none overflow-hidden w-full max-w-full`}>

      {/* Sub-Sidebar: Left - Agent List */}
      <aside className="hidden md:flex w-[240px] lg:w-[280px] border-r border-black/[0.06] dark:border-white/10 flex-col h-full bg-white/70 dark:bg-[#1c1c1e]/70 backdrop-blur-xl flex-shrink-0">
        <div className="p-4 border-b border-slate-100 h-20 flex flex-col justify-center gap-0.5">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-slate-800 dark:text-slate-100 text-sm tracking-wide">Agents</h2>
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
                      "w-full flex items-center px-3 py-3 rounded-xl transition-all duration-300 cursor-pointer border",
                      isSelected
                        ? 'bg-primary/10 text-slate-800 dark:text-slate-100 border-primary/30'
                        : 'text-slate-600 dark:text-slate-300 border-transparent hover:bg-black/[0.03] dark:hover:bg-white/[0.05]',
                      isLoading && 'opacity-65 cursor-not-allowed'
                    )}
                  >
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <div className={cn(
                        "size-8 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors",
                        isSelected
                          ? 'bg-primary text-primary-foreground shadow-sm shadow-primary/30'
                          : 'bg-slate-100 dark:bg-white/[0.06] text-slate-500 dark:text-slate-400'
                      )}>
                        <Bot className="size-4" />
                      </div>
                      <div className="flex-1 text-left min-w-0">
                        <div className="font-semibold text-xs text-slate-700 dark:text-slate-200 truncate flex items-center gap-1.5">
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
      <div className="flex-grow flex flex-col h-full bg-[#f5f5f7] dark:bg-[#161618] min-w-0 relative">

        {/* Top Header Bar */}
        <header className="min-h-16 sm:h-20 px-3 sm:px-4 md:px-8 py-2.5 sm:py-0 border-b border-black/[0.06] dark:border-white/10 bg-white/70 dark:bg-[#1c1c1e]/70 backdrop-blur-xl flex items-center gap-2 sm:gap-4 flex-shrink-0 z-10 overflow-hidden">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1 overflow-hidden">
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
            <div className="min-w-0 flex-1 overflow-hidden">
              <h1 className="font-bold text-sm text-slate-800 dark:text-slate-100 tracking-wide truncate leading-tight">
                {selectedAgent ? selectedAgent.name : 'Select an agent'}
              </h1>
              <div className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5 mt-0.5 font-sans min-w-0">
                <span
                  className="min-w-0 truncate max-w-[min(100%,12rem)] sm:max-w-[min(100%,18rem)] bg-slate-50 dark:bg-white/[0.06] border border-slate-100 dark:border-white/10 px-1.5 py-0.5 rounded font-mono text-[10px] text-slate-500 dark:text-slate-400 font-semibold leading-none"
                  title={modelName}
                >
                  {modelName}
                </span>
                <span className="text-slate-200 dark:text-slate-600 shrink-0">·</span>
                <span className="shrink-0 whitespace-nowrap">{toolsCount} tool{toolsCount === 1 ? '' : 's'}</span>
              </div>
            </div>
          </div>

          {/* Context-window fullness (this turn) + session-cumulative tokens.
              Context bar mirrors claude-code-main's status line: current-turn
              fullness, color-coded, NOT the session total (tracked separately
              below — finalized on the session, not per-workflow-run). */}
          {contextUsage.window > 0 && (
            <div className="hidden md:flex items-center gap-3 shrink-0 px-2" title={`${contextUsage.used.toLocaleString()} / ${contextUsage.window.toLocaleString()} tokens this turn`}>
              <div className="flex items-center gap-1.5">
                <div className="w-16 h-1.5 rounded-full bg-slate-100 dark:bg-white/10 overflow-hidden">
                  <div
                    className={cn(
                      'h-full rounded-full transition-all duration-500',
                      contextUsage.pct >= 90 ? 'bg-red-500' : contextUsage.pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500'
                    )}
                    style={{ width: `${Math.max(4, contextUsage.pct)}%` }}
                  />
                </div>
                <span className={cn(
                  'text-[10px] font-semibold tabular-nums',
                  contextUsage.pct >= 90 ? 'text-red-600' : contextUsage.pct >= 70 ? 'text-amber-600' : 'text-slate-400'
                )}>
                  {contextUsage.pct}%
                </span>
              </div>
              {(sessionTokens.input + sessionTokens.output) > 0 && (
                <span
                  className="text-[10px] text-slate-400 font-medium whitespace-nowrap"
                  title={`Session total — in: ${sessionTokens.input.toLocaleString()}, out: ${sessionTokens.output.toLocaleString()}, cache-read: ${sessionTokens.cacheRead.toLocaleString()}`}
                >
                  {(sessionTokens.input + sessionTokens.output).toLocaleString()} session tok
                </span>
              )}
            </div>
          )}

          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            <DensityToggle value={density} onChange={changeDensity} />

            <Button
              variant="outline"
              size="sm"
              onClick={startNewChat}
              title="Start a new chat"
              className="h-8 rounded-lg text-xs font-semibold gap-1.5 px-2 sm:px-3"
            >
              <Plus className="size-3.5 shrink-0" />
              <span className="hidden sm:inline">New chat</span>
            </Button>

            {/* Trace drawer toggle */}
            <Button
              variant="outline"
              size="icon"
              onClick={() => setTraceDrawerOpen(true)}
              title="Run trace"
              className={cn('size-8 shrink-0 rounded-lg relative', isLoading && 'text-primary border-primary/40')}
            >
              <Activity className="size-4" />
              {isLoading && (
                <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-primary animate-pulse" />
              )}
            </Button>

            {/* Conversation history (persisted sessions) */}
            <Sheet open={sessionSheetOpen} onOpenChange={(o) => { setSessionSheetOpen(o); if (o) invalidateSessions(); }}>
              <SheetTrigger asChild>
                <Button variant="outline" size="icon" className="size-8 shrink-0 rounded-lg" title="Chat history">
                  <History className="size-4" />
                </Button>
              </SheetTrigger>
              <SheetContent side="right" className="w-[320px] p-0 gap-0">
                <SheetHeader className="p-4 border-b border-slate-100 dark:border-white/10">
                  <SheetTitle className="text-sm flex items-center gap-2">
                    <MessageSquare className="size-4 text-primary" /> Conversations
                  </SheetTitle>
                </SheetHeader>
                <div className="p-2 overflow-auto h-[calc(100vh-64px)]">
                  {sessions.length === 0 ? (
                    <p className="px-3 py-3 text-xs text-slate-500">No saved conversations yet. Ask an agent something to start one.</p>
                  ) : sessions.map((s) => (
                    <div
                      key={s.id}
                      onClick={() => resumeSession(s.id)}
                      className={cn(
                        'group w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-left cursor-pointer transition-colors',
                        s.id === sessionId ? 'bg-primary/10' : 'hover:bg-black/[0.03] dark:hover:bg-white/[0.05]'
                      )}
                    >
                      <MessageSquare className="size-4 text-slate-400 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-semibold text-slate-700 dark:text-slate-200 truncate">{s.title}</div>
                        <div className="text-[10px] text-slate-400">{s.message_count} msgs · {formatClock(s.last_message_at || s.updated_at)}</div>
                      </div>
                      <button
                        onClick={(e) => togglePinSession(s, e)}
                        title={s.is_important ? 'Unpin' : 'Pin'}
                        className={cn(
                          'size-6 rounded-md flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-slate-100 dark:hover:bg-white/10',
                          s.is_important && 'opacity-100 text-amber-500'
                        )}
                      >
                        <Pin className="size-3.5" />
                      </button>
                      <button
                        onClick={(e) => deleteSession(s.id, e)}
                        title="Delete conversation"
                        className="size-6 rounded-md flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-500/10"
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              </SheetContent>
            </Sheet>

            <Badge
              variant="success"
              className="hidden sm:flex px-2.5 py-0.5 rounded-full items-center gap-1.5 text-[11px] font-semibold transition-all duration-300 shrink-0"
            >
              <span className="relative flex size-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full size-1.5 bg-emerald-500"></span>
              </span>
              Online
            </Badge>
          </div>
        </header>

        {/* Scrollable Conversation Stream — centered single-column rail */}
        <div className="flex-grow overflow-auto px-4 md:px-8 py-6 select-text">
          <div className={cn('mx-auto w-full max-w-[var(--chat-max)] flex flex-col', `chat-density-${density}`)}>
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                message={message}
                copiedId={copiedId}
                isLoading={isLoading}
                onCopy={copyMessage}
                onEdit={editMessage}
                onRegenerate={regenerateLast}
              />
            ))}
            <div ref={messagesEndRef} />
          </div>
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
            <div className="mx-auto w-full max-w-[var(--chat-max)] rounded-xl border border-amber-300 bg-amber-50 p-3 shadow-sm">
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
                <div className="mb-2 space-y-1 text-[11px] max-h-48 overflow-auto rounded bg-white/40 border border-amber-200/60 p-2">
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

        {/* Input tray panel — same max width as the message rail */}
        <div className="p-4 md:p-8 pt-0 md:pt-0 bg-transparent flex-shrink-0 z-10">
          <div className="mx-auto w-full max-w-[var(--chat-max)] bg-white/80 dark:bg-[#1c1c1e]/80 backdrop-blur-xl border border-black/[0.08] dark:border-white/10 rounded-2xl p-3 flex flex-col gap-3 shadow-[0_4px_24px_rgb(0_0_0/0.06)] transition-colors focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/20">
            
            {/* Multi-line chat Textarea */}
            <Textarea 
              ref={inputRef}
              value={inputValue} 
              onChange={e => setInputValue(e.target.value)} 
              onKeyDown={handleKeyPress}
              placeholder="Ask the agent to investigate, summarize, or run a workflow…"
              disabled={isLoading}
              rows={2}
              className="w-full min-h-[44px] max-h-48 border-none outline-none resize-none bg-transparent text-slate-900 dark:text-slate-100 placeholder:text-slate-400 dark:placeholder:text-slate-500 focus-visible:ring-0 focus-visible:ring-offset-0 p-1"
            />
            
            {/* Input Footer row — tools wrap; actions stay grouped on the right */}
            <div className="flex flex-col gap-2 border-t border-slate-50 dark:border-white/[0.06] pt-2.5 sm:flex-row sm:items-center">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 sm:gap-2">
                <Button 
                  variant="ghost" 
                  size="sm" 
                  onClick={() => setShowToolsList(!showToolsList)}
                  className={cn(
                    "h-8 shrink-0 rounded-lg text-xs font-semibold text-muted-foreground",
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
                      className="h-8 shrink-0 rounded-lg text-xs font-semibold text-slate-500 hover:bg-slate-50 hover:text-slate-700"
                      title="Insert a correlation/trace ID lookup template"
                    >
                      <Search className="size-3.5 shrink-0" data-icon="inline-start" />
                      <span className="max-[420px]:hidden">Trace ID</span>
                    </Button>

                    {/* Time range scoping for CloudWatch lookups */}
                    <Select value={timeRange} onValueChange={setTimeRange}>
                      <SelectTrigger
                        size="sm"
                        className="h-8 w-[74px] shrink-0 rounded-lg text-xs font-semibold text-slate-600"
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
              </div>

              <div className="flex shrink-0 items-center justify-end gap-2 sm:ml-2">
                {/* Model + status cluster, like the Claude Code composer's
                    "Sonnet 5 · High ⟳" indicator. modelName can be a long
                    multi-model list ("Claude Sonnet 4.6, Claude Haiku 4.5"),
                    and this row's available width depends on the sidebar
                    panels, not the viewport — so it must truncate rather than
                    rely on a viewport breakpoint (which caused it to overlap
                    "Trace ID" whenever the chat column was narrow but the
                    browser itself was wide). */}
                {modelName && modelName !== '—' && (
                  <span
                    className="hidden sm:flex items-center gap-1.5 min-w-0 max-w-[7rem] shrink text-[10px] font-semibold text-slate-400"
                    title={modelName}
                  >
                    <span className="truncate">{modelName.split(',')[0].trim()}</span>
                    {isLoading && <Loader2 className="size-3 shrink-0 animate-spin text-primary" />}
                  </span>
                )}
                {isLoading && watchdogStale && !isStopping && (
                  // Watchdog: no SSE activity for WATCHDOG_SILENCE_MS. Most
                  // stuck runs would otherwise leave the user staring at a
                  // spinner with no signal that anything is wrong or any
                  // action to take beyond guessing to click Stop.
                  <span
                    className="hidden md:inline text-[10px] font-medium text-amber-600"
                    title="No activity from the agent in over 90s — it may be stuck. Stop and try again."
                  >
                    still running…
                  </span>
                )}
                {isLoading && (
                  <button
                    type="button"
                    onClick={handleStop}
                    disabled={isStopping}
                    title={watchdogStale ? 'Force stop (no response in 90s+)' : 'Stop agent'}
                    className={cn(
                      "flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-semibold border transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                      watchdogStale
                        ? "bg-amber-50 text-amber-700 border-amber-300 hover:bg-amber-100 animate-pulse"
                        : "bg-red-50 text-red-600 border-red-200 hover:bg-red-100 hover:border-red-300"
                    )}
                  >
                    <Square className="size-3 fill-current" />
                    {isStopping ? 'Stopping…' : watchdogStale ? 'Force stop' : 'Stop'}
                  </button>
                )}
                <SendButton
                  onClick={handleSendClick}
                  disabled={!inputValue.trim() || isLoading}
                  phase={sendPhase}
                  launching={sendLaunching}
                />
              </div>
            </div>

          </div>
        </div>

      </div>

      {/* Run trace — collapsible drawer (was an always-on right column) */}
      <Sheet open={traceDrawerOpen} onOpenChange={setTraceDrawerOpen}>
        <SheetContent side="right" className="w-[360px] p-0 gap-0 flex flex-col">
          <SheetHeader className="p-4 border-b border-black/[0.06] dark:border-white/10 h-20 justify-center">
            <SheetTitle className="text-sm flex items-center gap-2">
              <Activity className="size-4 text-primary" /> Trace · this turn
              {isLoading && (
                <Badge variant="outline" className="ml-1 px-2 py-0 text-[9px] uppercase tracking-wider text-primary bg-red-50/20 border-red-100/50 font-bold animate-pulse">
                  Live
                </Badge>
              )}
            </SheetTitle>
          </SheetHeader>
          <div className="flex-1 overflow-hidden">
            <TraceTimeline traceSteps={traceSteps} tokens={tokens} />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}

// Ensure AgentChat is mounted on window so we align with client needs
Object.assign(window, { AgentChat: Chat });

export default Chat;
