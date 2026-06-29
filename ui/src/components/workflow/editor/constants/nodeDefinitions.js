/**
 * Workflow editor static node definitions (extracted from LangflowEditor.jsx).
 *
 * Pure data — category tints, port types, the node-type catalogue (palette +
 * slot schemas), and per-type default params applied when a node is dropped.
 * Icons are referenced by string key (see `./icons`).
 */

// ── Category tints ──────────────────────────────────────────────────────────
export const CAT_TINT = {
  Inputs:  { bg: '#fffbeb', fg: '#78350f', border: '#fde68a', accent: '#d97706' },
  Models:  { bg: '#fef2f2', fg: '#7f1d1d', border: '#fecaca', accent: '#dc2626' },
  Memory:  { bg: '#fffbeb', fg: '#92400e', border: '#fde68a', accent: '#d97706' },
  Tools:   { bg: '#eff6ff', fg: '#1e3a8a', border: '#bfdbfe', accent: '#2563eb' },
  Agents:  { bg: '#f5f3ff', fg: '#5b21b6', border: '#ddd6fe', accent: '#7c3aed' },
  Logic:   { bg: '#f1f5f9', fg: '#334155', border: '#cbd5e1', accent: '#475569' },
  Outputs: { bg: '#ecfdf5', fg: '#065f46', border: '#a7f3d0', accent: '#059669' },
};

// ── Port types ──────────────────────────────────────────────────────────────
export const PORT_TYPE = {
  message: { color: '#475569', label: 'Message'        },
  model:   { color: '#dc2626', label: 'Language Model' },
  memory:  { color: '#d97706', label: 'Memory'         },
  tool:    { color: '#2563eb', label: 'Tool'           },
  data:    { color: '#059669', label: 'Data'           },
  trigger: { color: '#a16207', label: 'Trigger'        },
  boolean: { color: '#7c3aed', label: 'Boolean'        },
};

// ── Node catalogue ──────────────────────────────────────────────────────────
export const NODE_TYPES = {
  // ── Inputs ───────────────────────────────────────────────────
  schedule: {
    category: 'Inputs', label: 'Schedule', icon: 'clock',
    desc: 'Run on a cron schedule',
    slots: [
      { kind: 'select',      id: 'frequency', label: 'Frequency',
        options: ['Every 5 min','Every 15 min','Every 30 min','Hourly','Daily','Weekly','Monthly'] },
      { kind: 'field',       id: 'time',      label: 'Time (HH:MM)', mono: true },
      { kind: 'weekday-select', id: 'days',   label: 'Days' },
      // Timezone is global (Settings → Timezone), shared by every schedule —
      // shown read-only here, not editable per node.
      { kind: 'tz-info',     id: 'tz',        label: 'Timezone' },
      { kind: 'port-out', id: 'trigger', label: 'Trigger', portType: 'trigger' },
    ],
  },
  // ── Models ───────────────────────────────────────────────────
  // Legacy types kept for existing saved workflows — hidden from palette
  anthropic_model: {
    category: 'Models', label: 'Anthropic', icon: 'sparkle', paletteHidden: true,
    desc: 'Claude chat completion',
    slots: [
      { kind: 'select',   id: 'model',  label: 'Model name' },
      { kind: 'field',    id: 'temp',   label: 'Temperature', suffix: '0–1' },
      { kind: 'textarea', id: 'system', label: 'System message' },
      { kind: 'port-out', id: 'lm',     label: 'Language Model', portType: 'model' },
    ],
  },
  openai_model: {
    category: 'Models', label: 'OpenAI', icon: 'sparkle', paletteHidden: true,
    desc: 'GPT chat completion',
    slots: [
      { kind: 'select',   id: 'model', label: 'Model name' },
      { kind: 'field',    id: 'temp',  label: 'Temperature', suffix: '0–2' },
      { kind: 'port-out', id: 'lm',   label: 'Language Model', portType: 'model' },
    ],
  },
  // New unified LLM node — dropdown populated from Settings
  language_model: {
    category: 'Models', label: 'Language Model', icon: 'sparkle',
    desc: 'Pick one or more models · wire each output',
    slots: [
      { kind: 'llm-select', id: 'llm',    label: 'Models' },  // multi — one output port per model
      { kind: 'field',      id: 'temp',   label: 'Temperature', suffix: '0–1' },
      { kind: 'textarea',   id: 'system', label: 'System message' },
      { kind: 'port-out',   id: 'lm',     label: 'Language Model', portType: 'model' },
    ],
  },

  // ── Memory ───────────────────────────────────────────────────
  vector_memory: {
    category: 'Memory', label: 'Vector Memory', icon: 'db',
    desc: 'Semantic vector recall',
    slots: [
      { kind: 'field',    id: 'collection', label: 'Collection', mono: true },
      { kind: 'field',    id: 'topK',       label: 'Top K', suffix: 'matches' },
      { kind: 'port-out', id: 'mem',        label: 'Memory', portType: 'memory' },
    ],
  },

  // ── Tools ────────────────────────────────────────────────────
  cloudwatch_tool: {
    category: 'Tools', label: 'CloudWatch', icon: 'cloud',
    desc: 'Scan log groups + run analyzers',
    width: 268,
    slots: [
      { kind: 'select', id: 'region',   label: 'AWS region',
        options: ['us-east-1','us-east-2','us-west-1','us-west-2','eu-west-1','eu-west-2',
                  'eu-central-1','ap-south-1','ap-southeast-1','ap-southeast-2','ap-northeast-1'] },
      { kind: 'field',  id: 'profile',  label: 'AWS profile', mono: true },
      { kind: 'chips',  id: 'groups',   label: 'Log groups', action: 'discoverCloudWatchLogGroups' },
      { kind: 'select', id: 'analysis', label: 'Analysis type',
        options: ['error-patterns','metrics','alarms','anomalies'] },
      { kind: 'select', id: 'analysis_depth', label: 'Analysis depth',
        options: ['auto','shallow','deep'] },
      { kind: 'select', id: 'tool_mode', label: 'Tool mode',
        options: ['auto','agent','prescan'] },
      { kind: 'toggle', id: 'activeAlarmsOnly', label: '🔴 Active alarms only' },
      { kind: 'select', id: 'range',    label: 'Time range',
        options: ['15m','1h','6h','24h'] },
      { kind: 'field',  id: 'threshold', label: 'Error threshold', suffix: 'errors' },
      { kind: 'toggle', id: 'alerts',   label: 'Enable alerts' },
      { kind: 'port-out', id: 'tool',   label: 'Tool', portType: 'tool' },
    ],
  },
  postgres_tool: {
    category: 'Tools', label: 'Postgres', icon: 'server', paletteHidden: true,
    desc: 'SQL query tool',
    slots: [
      { kind: 'field',    id: 'connection', label: 'Connection', mono: true },
      { kind: 'port-out', id: 'tool',       label: 'Tool', portType: 'tool' },
    ],
  },
  database: {
    category: 'Tools', label: 'Database', icon: 'db',
    desc: 'Database MCP server from Settings',
    paletteHidden: true,  // superseded by mcp_server node
    slots: [
      { kind: 'db-select', id: 'server', label: 'Server' },
      { kind: 'port-out',  id: 'tool',   label: 'Tool', portType: 'tool' },
    ],
  },
  mcp_server: {
    category: 'Tools', label: 'MCP', icon: 'server',
    desc: 'Connect MCP servers from Settings · wire each independently',
    slots: [
      { kind: 'mcp-select', id: 'servers', label: 'MCP Servers' },
      { kind: 'field',      id: 'tools',   label: 'Tool filter',
        placeholder: 'e.g. wit_*, search_code (blank = all)' },
      { kind: 'port-out',   id: 'tool',    label: 'Tool', portType: 'tool' },
    ],
  },
  code_search_tool: {
    category: 'Tools', label: 'Code Search', icon: 'search',
    desc: 'Search repositories via Code Crawler or codegraph',
    slots: [
      { kind: 'segment', id: 'backend', label: 'Backend', options: [
        { label: 'Code Crawler', value: 'code_crawler' },
        { label: 'codegraph',    value: 'codegraph' },
      ] },
      { kind: 'repo-select', id: 'repos', label: 'Repositories' },
      { kind: 'port-in',     id: 'lm',    label: 'Crawler model', portType: 'model', optional: true },
      { kind: 'port-out',    id: 'tool',  label: 'Tool', portType: 'tool' },
    ],
  },

  // ── Logic ────────────────────────────────────────────────────
  orchestrator: {
    category: 'Logic', label: 'SQL Orchestrator', icon: 'server',
    desc: 'Run a SQL file via connected database tool',
    slots: [
      { kind: 'port-in',  id: 'trigger', label: 'Trigger',  portType: 'trigger' },
      { kind: 'port-in',  id: 'tool',    label: 'Databases', portType: 'tool', multi: true },
      { kind: 'file-select', id: 'sqlFile', label: 'SQL file' },
      { kind: 'port-out', id: 'result',  label: 'Result',    portType: 'data' },
    ],
  },
  router: {
    category: 'Logic', label: 'Semantic Router', icon: 'funnel',
    desc: 'Classify user query using LLM and route to selected agent node',
    slots: [
      { kind: 'port-in',  id: 'trigger', label: 'Trigger', portType: 'trigger', optional: true },
      { kind: 'port-in',  id: 'model', label: 'Language Model', portType: 'model' },
      { kind: 'routes-editor', id: 'routes', label: 'Category Routes' },
      { kind: 'port-out', id: 'route-output', label: 'Route Output', portType: 'message' },
    ],
  },

  // ── Agents ───────────────────────────────────────────────────
  agent: {
    category: 'Agents', label: 'Agent', icon: 'workflow',
    desc: 'ReAct loop over tools + memory',
    width: 268,
    slots: [
      { kind: 'port-in',  id: 'trigger',  label: 'Trigger',        portType: 'trigger', optional: true },
      { kind: 'port-in',  id: 'lm',       label: 'Language Model', portType: 'model'   },
      { kind: 'port-in',  id: 'subagent', label: 'Subagent model', portType: 'model', optional: true },
      { kind: 'port-in',  id: 'memory',   label: 'Memory',         portType: 'memory', optional: true },
      { kind: 'port-in',  id: 'tools',    label: 'Tools',          portType: 'tool',   multi: true },
      { kind: 'port-in',  id: 'input',    label: 'Input',          portType: 'message', optional: true },
      { kind: 'textarea', id: 'system',   label: 'Agent instructions' },
      { kind: 'field',    id: 'maxIter',  label: 'Max iterations', suffix: 'steps' },
      { kind: 'toggle',   id: 'autoLearn',   label: 'Auto-learn' },
      { kind: 'toggle',   id: 'sandbox',     label: 'Sandbox shell' },
      { kind: 'toggle',   id: 'supervisor_enabled', label: 'Supervisor (human review)' },
      { kind: 'port-out', id: 'response', label: 'Response', portType: 'message' },
    ],
  },

  // ── Logic ────────────────────────────────────────────────────
  if: {
    category: 'Logic', label: 'IF Else', icon: 'funnel',
    desc: 'Branch on a boolean expression',
    slots: [
      { kind: 'port-in',  id: 'input',     label: 'Input',    portType: 'message' },
      { kind: 'textarea', id: 'condition', label: 'Condition' },
      { kind: 'port-out', id: 'true',      label: 'If true',  portType: 'message' },
      { kind: 'port-out', id: 'false',     label: 'If false', portType: 'message' },
    ],
  },

  // ── Outputs ──────────────────────────────────────────────────
  wiki: {
    category: 'Outputs', label: 'Wiki', icon: 'wiki',
    desc: 'Publish agent output to a Wiki page',
    slots: [
      { kind: 'port-in', id: 'msg',      label: 'Message',  portType: 'message' },
      { kind: 'select',  id: 'format',   label: 'Format',
        options: ['Summary', 'Table', 'Raw Markdown', 'Bullet Points'] },
      { kind: 'select',  id: 'platform', label: 'Platform',
        options: ['Azure DevOps Wiki', 'Confluence', 'GitHub Wiki'] },
      { kind: 'field',   id: 'wikiUrl',  label: 'Wiki URL',   mono: true },
      { kind: 'field',   id: 'pagePath', label: 'Page path',  mono: true },
      { kind: 'field',   id: 'project',  label: 'Project',    mono: true },
      { kind: 'field',   id: 'pat',      label: 'PAT Token (optional)', mono: true },
      { kind: 'field',   id: 'tokenVar', label: 'Token env var', mono: true },
    ],
  },
};

// ── Default params applied when a node is dropped from the palette ───────────
export const NODE_DEFAULTS = {
  schedule:    { frequency: 'Every 15 min', time: '09:00', days: 'Mon,Tue,Wed,Thu,Fri', tz: 'UTC' },
  vector_memory: { collection: '', topK: '5' },
  cloudwatch_tool: { region: 'us-east-1', profile: '', groups: '', analysis: 'error-patterns', analysis_depth: 'auto', tool_mode: 'auto', range: '15m', threshold: '10', alerts: 'false' },
  agent:       { system: '', maxIter: '10', harness: '', autoLearn: 'false', sandbox: 'false', supervisor_enabled: 'true' },
  if:          { condition: '' },
  wiki:        { format: 'Summary', platform: 'Azure DevOps Wiki', wikiUrl: '', pagePath: '', project: '', pat: '', tokenVar: 'ADO_WIKI_PAT' },
  orchestrator: { sqlFile: '' },
  router:      { routes: {}, routes_description: {} },
  code_search_tool: { repos: '', backend: 'code_crawler' },
};
