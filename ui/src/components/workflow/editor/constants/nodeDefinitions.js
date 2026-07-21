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
  // The unified LLM node — dropdown populated from Settings
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
    category: 'Memory', label: 'Memory', icon: 'db',
    desc: 'Typed memory recall + capture',
    slots: [
      { kind: 'memory-select', id: 'memoryTypes', label: 'Memory types',
        hint: 'Which memory tiers this agent uses. Semantic = facts learned from past runs; '
            + 'Pinned = operator facts injected every turn; Knowledge base = curated known '
            + 'issues & runbooks; Session = recall of earlier turns in this chat. Leave all '
            + 'on unless you want to narrow it.',
        options: [
          { value: 'semantic', label: 'Semantic (learned facts)' },
          { value: 'pinned',   label: 'Pinned facts' },
          { value: 'kb',       label: 'Knowledge base' },
          { value: 'session',  label: 'Session history' },
        ] },
      // Advanced tuning — sensible defaults (global scope, 5 matches); tucked
      // into the properties panel's Advanced group so the node shows only
      // Memory types by default.
      { kind: 'field',    id: 'collection', label: 'Collection', mono: true, advanced: true,
        hint: 'Optional namespace to scope recall to (e.g. a repo name). Leave blank to '
            + 'recall from the shared global memory.' },
      { kind: 'field',    id: 'topK',       label: 'Top K', suffix: 'matches', advanced: true,
        hint: 'Maximum number of memories to pull into context per recall. Default 5 — '
            + 'higher adds recall but costs tokens.' },
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
      { kind: 'aws-profile-select', id: 'profile', label: 'AWS profile' },
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
      { kind: 'port-in',  id: 'lm',    label: 'Model', portType: 'model', optional: true },
      { kind: 'port-out', id: 'tool',   label: 'Tool', portType: 'tool' },
    ],
  },
  postgres_tool: {
    category: 'Tools', label: 'Postgres', icon: 'server', paletteHidden: true,
    desc: 'SQL query tool',
    slots: [
      { kind: 'field',    id: 'connection', label: 'Connection', mono: true },
      { kind: 'port-in',  id: 'lm',         label: 'Model', portType: 'model', optional: true },
      { kind: 'port-out', id: 'tool',       label: 'Tool', portType: 'tool' },
    ],
  },
  database: {
    category: 'Tools', label: 'Database', icon: 'db',
    desc: 'Database MCP server from Settings',
    paletteHidden: true,  // superseded by mcp_server node
    slots: [
      { kind: 'db-select', id: 'server', label: 'Server' },
      { kind: 'port-in',   id: 'lm',     label: 'Model', portType: 'model', optional: true },
      { kind: 'port-out',  id: 'tool',   label: 'Tool', portType: 'tool' },
    ],
  },
  mcp_server: {
    category: 'Tools', label: 'MCP', icon: 'server',
    desc: 'Connect MCP servers from Settings · wire each independently',
    slots: [
      { kind: 'mcp-select', id: 'servers', label: 'MCP Servers' },
      { kind: 'port-in',    id: 'lm',      label: 'Model', portType: 'model', optional: true },
      { kind: 'port-out',   id: 'tool',    label: 'Tool', portType: 'tool' },
    ],
  },
  code_search_tool: {
    category: 'Tools', label: 'Code Search', icon: 'search',
    desc: 'Search repositories via Code Crawler',
    slots: [
      { kind: 'repo-select', id: 'repos', label: 'Repositories' },
      { kind: 'port-in',     id: 'lm',    label: 'Model', portType: 'model', optional: true },
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
  // Legacy JSON-textarea subagent editor — superseded by `subagent_window`
  // (drag tool nodes into a named box on the canvas). Kept registered
  // (paletteHidden) so older saved workflows still load; the canvas
  // auto-generates an equivalent node of this type at save time from the
  // subagent_window boxes, so the two never need to coexist in the UI.
  subagents: {
    category: 'Agents', label: 'Subagents', icon: 'workflow', paletteHidden: true,
    desc: 'Define named subagents · wire to Agent',
    slots: [
      { kind: 'subagents-editor', id: 'subagents', label: 'Subagent Definitions' },
      { kind: 'port-out', id: 'specialists', label: 'Subagents', portType: 'data' },
    ],
  },
  // Visual subagent grouping box: drag Tool-category nodes inside its
  // boundary on the canvas to scope them to this named subagent, instead of
  // the main agent. Rendered as a dashed frame (see SubagentWindowFrame in
  // LangflowEditor.jsx), not a normal port-driven node for its BODY —
  // membership is geometric (child node's parentId === this node's id). It
  // carries one real wireable port: `specialists`, to the Agent's own
  // `specialists` input (same as the legacy Subagents node) so the
  // connection is visible on the canvas. Model choice is per-TOOL (each
  // Tool node has its own optional `lm` port), not per-window — a subagent
  // can mix tools that each use a different model.
  subagent_window: {
    category: 'Agents', label: 'Subagent Window', icon: 'workflow',
    desc: 'Group tools into a named subagent · drag tools inside',
    width: 300,
    container: true,
    slots: [
      { kind: 'field',    id: 'name',        label: 'Subagent name' },
      { kind: 'textarea', id: 'description', label: 'Description (optional)' },
      { kind: 'port-out', id: 'specialists', label: 'Subagents', portType: 'data' },
    ],
  },
  agent: {
    category: 'Agents', label: 'Agent', icon: 'workflow',
    desc: 'ReAct loop over tools + memory',
    width: 268,
    slots: [
      { kind: 'port-in',  id: 'trigger',  label: 'Trigger',        portType: 'trigger', optional: true },
      { kind: 'port-in',  id: 'lm',       label: 'Language Model', portType: 'model'   },
      { kind: 'port-in',  id: 'memory',   label: 'Memory',         portType: 'memory', optional: true },
      { kind: 'port-in',  id: 'tools',    label: 'Tools',          portType: 'tool',   multi: true },
      { kind: 'port-in',  id: 'input',    label: 'Input',          portType: 'message', optional: true },
      { kind: 'port-in',  id: 'specialists', label: 'Subagents', portType: 'data', optional: true },
      { kind: 'field',    id: 'profile',  label: 'Profile (optional)', mono: true,
        placeholder: 'e.g. incident-rca-multiagent' },
      { kind: 'textarea', id: 'system',   label: 'Agent instructions' },
      { kind: 'field',    id: 'maxIter',  label: 'Max iterations', suffix: 'steps' },
      { kind: 'skills-picker', id: 'skills', label: 'Skills' },
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
      { kind: 'select',  id: 'writeMode', label: 'Write mode',
        options: ['Overwrite', 'Append', 'Prepend'] },
      { kind: 'select',  id: 'platform', label: 'Platform',
        options: ['Azure DevOps Wiki', 'Confluence', 'GitHub Wiki'] },
      { kind: 'field',   id: 'organization', label: 'Organization', mono: true },
      { kind: 'field',   id: 'project',  label: 'Project',    mono: true },
      { kind: 'field',   id: 'wikiUrl',  label: 'Wiki URL',   mono: true },
      { kind: 'field',   id: 'pagePath', label: 'Page path',  mono: true },
      { kind: 'field',   id: 'pat',      label: 'PAT Token (optional)', mono: true, advanced: true },
      { kind: 'field',   id: 'tokenVar', label: 'Token env var', mono: true, advanced: true },
    ],
  },
};

// ── Default params applied when a node is dropped from the palette ───────────
export const NODE_DEFAULTS = {
  schedule:    { frequency: 'Every 15 min', time: '09:00', days: 'Mon,Tue,Wed,Thu,Fri', tz: 'UTC' },
  vector_memory: { memoryTypes: 'semantic,pinned,kb,session', collection: '', topK: '5' },
  cloudwatch_tool: { region: 'us-east-1', profile: '', groups: '', analysis: 'error-patterns', analysis_depth: 'auto', tool_mode: 'auto', range: '15m', threshold: '10', alerts: 'false' },
  subagents:   { subagents: '[]' },
  subagent_window: { name: 'Subagent', description: '', w: '300', h: '360' },
  agent:       { system: '', maxIter: '10', skills: '', autoLearn: 'false', sandbox: 'false', supervisor_enabled: 'true' },
  if:          { condition: '' },
  wiki:        { format: 'Summary', writeMode: 'Append', platform: 'Azure DevOps Wiki', organization: '', project: '', wikiUrl: '', pagePath: '', pat: '', tokenVar: 'ADO_WIKI_PAT' },
  orchestrator: { sqlFile: '' },
  router:      { routes: {}, routes_description: {} },
  code_search_tool: { repos: '', backend: 'codegraph' },
};
