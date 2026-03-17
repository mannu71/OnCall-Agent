import React, { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, Send, Bot, User, Play, RefreshCw, XCircle, Construction } from 'lucide-react';
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

function Chat() {
  // Initialize all hooks first (before any returns)
  const [messages, setMessages] = useState([
    {
      id: 1,
      type: MESSAGE_TYPES.SYSTEM,
      content: 'Welcome! Select an agent from the sidebar, then ask any question.',
      timestamp: new Date().toISOString()
    }
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [agents, setAgents] = useState([]);
  const [selectedAgent, setSelectedAgent] = useState(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

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

  // Show under development message if not in dev mode
  if (!DEV_MODE) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8 text-center">
        <Construction className="w-20 h-20 text-yellow-500 mb-4" />
        <h1 className="text-3xl font-bold mb-2">
          Agent Chat
        </h1>
        <h2 className="text-xl text-muted-foreground mb-4">
          🚧 Under Development 🚧
        </h2>
        <Card className="p-6 mt-4 max-w-lg bg-yellow-50 border-yellow-500 rounded-lg">
          <p className="text-base text-foreground mb-4">
            This feature is currently being developed. It will allow you to interact with your agents through a chat interface.
          </p>
          <p className="text-sm text-muted-foreground">
            In the meantime, you can use the <strong>Workflow</strong> page to create and manage your agent workflows, and run them from the <strong>Dashboard</strong>.
          </p>
        </Card>
      </div>
    );
  }

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

  // Select agent (without triggering)
  const handleSelectAgent = (agent) => {
    setSelectedAgent(agent);
    addMessage(MESSAGE_TYPES.SYSTEM, `Selected agent: ${agent.name}. Ask any question to get started.`);
  };

  // Trigger the selected agent
  const triggerAgent = async (agent) => {
    if (!agent) return;

    addMessage(MESSAGE_TYPES.USER, `Triggering agent: ${agent.name}`);

    const thinkingId = addMessage(MESSAGE_TYPES.ASSISTANT, `Running "${agent.name}"...`, { isLoading: true });
    setIsLoading(true);

    try {
      if (window.electronAPI?.triggerWorkflow) {
        const result = await window.electronAPI.triggerWorkflow(agent.name);

        if (result?.success) {
          updateMessage(thinkingId, {
            content: `Agent "${agent.name}" triggered successfully!`,
            isLoading: false
          });
        } else {
          updateMessage(thinkingId, {
            content: `Failed to trigger agent: ${result?.error || 'Unknown error'}`,
            isLoading: false,
            isError: true
          });
        }
      } else {
        updateMessage(thinkingId, {
          content: 'Agent execution is only available in the desktop app.',
          isLoading: false,
          isError: true
        });
      }
    } catch (error) {
      updateMessage(thinkingId, {
        content: `Error: ${error.message}`,
        isLoading: false,
        isError: true
      });
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
        addMessage(MESSAGE_TYPES.ASSISTANT, 'No agents available. Please create an agent workflow first.');
        return;
      }
      // Auto-select first agent if none selected
      const firstAgent = agents[0];
      setSelectedAgent(firstAgent);
      addMessage(MESSAGE_TYPES.SYSTEM, `Auto-selected agent: ${firstAgent.name}`);
      await askAgent(firstAgent, userMessage);
      return;
    }

    // Send the question to the selected agent
    await askAgent(selectedAgent, userMessage);
  };

  // Ask the agent a question
  const askAgent = async (agent, question) => {
    const thinkingId = addMessage(MESSAGE_TYPES.ASSISTANT, `Starting agent...`, { isLoading: true, statusHistory: [] });
    setIsLoading(true);

    // Subscribe to progress events
    let unsubscribe = null;
    if (window.electronAPI?.onAgentProgress) {
      unsubscribe = window.electronAPI.onAgentProgress((progress) => {
        // Update the message with progress
        setMessages(prev => prev.map(msg => {
          if (msg.id === thinkingId) {
            const statusHistory = [...(msg.statusHistory || [])];
            // Add new status to history (keep last 5)
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
        } else {
          updateMessage(thinkingId, {
            content: `Error: ${result?.error || 'Unknown error'}`,
            isLoading: false,
            isError: true,
            currentStatus: null
          });
        }
      } else {
        updateMessage(thinkingId, {
          content: 'Agent execution is only available in the desktop app.',
          isLoading: false,
          isError: true
        });
      }
    } catch (error) {
      updateMessage(thinkingId, {
        content: `Error: ${error.message}`,
        isLoading: false,
        isError: true
      });
    } finally {
      setIsLoading(false);
      // Unsubscribe from progress events
      if (unsubscribe) unsubscribe();
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const renderMessage = (message) => {
    const isUser = message.type === MESSAGE_TYPES.USER;
    const isSystem = message.type === MESSAGE_TYPES.SYSTEM;

    return (
      <div
        key={message.id}
        className={`flex mb-4 ${isUser ? 'justify-end' : 'justify-start'}`}
      >
        <div
          className={`flex ${isUser ? 'flex-row-reverse' : 'flex-row'} items-start max-w-full w-full gap-2`}
        >
          <div
            className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${
              isUser 
                ? 'bg-primary text-primary-foreground' 
                : isSystem 
                ? 'bg-gray-500 text-white' 
                : 'bg-secondary text-secondary-foreground'
            }`}
          >
            {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
          </div>

          <Card
            className={`${
              isUser 
                ? 'bg-primary text-primary-foreground rounded-tl-2xl rounded-tr-sm' 
                : isSystem 
                ? 'bg-gray-100 text-gray-900 rounded-tl-sm rounded-tr-2xl' 
                : 'bg-background rounded-tl-sm rounded-tr-2xl'
            }`}
          >
            <CardContent className="py-2 px-3">
              <p className="whitespace-pre-wrap break-words text-sm">
                {message.content}
              </p>

              {message.isLoading && (
                <div className="mt-2">
                  {/* Show status history */}
                  {message.statusHistory && message.statusHistory.length > 0 && (
                    <div className="mb-2 max-h-36 overflow-y-auto">
                      {message.statusHistory.map((status, idx) => (
                        <div
                          key={idx}
                          className={`text-xs opacity-80 pl-2 border-l-2 mb-1 ${
                            status.type === 'tool' 
                              ? 'border-blue-500 text-blue-600' 
                              : status.type === 'thinking' 
                              ? 'border-yellow-500 text-yellow-600' 
                              : status.type === 'error' 
                              ? 'border-red-500 text-red-600' 
                              : 'border-gray-400 text-gray-600'
                          }`}
                        >
                          <span className="opacity-60 mr-1">{status.time}</span>
                          {status.message}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span className="text-xs text-muted-foreground">
                      {message.currentStatus?.message || 'Processing...'}
                    </span>
                  </div>
                </div>
              )}

              {message.isError && (
                <Badge variant="destructive" className="mt-2">
                  <XCircle className="w-3 h-3 mr-1" />
                  Error
                </Badge>
              )}

              <p className="text-xs text-muted-foreground mt-2">
                {new Date(message.timestamp).toLocaleTimeString()}
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  };

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar - Agent List */}
      <Card className="w-[280px] border-r rounded-none flex flex-col">
        <div className="p-4 h-20 border-b flex flex-col justify-center">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-base">Agents</h2>
            <Button size="icon" variant="ghost" onClick={loadAgents} title="Refresh">
              <RefreshCw className="w-4 h-4" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Click to trigger an agent
          </p>
        </div>

        <div className="flex-grow overflow-auto">
          {agents.length === 0 ? (
            <div className="p-4">
              <Alert>
                <AlertDescription className="text-sm">
                  No agents found. Create a workflow with type "Agent" and ensure the Agent AI node has both an LLM and at least one Tool connected.
                </AlertDescription>
              </Alert>
            </div>
          ) : (
            agents.map((agent) => (
              <div key={agent.id} className="relative">
                <Button
                  variant="ghost"
                  className={`w-full justify-start px-4 py-6 rounded-none border-b ${
                    selectedAgent?.id === agent.id ? 'bg-accent' : ''
                  }`}
                  onClick={() => handleSelectAgent(agent)}
                  disabled={isLoading}
                >
                  <Bot className={`w-5 h-5 mr-3 ${selectedAgent?.id === agent.id ? 'text-primary' : 'text-muted-foreground'}`} />
                  <div className="flex-1 text-left">
                    <div className="font-medium">{agent.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {selectedAgent?.id === agent.id ? 'Selected' : 'Click to select'}
                    </div>
                  </div>
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="absolute right-2 top-1/2 -translate-y-1/2"
                  onClick={(e) => { e.stopPropagation(); triggerAgent(agent); }}
                  disabled={isLoading}
                  title="Run agent"
                >
                  <Play className="w-4 h-4 text-primary" />
                </Button>
              </div>
            ))
          )}
        </div>
      </Card>

      {/* Main Chat Area */}
      <div className="flex-grow flex flex-col">
        <Card className="h-20 px-6 border-b rounded-none flex flex-col justify-center">
          <h1 className="font-bold text-xl">Agent Chat</h1>
          <p className="text-sm text-muted-foreground">
            Interact with your agent workflows in real-time
          </p>
        </Card>

        <div className="flex-grow overflow-auto p-4 bg-gray-50">
          {messages.map(renderMessage)}
          <div ref={messagesEndRef} />
        </div>

        <Card className="p-4 border-t rounded-none shadow-lg">
          <div className="flex gap-2">
            <Input
              ref={inputRef}
              placeholder="Enter message"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              disabled={isLoading}
              className="flex-1 rounded-full"
            />
            <Button
              size="icon"
              onClick={handleSendMessage}
              disabled={!inputValue.trim() || isLoading}
              className="rounded-full"
            >
              {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

export default Chat;
