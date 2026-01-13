import React, { useState, useEffect, useRef } from 'react';
import {
  Box,
  Paper,
  TextField,
  IconButton,
  Typography,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  CircularProgress,
  Chip,
  Alert
} from '@mui/material';
import {
  Send as SendIcon,
  SmartToy as AgentIcon,
  Person as PersonIcon,
  PlayArrow as PlayIcon,
  Error as ErrorIcon,
  Refresh as RefreshIcon,
  Construction as ConstructionIcon
} from '@mui/icons-material';
import { isAgentWorkflowValid } from '../utils/workflowValidation.js';

// Use Vite's environment check for development mode
const DEV_MODE = import.meta.env.DEV;

// Message types
const MESSAGE_TYPES = {
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system'
};

function Chat() {
  // Show under development message if not in dev mode
  if (!DEV_MODE) {
    return (
      <Box 
        sx={{ 
          display: 'flex', 
          flexDirection: 'column', 
          alignItems: 'center', 
          justifyContent: 'center', 
          height: '100%',
          p: 4,
          textAlign: 'center'
        }}
      >
        <ConstructionIcon sx={{ fontSize: 80, color: 'warning.main', mb: 2 }} />
        <Typography variant="h4" gutterBottom fontWeight="bold">
          Agent Chat
        </Typography>
        <Typography variant="h6" color="text.secondary" gutterBottom>
          🚧 Under Development 🚧
        </Typography>
        <Paper 
          elevation={0} 
          sx={{ 
            p: 3, 
            mt: 2, 
            maxWidth: 500, 
            bgcolor: 'warning.light', 
            borderRadius: 2,
            border: '1px solid',
            borderColor: 'warning.main'
          }}
        >
          <Typography variant="body1" color="text.primary">
            This feature is currently being developed. It will allow you to interact with your agents through a chat interface.
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
            In the meantime, you can use the <strong>Workflow</strong> page to create and manage your agent workflows, and run them from the <strong>Dashboard</strong>.
          </Typography>
        </Paper>
      </Box>
    );
  }

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
      if (window.electronAPI?.loadWorkflows) {
        const workflows = await window.electronAPI.loadWorkflows();
        // Filter to only show valid agent workflows
        const agentWorkflows = workflows.filter(wf => isAgentWorkflowValid(wf));
        setAgents(agentWorkflows);
      }
    } catch (error) {
      console.error('Error loading agents:', error);
    }
  };

  const addMessage = (type, content, metadata = {}) => {
    const newMessage = {
      id: `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
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
      <Box
        key={message.id}
        sx={{
          display: 'flex',
          justifyContent: isUser ? 'flex-end' : 'flex-start',
          mb: 2
        }}
      >
        <Box
          sx={{
            display: 'flex',
            flexDirection: isUser ? 'row-reverse' : 'row',
            alignItems: 'flex-start',
            maxWidth: '100%',
            width: '100%',
            gap: 1
          }}
        >
          <Box
            sx={{
              width: 36,
              height: 36,
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              bgcolor: isUser ? 'primary.main' : isSystem ? 'grey.500' : 'secondary.main',
              color: 'white',
              flexShrink: 0
            }}
          >
            {isUser ? <PersonIcon fontSize="small" /> : <AgentIcon fontSize="small" />}
          </Box>
          
          <Paper
            elevation={1}
            sx={{
              py: 1,
              px: 2,
              bgcolor: isUser ? 'primary.light' : isSystem ? 'grey.100' : 'background.paper',
              color: isUser ? 'primary.contrastText' : 'text.primary',
              borderRadius: 2,
              borderTopLeftRadius: isUser ? 16 : 4,
              borderTopRightRadius: isUser ? 4 : 16
            }}
          >
            <Typography 
              variant="body1" 
              sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
            >
              {message.content}
            </Typography>
            
            {message.isLoading && (
              <Box sx={{ mt: 1 }}>
                {/* Show status history */}
                {message.statusHistory && message.statusHistory.length > 0 && (
                  <Box sx={{ mb: 1, maxHeight: 150, overflowY: 'auto' }}>
                    {message.statusHistory.map((status, idx) => (
                      <Typography 
                        key={idx} 
                        variant="caption" 
                        sx={{ 
                          display: 'block', 
                          color: status.type === 'error' ? 'error.main' : 'text.secondary',
                          fontSize: '0.7rem',
                          opacity: 0.8,
                          pl: 1,
                          borderLeft: '2px solid',
                          borderColor: status.type === 'tool' ? 'info.main' : 
                                      status.type === 'thinking' ? 'warning.main' :
                                      status.type === 'error' ? 'error.main' : 'grey.400',
                          mb: 0.5
                        }}
                      >
                        <span style={{ opacity: 0.6, marginRight: 4 }}>{status.time}</span>
                        {status.message}
                      </Typography>
                    ))}
                  </Box>
                )}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <CircularProgress size={16} />
                  <Typography variant="caption" color="text.secondary">
                    {message.currentStatus?.message || 'Processing...'}
                  </Typography>
                </Box>
              </Box>
            )}
            
            {message.isError && (
              <Chip icon={<ErrorIcon />} label="Error" color="error" size="small" sx={{ mt: 1 }} />
            )}
            
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              {new Date(message.timestamp).toLocaleTimeString()}
            </Typography>
          </Paper>
        </Box>
      </Box>
    );
  };

  return (
    <Box sx={{ display: 'flex', height: '100vh', bgcolor: 'grey.50' }}>
      {/* Sidebar - Agent List */}
      <Paper 
        elevation={0} 
        sx={{ 
          width: 280, 
          borderRight: 1, 
          borderColor: 'divider',
          display: 'flex',
          flexDirection: 'column'
        }}
      >
        <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Typography variant="h6" fontWeight="bold">Agents</Typography>
            <IconButton size="small" onClick={loadAgents} title="Refresh">
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Box>
          <Typography variant="body2" color="text.secondary">
            Click to trigger an agent
          </Typography>
        </Box>
        
        <List sx={{ flexGrow: 1, overflow: 'auto' }}>
          {agents.length === 0 ? (
            <Box sx={{ p: 2 }}>
              <Alert severity="info" sx={{ fontSize: '0.875rem' }}>
                No agents found. Create a workflow with type "Agent" and ensure the Agent AI node has both an LLM and at least one Tool connected.
              </Alert>
            </Box>
          ) : (
            agents.map((agent) => (
              <ListItem key={agent.id} disablePadding secondaryAction={
                <IconButton 
                  edge="end" 
                  onClick={(e) => { e.stopPropagation(); triggerAgent(agent); }}
                  disabled={isLoading}
                  title="Run agent"
                >
                  <PlayIcon color="primary" fontSize="small" />
                </IconButton>
              }>
                <ListItemButton 
                  onClick={() => handleSelectAgent(agent)}
                  disabled={isLoading}
                  selected={selectedAgent?.id === agent.id}
                >
                  <ListItemIcon>
                    <AgentIcon color={selectedAgent?.id === agent.id ? 'primary' : 'action'} />
                  </ListItemIcon>
                  <ListItemText 
                    primary={agent.name}
                    secondary={selectedAgent?.id === agent.id ? 'Selected' : 'Click to select'}
                  />
                </ListItemButton>
              </ListItem>
            ))
          )}
        </List>
      </Paper>
      
      {/* Main Chat Area */}
      <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>
        <Paper 
          elevation={0} 
          sx={{ p: 2, borderBottom: 1, borderColor: 'divider', bgcolor: 'background.paper' }}
        >
          <Typography variant="h5" fontWeight="bold">Agent Chat</Typography>
          <Typography variant="body2" color="text.secondary">
            Select an agent from the sidebar, then run it with the play button or type "run"
          </Typography>
        </Paper>
        
        <Box sx={{ flexGrow: 1, overflow: 'auto', p: 2, bgcolor: 'grey.50' }}>
          {messages.map(renderMessage)}
          <div ref={messagesEndRef} />
        </Box>
        
        <Paper elevation={2} sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <TextField
              ref={inputRef}
              fullWidth
              variant="outlined"
              placeholder="Type 'run [agent name]' to trigger an agent..."
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              disabled={isLoading}
              size="small"
              sx={{ '& .MuiOutlinedInput-root': { borderRadius: 3 } }}
            />
            <IconButton 
              color="primary" 
              onClick={handleSendMessage}
              disabled={!inputValue.trim() || isLoading}
              sx={{ 
                bgcolor: 'primary.main', 
                color: 'white',
                '&:hover': { bgcolor: 'primary.dark' },
                '&:disabled': { bgcolor: 'grey.300' }
              }}
            >
              {isLoading ? <CircularProgress size={24} color="inherit" /> : <SendIcon />}
            </IconButton>
          </Box>
        </Paper>
      </Box>
    </Box>
  );
}

export default Chat;
