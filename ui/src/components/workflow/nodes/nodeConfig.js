import { Position } from 'reactflow';

// ----------------------------------------------------
// BASE CONFIGURATIONS (reusable patterns)
// ----------------------------------------------------

const baseHandles = {
  // Source handles (outputs)
  sourceRight: { type: 'source', position: Position.Right, id: 'output', style: { background: '#666' } },
  sourceLeft: (id = 'output', color = '#4caf50') => ({
    type: 'source', position: Position.Left, id, style: { background: color }
  }),
  sourceTop: (id = 'output', color = '#ff9800') => ({
    type: 'source', position: Position.Top, id, style: { background: color }
  }),

  // Target handles (inputs)
  targetLeft: (id = 'input', color = '#666') => ({
    type: 'target', position: Position.Left, id, style: { background: color }
  }),
  // Agent bottom handles with positioning
  targetBottomLeft: (id, color, label) => ({
    type: 'target', position: Position.Bottom, id, style: { background: color, left: '25%' }, label
  }),
  targetBottomCenter: (id, color, label) => ({
    type: 'target', position: Position.Bottom, id, style: { background: color, left: '50%' }, label
  }),
  targetBottomRight: (id, color, label) => ({
    type: 'target', position: Position.Bottom, id, style: { background: color, left: '75%' }, label
  })
};

// Common icon maps
const iconMaps = {
  tool: { search: '🔍', analysis: '📊', text: '📝', image: '📷', api: '🔧', file: '📋' },
  memory: { vector: '🧠', episodic: '📚', working: '💾', knowledge: '🗂️' }
};

// ----------------------------------------------------
// NODE CONFIGURATIONS
// ----------------------------------------------------

export const nodeConfigurations = {
  // Core Agent
  agent: {
    icon: '🤖',
    title: 'AI Agent',
    className: 'agent-node',
    defaultLabel: 'AI Agent',
    handles: [
      baseHandles.targetLeft('agent-input'),
      baseHandles.targetBottomLeft('model', '#2196f3', 'Chat Model'),
      baseHandles.targetBottomCenter('memory', '#ff9800', 'Memory'),
      baseHandles.targetBottomRight('tool', '#4caf50', 'Tool'),
      baseHandles.sourceRight
    ],
    getStatus: (data) => {
      const mode = data?.agentMode === 'multi' ? 'Multi-Agent' : 'Single Agent';
      if (data?.initializing) return { text: `Initializing (${mode})...`, class: 'initializing' };
      if (data?.initialized) return { text: `${mode} ready`, class: 'ready', model: data?.model };
      if (data?.processing) return { text: `Processing (${mode})...`, class: 'processing' };
      return { text: `Connect LLM (${mode})`, class: 'not-initialized' };
    },
    getExtra: (data) => {
      if (data?.agentMode === 'multi') {
        return { text: '🧩 Orchestrated', class: 'mode' };
      }
      return null;
    }
  },

  // LLM (AWS Bedrock)
  llm: {
    icon: '🧠',
    title: 'LLM',
    className: 'llm-node',
    defaultLabel: 'Language Model',
    handles: [baseHandles.sourceLeft('llm-output', '#4285f4')],
    getStatus: (data) => {
      if (data?.processing) return { text: `Processing (${data?.model || 'Bedrock'})...`, class: 'processing' };
      return { text: data?.status || 'Ready', class: 'ready' };
    },
    getExtra: (data) => {
      if (data?.provider === 'AWS Bedrock') {
        return { text: '☁️ AWS Bedrock', class: 'provider' };
      }
      return null;
    }
  },

  // Memory
  memory: {
    icon: '🧠',
    title: 'Memory',
    className: 'memory-node',
    defaultLabel: 'Memory',
    handles: [baseHandles.sourceTop()],
    getIcon: (data) => iconMaps.memory[data?.memoryType] || '🧠'
  },

  // Tool
  tool: {
    icon: '🛠️',
    title: 'Tool',
    className: 'tool-node',
    defaultLabel: 'Tool',
    handles: [baseHandles.sourceLeft()],
    getIcon: (data) => iconMaps.tool[data?.toolType] || '🛠️'
  },

  // Database
  database: {
    icon: '🗄️',
    title: 'Database',
    className: 'database-node',
    defaultLabel: 'Database',
    handles: [baseHandles.sourceLeft('database-connection', '#34a853')]
  },

  // Chat
  chat: {
    icon: '💬',
    title: 'Chat',
    className: 'chat-node',
    defaultLabel: 'Chat Interface',
    handles: [baseHandles.targetLeft(), baseHandles.sourceRight]
  },

  // Teams
  teams: {
    icon: '💬',
    title: 'Teams',
    className: 'teams-node',
    defaultLabel: 'Teams Channel',
    handles: [
      baseHandles.targetLeft('teams-input', '#6264a7'),
      { type: 'source', position: Position.Right, id: 'teams-output', style: { background: '#6264a7' } }
    ],
    getStatus: (data) => ({
      text: data?.processing ? 'Processing...' : (data?.status || 'Connected'),
      class: data?.processing ? 'processing' : 'ready'
    })
  },

  // Output
  output: {
    icon: '📤',
    title: 'Output',
    className: 'output-node',
    defaultLabel: 'Output',
    handles: [baseHandles.targetLeft()],
    isCustom: true
  },

  // Scheduler
  scheduler: {
    icon: '⏰',
    title: 'Scheduler',
    className: 'scheduler-node',
    defaultLabel: 'Scheduler',
    handles: [{ type: 'source', position: Position.Right, id: 'scheduler-output', style: { background: '#9c27b0' } }],
    getIcon: (data) => data?.enabled !== false ? '⏰' : '⏸️',
    getStatus: (data) => {
      const cron = data?.cronExpression || '0 9 * * *';
      const parts = cron.split(' ');
      const recurrence = data?.recurrence || 'daily';

      let text = 'Ready';
      if (parts.length >= 2 && parts[1] !== '*') {
        const time = `${parts[1]}:${parts[0].padStart(2, '0')}`;
        text = `${recurrence.charAt(0).toUpperCase() + recurrence.slice(1)} at ${time}`;
      }
      return { text, class: 'ready', icon: '✓' };
    },
    getExtra: (data) => data?.cronExpression ? { text: data.cronExpression, class: 'cron' } : null
  },

  // CloudWatch Analyzer
  cloudwatchAnalyzer: {
    icon: '📊',
    title: 'CloudWatch Log Analyzer',
    className: 'cloudwatch-analyzer-node',
    defaultLabel: 'Log Analyzer',
    handles: [baseHandles.sourceLeft()],
    getIcon: (data) => {
      if (data?.processing) return '📊';
      if (data?.error) return '❌';
      if (data?.lastAnalysis) return '✅';
      return '📊';
    },
    getStatus: (data) => {
      if (data?.processing) return { text: 'Analyzing logs...', class: 'processing' };
      if (data?.error) return { text: `Error: ${data.error.slice(0, 30)}...`, class: 'error' };
      const count = data?.logGroups?.length || 0;
      return { text: `${count} log group${count !== 1 ? 's' : ''} configured`, class: 'ready' };
    },
    getExtra: (data) => data?.lastAnalysis ? {
      text: `Last: ${new Date(data.lastAnalysis).toLocaleTimeString()}`,
      class: 'timestamp'
    } : null
  },

  codeAnalyzer: {
    icon: '🔍',
    title: 'Code Analyzer',
    className: 'code-analyzer-node',
    defaultLabel: 'Code Analyzer',
    handles: [{ type: 'source', position: 'left', id: 'source-left' }],
    getIcon: (data) => {
      if (data?.processing) return '🔄';
      if (data?.error) return '❌';
      if (data?.lastIndexed) return '✅';
      return '🔍';
    },
    getStatus: (data) => {
      if (data?.processing) return { text: 'Indexing...', class: 'processing' };
      if (data?.error) return { text: `Error: ${data.error.slice(0, 30)}...`, class: 'error' };
      const c = data?.repos?.length || 0;
      const text = `${c} repo${c !== 1 ? 's' : ''} configured`;
      return { text, class: 'ready' };
    },
    getExtra: (data) => data?.lastIndexed ? {
      text: `Last indexed: ${new Date(data.lastIndexed).toLocaleTimeString()}`,
      class: 'timestamp'
    } : null
  }
};

// ----------------------------------------------------
// HELPER FUNCTIONS
// ----------------------------------------------------

export const getNodeConfig = (type) => nodeConfigurations[type] || null;

export const getNodeHandles = (type) => nodeConfigurations[type]?.handles || [];

export const getNodeIcon = (type, data) => {
  const config = nodeConfigurations[type];
  if (!config) return '❓';
  if (config.getIcon) return config.getIcon(data);
  return config.icon;
};

export const getNodeStatus = (type, data) => {
  const config = nodeConfigurations[type];
  if (!config?.getStatus) return null;
  return config.getStatus(data);
};
