import React, { useState, useEffect, useCallback } from 'react';
import { getMCPServers, convertServersToNodeItems, invalidateCache as invalidateMCPCache } from '../../services/mcpService';
import { getLLMs, convertLLMsToNodeItems, invalidateCache as invalidateLLMCache } from '../../services/llmService';
import { localTimeToCron } from '../../utils/cronUtils';

const NodeSidebar = () => {
  const [mcpServers, setMcpServers] = useState([]);
  const [isLoadingMCP, setIsLoadingMCP] = useState(true);
  const [llmModels, setLLMModels] = useState([]);
  const [isLoadingLLM, setIsLoadingLLM] = useState(true);

  // Function to load MCP servers
  const loadMCPServers = useCallback(async () => {
    try {
      setIsLoadingMCP(true);
      invalidateMCPCache(); // Force fresh data from file
      const servers = await getMCPServers();
      const nodeItems = convertServersToNodeItems(servers);
      setMcpServers(nodeItems);
    } catch (error) {
      console.error('Failed to load MCP servers:', error);
      setMcpServers([]);
    } finally {
      setIsLoadingMCP(false);
    }
  }, []);

  // Function to load LLM configurations
  const loadLLMConfigs = useCallback(async () => {
    try {
      setIsLoadingLLM(true);
      invalidateLLMCache(); // Force fresh data from file
      const llms = await getLLMs();
      const nodeItems = convertLLMsToNodeItems(llms);
      setLLMModels(nodeItems);
    } catch (error) {
      console.error('Failed to load LLM configs:', error);
      setLLMModels([]);
    } finally {
      setIsLoadingLLM(false);
    }
  }, []);

  // Fetch MCP servers on component mount
  useEffect(() => {
    loadMCPServers();
  }, [loadMCPServers]);

  // Fetch LLM configurations on component mount
  useEffect(() => {
    loadLLMConfigs();
  }, [loadLLMConfigs]);

  // Refresh configs when window gains focus (user might have changed settings)
  useEffect(() => {
    const handleFocus = () => {
      loadMCPServers();
      loadLLMConfigs();
    };

    window.addEventListener('focus', handleFocus);
    return () => window.removeEventListener('focus', handleFocus);
  }, [loadMCPServers, loadLLMConfigs]);


  const onDragStart = (event, nodeType, nodeData) => {
    event.dataTransfer.setData('application/reactflow', JSON.stringify({ type: nodeType, data: nodeData }));
    event.dataTransfer.effectAllowed = 'move';
  };

  const getNodeCategories = () => [
    {
      title: 'Agents',
      items: [
        {
          type: 'agent',
          icon: '🤖',
          title: 'AI Agent',
          description: 'Intelligent autonomous agent',
          data: {
            label: 'AI Agent',
            description: 'Tools Agent',
            agentMode: 'single'  // 'single' or 'multi'
          }
        }
      ]
    },
    {
      title: 'Scheduling',
      items: [
        {
          type: 'scheduler',
          icon: '⏰',
          title: 'Scheduler',
          description: 'Trigger workflows on schedule',
          data: {
            label: 'Scheduler',
            cronExpression: localTimeToCron('09:00', 'daily'), // Convert 9:00 AM local to UTC cron
            startTime: '09:00', // Local time for display
            recurrence: 'daily',
            enabled: true,
            status: 'Ready'
          }
        }
      ]
    },
    {
      title: 'Workflow',
      items: [
        {
          type: 'orchestrator',
          icon: '🧩',
          title: 'SQL Orchestrator',
          description: 'Run SQL files',
          data: { label: 'SQL Orchestrator', status: 'Ready' }
        }
      ]
    },
    {
      title: 'MCP Servers',
      items: isLoadingMCP
        ? [
          {
            type: 'tool',
            icon: '⏳',
            title: 'Loading...',
            description: 'Loading MCP servers from config',
            data: { label: 'Loading...', toolType: 'mcp-server', status: 'Loading' }
          }
        ]
        : mcpServers.length > 0
          ? mcpServers
          : [
            {
              type: 'tool',
              icon: '❌',
              title: 'No MCP Servers',
              description: 'Add MCP servers in Settings',
              data: { label: 'No Servers', toolType: 'mcp-server', status: 'Empty' }
            }
          ]
    },
    {
      title: 'Tools',
      items: [
        {
          type: 'cloudwatchAnalyzer',
          icon: '📊',
          title: 'CloudWatch Log Analyzer',
          description: 'Analyze multiple CloudWatch log groups',
          data: {
            label: 'Log Analyzer',
            logGroups: [],
            analysisType: 'error-patterns',
            timeRange: '1h',
            errorThreshold: 10,
            enableAlerts: false,
            status: 'Ready'
          }
        }
      ]
    },
    {
      title: 'Memory',
      items: [
        {
          type: 'memory',
          icon: '🧠',
          title: 'Vector Memory',
          description: 'Semantic vector storage',
          data: { label: 'Vector Memory', memoryType: 'vector', status: 'Ready' }
        }
      ]
    },
    {
      title: 'Language Models',
      items: isLoadingLLM
        ? [
          {
            type: 'llm',
            icon: '⏳',
            title: 'Loading...',
            description: 'Loading LLM configurations',
            data: { label: 'Loading...', model: '', status: 'Loading' }
          }
        ]
        : llmModels.length > 0
          ? llmModels
          : [
            {
              type: 'llm',
              icon: '❌',
              title: 'No LLMs',
              description: 'Add LLMs in Settings',
              data: { label: 'No LLMs', model: '', status: 'Empty' }
            }
          ]
    },
    {
      title: 'Communication',
      items: [
        {
          type: 'output',
          icon: '📺',
          title: 'Output Display',
          description: 'View agent output and results',
          data: { label: 'Output Display', type: 'output', status: 'Ready' }
        },
        {
          type: 'teams',
          icon: '💬',
          title: 'Teams',
          description: 'Microsoft Teams Channel',
          data: { label: 'Teams Channel', channel: 'General Channel', status: 'Connected' }
        }
      ]
    }
  ];

  return (
    <div className="node-sidebar">
      <div className="sidebar-title">🎯 AI Playground</div>
      <div style={{ fontSize: '12px', color: '#666', marginBottom: '20px' }}>
        Drag and drop components to build your AI workflow
      </div>

      {getNodeCategories().map((category, categoryIndex) => (
        <div key={categoryIndex} className="sidebar-section">
          <div className="sidebar-section-title">{category.title}</div>
          {category.items.map((item, itemIndex) => (
            <div
              key={itemIndex}
              className="node-item"
              draggable
              onDragStart={(event) => onDragStart(event, item.type, item.data)}
            >
              <div className="node-item-icon">{item.icon}</div>
              <div className="node-item-info">
                <div className="node-item-title">{item.title}</div>
                <div className="node-item-desc">{item.description}</div>
              </div>
            </div>
          ))}
        </div>
      ))}

      <div style={{
        marginTop: '30px',
        padding: '10px',
        background: '#e3f2fd',
        borderRadius: '6px',
        fontSize: '11px',
        color: '#1976d2'
      }}>
        💡 <strong>Tip:</strong> Connect Model (bottom left), Memory (bottom center), and Tool (bottom right) to Agents
      </div>
    </div>
  );
};

export default NodeSidebar;