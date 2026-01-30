import { Position } from 'reactflow';

export const nodeConfigurations = {
  agent: {
    icon: '🤖',
    title: 'AI Agent',
    className: 'agent-node',
    defaultLabel: 'AI Agent',
    defaultDescription: 'Tools Agent',
    handles: [
      { type: 'target', position: Position.Left, id: 'agent-input', style: { background: '#666' } },
      { type: 'target', position: Position.Bottom, id: 'model', style: { background: '#2196f3', left: '25%' }, label: 'Chat Model' },
      { type: 'target', position: Position.Bottom, id: 'memory', style: { background: '#ff9800', left: '50%' }, label: 'Memory' },
      { type: 'target', position: Position.Bottom, id: 'tool', style: { background: '#4caf50', left: '75%' }, label: 'Tool' },
      { type: 'source', position: Position.Right, id: 'agent-output', style: { background: '#666' } }
    ],
    hasProcessing: true,
    hasInitialization: true,
    getIcon: (data) => {
      if (data?.processing) return { icon: '🤖', spinner: true };
      if (data?.initializing) return { icon: '⚙️', spinner: true };
      if (data?.initialized) return { icon: '✅' };
      return { icon: '🤖' };
    },
    renderStatus: (data) => {
      if (data?.initializing) {
        return {
          text: 'Initializing agent...',
          className: 'initializing-status',
          modelInfo: data?.model
        };
      }
      if (data?.initialized) {
        return {
          text: 'Agent ready',
          className: 'initialized-status',
          modelInfo: data?.model
        };
      }
      return {
        text: 'Connect LLM to initialize',
        className: 'not-initialized-status'
      };
    },
    renderProcessing: (data) => {
      if (data?.processing && data?.lastMessage) {
        return {
          text: 'Processing message...',
          message: data.lastMessage.length > 30 ? data.lastMessage.substring(0, 30) + '...' : data.lastMessage
        };
      }
      return null;
    }
  },

  chat: {
    icon: '💬',
    title: 'Chat',
    className: 'chat-node',
    defaultLabel: 'Chat Interface',
    defaultDescription: 'Interactive Chat',
    handles: [
      { type: 'target', position: Position.Left, id: 'chat-input', style: { background: '#666' } },
      { type: 'source', position: Position.Right, id: 'chat-output', style: { background: '#666' } }
    ],
    hasProcessing: true,
    getIcon: (data) => {
      if (data?.processing) return { icon: '💬', spinner: true };
      return { icon: '💬' };
    }
  },

  llm: {
    icon: '🧠',
    title: 'LLM',
    className: 'llm-node',
    defaultLabel: 'Language Model',
    defaultDescription: 'GPT-4',
    handles: [
      { type: 'source', position: Position.Right, id: 'llm-output', style: { background: '#4285f4' } }
    ],
    hasProcessing: true,
    getIcon: (data) => {
      if (data?.processing) return { icon: '🧠', spinner: true };
      return { icon: '🧠' };
    },
    renderStatus: (data) => {
      if (data?.processing) {
        return {
          text: `Processing (${data?.model || 'LLM'})...`,
          className: 'llm-processing-status',
          icon: '⚡'
        };
      }
      return { text: data?.status || 'Ready', className: 'node-status' };
    },
    renderExtra: (data) => {
      if (data?.agent) {
        return {
          text: data.agent === 'openai' ? 'OpenAI Agent' : 'Groq Agent',
          className: 'node-provider'
        };
      }
      return null;
    }
  },

  tool: {
    icon: '🛠️',
    title: 'Tool',
    className: 'tool-node',
    defaultLabel: 'Tool',
    defaultDescription: 'Generic tool',
    handles: [
      { type: 'source', position: Position.Left, id: 'tool-connection', style: { background: '#4caf50' } }
    ],
    getIcon: (data) => {
      const iconMap = {
        search: '🔍',
        analysis: '📊',
        text: '📝',
        image: '📷',
        api: '🔧',
        file: '📋'
      };
      return { icon: iconMap[data?.toolType] || '🛠️' };
    }
  },

  memory: {
    icon: '🧠',
    title: 'Memory',
    className: 'memory-node',
    defaultLabel: 'Memory',
    defaultDescription: 'Generic memory',
    handles: [
      { type: 'source', position: Position.Top, id: 'memory-output', style: { background: '#ff9800' } }
    ],
    getIcon: (data) => {
      const iconMap = {
        vector: '🧠',
        episodic: '📚',
        working: '💾',
        knowledge: '🗂️'
      };
      return { icon: iconMap[data?.memoryType] || '🧠' };
    }
  },

  database: {
    icon: '🗄️',
    title: 'Database',
    className: 'database-node',
    defaultLabel: 'Database',
    defaultDescription: 'PostgreSQL',
    handles: [
      { type: 'source', position: Position.Left, id: 'database-connection', style: { background: '#34a853' } }
    ]
  },

  teams: {
    icon: '💬',
    title: 'Teams',
    className: 'teams-node',
    defaultLabel: 'Teams Channel',
    defaultDescription: 'General Channel',
    handles: [
      { type: 'target', position: Position.Left, id: 'teams-input', style: { background: '#6264a7' } },
      { type: 'source', position: Position.Right, id: 'teams-output', style: { background: '#6264a7' } }
    ],
    hasProcessing: true,
    getIcon: (data) => {
      if (data?.processing) return { icon: '💬', spinner: true };
      return { icon: '💬' };
    },
    renderStatus: (data) => {
      return {
        text: data?.processing ? 'Processing...' : (data?.status || 'Connected'),
        className: 'node-status'
      };
    }
  },

  output: {
    icon: '📤',
    title: 'Output',
    className: 'output-node',
    defaultLabel: 'Output',
    defaultDescription: 'Display output',
    handles: [
      { type: 'target', position: Position.Left, id: 'input', style: { background: '#666' } }
    ],
    isCustom: true // Special handling needed for OutputNode
  },

  scheduler: {
    icon: '⏰',
    title: 'Scheduler',
    className: 'scheduler-node',
    defaultLabel: 'Scheduler',
    defaultDescription: '',
    handles: [
      { type: 'source', position: Position.Right, id: 'scheduler-output', style: { background: '#9c27b0' } }
    ],
    hasProcessing: false,
    getIcon: (data) => {
      if (data?.enabled !== false) return { icon: '⏰' };
      return { icon: '⏸️' };
    },
    renderStatus: (data) => {
      const cronExpr = data?.cronExpression || '0 9 * * *';
      const parts = cronExpr.split(' ');
      let statusText = 'Ready';
      
      // Parse cron to display friendly time
      if (parts.length >= 2) {
        const minute = parts[0].padStart(2, '0');
        const hour = parts[1];
        
        // Check recurrence type
        const recurrence = data?.recurrence || 'daily';
        if (recurrence === 'daily') {
          statusText = `Daily at ${hour}:${minute}`;
        } else if (recurrence === 'weekly') {
          statusText = `Weekly at ${hour}:${minute}`;
        } else if (recurrence === 'monthly') {
          statusText = `Monthly at ${hour}:${minute}`;
        } else if (hour !== '*') {
          statusText = `At ${hour}:${minute}`;
        }
      }
      
      return {
        text: statusText,
        className: 'scheduler-enabled-status',
        icon: '✓'
      };
    },
    renderExtra: (data) => {
      if (data?.cronExpression) {
        return {
          text: data.cronExpression,
          className: 'node-cron'
        };
      }
      return null;
    }
  }
};
