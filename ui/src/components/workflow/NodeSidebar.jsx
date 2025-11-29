import React, { useState, useEffect } from 'react';
import { getMCPServers, convertServersToNodeItems } from '../../services/mcpService';

const NodeSidebar = () => {
  const [languageModels, setLanguageModels] = useState([]);
  const [isLoadingModels, setIsLoadingModels] = useState(true);
  const [mcpServers, setMcpServers] = useState([]);
  const [isLoadingMCP, setIsLoadingMCP] = useState(true);

  // Fetch language models on component mount
  useEffect(() => {
    const loadModels = async () => {
      try {
        setIsLoadingModels(true);
        const models = [];
        
        const transformedModels = [];

        // Add GPT models to the list (Note: requires OpenAI API key and quota)
        const gptModels = [
          {
            type: 'llm',
            icon: '⚡',
            title: 'GPT Models',
            description: 'OpenAI GPT Models - Requires API quota',
            data: { 
              label: 'gpt-4o-mini', 
              model: 'gpt-4o-mini', 
              status: 'Available',
              provider: 'OpenAI',
              agent: 'openai',
              contextWindow: 128000,
              maxTokens: 16384
            }
          }
        ];
        
        // Prioritize Groq models first, then add GPT models
        setLanguageModels([...transformedModels, ...gptModels]);
      } catch (error) {
        console.error('Failed to load language models:', error);
        // Fallback models when API fails
        const fallbackModels = [
          {
            type: 'llm',
            icon: '🤖',
            title: 'Mixtral 8x7B',
            description: 'Mixtral 8x7B 32K context',
            data: { 
              label: 'Mixtral 8x7B', 
              model: 'mixtral-8x7b-32768', 
              status: 'Available',
              provider: 'Groq',
              agent: 'groq'
            }
          },
          {
            type: 'llm',
            icon: '⚡',
            title: 'Llama 3.1 70B',
            description: 'Llama 3.1 70B Versatile',
            data: { 
              label: 'Llama 3.1 70B', 
              model: 'llama-3.1-70b-versatile', 
              status: 'Available',
              provider: 'Groq',
              agent: 'groq'
            }
          },
          {
            type: 'llm',
            icon: '🚀',
            title: 'Llama 3.1 8B',
            description: 'Llama 3.1 8B Instant',
            data: { 
              label: 'Llama 3.1 8B', 
              model: 'llama-3.1-8b-instant', 
              status: 'Available',
              provider: 'Groq',
              agent: 'groq'
            }
          },
          {
            type: 'llm',
            icon: '💬',
            title: 'Gemma 2 9B',
            description: 'Gemma 2 9B IT',
            data: { 
              label: 'Gemma 2 9B', 
              model: 'gemma2-9b-it', 
              status: 'Available',
              provider: 'Groq',
              agent: 'groq'
            }
          },
          // Add GPT models as fallback too
          {
            type: 'llm',
            icon: '🧠',
            title: 'GPT-4o',
            description: 'OpenAI GPT-4o - Most capable model',
            data: { 
              label: 'GPT-4o', 
              model: 'gpt-4o', 
              status: 'Available',
              provider: 'OpenAI',
              agent: 'openai'
            }
          },
          {
            type: 'llm',
            icon: '⚡',
            title: 'GPT-4o Mini',
            description: 'OpenAI GPT-4o Mini - Fast and efficient',
            data: { 
              label: 'GPT-4o Mini', 
              model: 'gpt-4o-mini', 
              status: 'Available',
              provider: 'OpenAI',
              agent: 'openai'
            }
          },
          {
            type: 'llm',
            icon: '💬',
            title: 'GPT-3.5 Turbo',
            description: 'OpenAI GPT-3.5 Turbo - Fast and cost-effective',
            data: { 
              label: 'GPT-3.5 Turbo', 
              model: 'gpt-3.5-turbo', 
              status: 'Available',
              provider: 'OpenAI',
              agent: 'openai'
            }
          }
        ];
        setLanguageModels(fallbackModels);
      } finally {
        setIsLoadingModels(false);
      }
    };

    loadModels();
  }, []);
  // Fetch MCP servers on component mount
  useEffect(() => {
    const loadMCPServers = async () => {
      try {
        setIsLoadingMCP(true);
        const servers = await getMCPServers();
        const nodeItems = convertServersToNodeItems(servers);
        setMcpServers(nodeItems);
      } catch (error) {
        console.error('Failed to load MCP servers:', error);
        setMcpServers([]);
      } finally {
        setIsLoadingMCP(false);
      }
    };

    loadMCPServers();
  }, []);


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
          data: { label: 'AI Agent', description: 'Tools Agent' }
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
      items: isLoadingModels 
        ? [
            {
              type: 'llm',
              icon: '⏳',
              title: 'Loading...',
              description: 'Fetching models from Groq API',
              data: { label: 'Loading...', model: 'loading', status: 'Loading' }
            }
          ]
        : languageModels.length > 0 
          ? languageModels
          : [
              {
                type: 'llm',
                icon: '❌',
                title: 'No Models',
                description: 'Failed to load models',
                data: { label: 'No Models', model: 'none', status: 'Error' }
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