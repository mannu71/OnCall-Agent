// Prebuilt workflow templates shown in the "New Workflow" gallery.
//
// Each template is in the canvas-normalized shape LangflowEditor consumes
// (node: { id, type, x, y, name, status, params }; edge: { id, source,
// sourceSlot, target, targetSlot }). They are modeled on real working
// workflows but SANITIZED: AWS profiles, log groups, repo names, DB
// connections and wiki URLs/credentials are blanked to placeholders, and every
// schedule starts disabled so instantiating a template never auto-runs. The
// user fills the blanks in the editor, then saves as a normal workflow.

const CLOUDWATCH_RCA_SYSTEM = `You are the on-call engineer. Your goal is to diagnose production incidents using CloudWatch logs and metrics.
## Investigation Protocol
1. **Start with alarms** — always call cloudwatch_list_alarms(state_value="ALARM") first. If any are firing, they define the blast radius.
2. **Detect anomalies** — call cloudwatch_detect_anomalies on the affected log groups. Focus on severity=critical/high and z_score > 2. Note the time window.
3. **Identify error patterns** — call cloudwatch_analyze_patterns(pattern_types=["error","critical"]). Sort by occurrence_count. Group semantically similar patterns.
4. **Drill into raw evidence** — for any pattern with occurrence_count > 5 or z_score > 3, call cloudwatch_search_logs with a targeted Insights query and drill_down=true to capture stack traces.
5. **Correlate across services** — if errors span multiple log groups, call cloudwatch_correlate_logs with the correlation_id or trace_id from the error messages.
6. **Confirm with metrics** — cross-check log error spikes against Lambda error rates, ALB 5xx counts, or RDS connection counts.
## Output Format
Always end your investigation with:
**Summary**: One sentence describing what happened.
**Root Cause**: The specific error pattern or service that caused the incident.
**Evidence**: Log group, timestamp range, occurrence count or z_score, example message.
**Impact**: Which services/users affected and estimated scope.
**Recommended Action**: Concrete next step.
**Confidence**: high / medium / low — and why if medium/low.
## Rules
- Never conclude without at least one raw log example.
- If evidence_grade is "low" or data_quality.partial is true, say so explicitly and qualify your confidence.
- If credentials are expired, report immediately — do not retry tool calls.
- Stop after 10 tool calls unless the root cause is still unconfirmed; report what you found so far.`;

const CODE_INVESTIGATION_SYSTEM = `You are a code investigation assistant for the connected repository. Use the code search tool to answer questions and trace root causes in the codebase.
## Protocol
1. **Locate** — find the relevant symbol(s), file(s), or feature with the code search tool.
2. **Read** — open the body of each candidate to confirm relevance.
3. **Trace** — follow callers/callees and imports to map how the code connects.
4. **Explain** — summarize the answer or root cause, citing concrete file paths and line numbers.
## Rules
- Always cite file paths (and line numbers where possible) as evidence.
- Prefer the indexed code graph over guessing; if a symbol is ambiguous, list the candidates.
- If you cannot find something after a focused search, say so rather than inventing it.`;

const EXAMPLE_SQL = `-- db: <your-database>
-- label: Example Daily Count
SELECT COUNT(*) AS value
FROM your_table
WHERE created_at >= {current_date}
  AND created_at < {next_date};`;

const FULLSTACK_RCA_SYSTEM = `You are a full-stack on-call engineer. Diagnose production incidents end-to-end by correlating CloudWatch log errors with the code and data that produced them.
## Protocol
1. **Triage logs** — review the pre-computed CloudWatch analysis: alarms, anomalies, and top error patterns. Pick the highest-impact errors (by occurrence_count / z_score) and extract the exception class, message, stack frames, and any correlation/request/trace IDs.
2. **Locate the code** — for each error, use the code crawler to search the connected repositories for the exception, log message, or stack-frame symbols. Open the offending function and trace its callers/callees to find the defect. Repos may span multiple services — search each relevant one.
3. **Check the data** — if an error implicates data (constraint violations, nulls, missing rows, timeouts), query the connected database(s) to confirm the data condition behind the error.
4. **Correlate** — tie the log evidence to the specific code path and/or data state. Follow correlation/trace IDs across services where possible.
## Output
**Summary** — one sentence.
**Root Cause** — the specific code path or data condition, with file path + line and/or the query result that proves it.
**Evidence** — log group + example error, code citation (file:line), and any DB finding.
**Impact** — services/users affected.
**Recommended Action** — concrete fix or mitigation.
**Confidence** — high / medium / low, with reasoning.
## Rules
- Ground every conclusion in concrete evidence: a raw log line AND a code citation or query result.
- If logs point at a service whose repo isn't connected, say so and name the repo to add.
- Never assert a root cause you haven't located in code or data.`;

const CODE_RCA_SYSTEM = `You are a code-defect RCA specialist. This incident was routed to you as application/code-related.
1. Review the pre-computed CloudWatch analysis: top error patterns, exception classes, stack frames, correlation/trace IDs.
2. Use the code crawler to search the connected repositories for the exception, log message, or stack-frame symbols. Open the offending function and trace its callers/callees.
3. Report the root cause with a concrete code citation (file:line), the raw log line that proves it, impact, and a recommended fix. State confidence (high/medium/low). If the implicated service's repo isn't connected, name the repo to add.`;

const DB_RCA_SYSTEM = `You are a data/database RCA specialist. This incident was routed to you as data-related.
1. Review the pre-computed CloudWatch analysis for the failing operation: SQL errors, constraint violations, timeouts, connection-pool exhaustion, missing/inconsistent rows.
2. Query the connected database(s) to confirm the data condition behind the error (use targeted, read-only queries — never full scans).
3. Report the root cause with the query result as evidence, the raw log line, impact, and a recommended fix or mitigation. State confidence (high/medium/low).`;

export const WORKFLOW_TEMPLATES = [
  {
    id: 'cloudwatch-rca',
    label: 'CloudWatch RCA',
    description: 'Investigate a CloudWatch incident: pulls error patterns, anomalies and alarms, then an agent performs root-cause analysis.',
    icon: 'cloud',
    type: 'workflow',
    nodes: [
      { id: 'cwrca_schedule', type: 'schedule', x: 430, y: 40, name: 'Schedule', status: 'idle',
        params: { frequency: 'Daily', time: '09:00', days: 'Mon,Tue,Wed,Thu,Fri', tz: 'UTC', enabled: false } },
      { id: 'cwrca_lm', type: 'language_model', x: 420, y: 360, name: 'Language Model', status: 'idle',
        params: { llm: '', temp: '0.4' } },
      { id: 'cwrca_tool', type: 'cloudwatch_tool', x: 420, y: 700, name: 'CloudWatch Tool', status: 'idle',
        params: { region: 'us-east-1', profile: '', groups: '', analysis: 'error-patterns',
                  range: '1h', threshold: '10', alerts: 'false', activeAlarmsOnly: 'false', analysis_depth: 'deep' } },
      { id: 'cwrca_agent', type: 'agent', x: 1030, y: 240, name: 'RCA Agent', status: 'idle',
        params: { maxIter: '10', system: CLOUDWATCH_RCA_SYSTEM } },
    ],
    edges: [
      { id: 'cwrca_e_lm', source: 'cwrca_lm', sourceSlot: 'lm', target: 'cwrca_agent', targetSlot: 'lm' },
      { id: 'cwrca_e_tool', source: 'cwrca_tool', sourceSlot: 'tool', target: 'cwrca_agent', targetSlot: 'tools' },
      { id: 'cwrca_e_sched', source: 'cwrca_schedule', sourceSlot: 'trigger', target: 'cwrca_agent', targetSlot: 'trigger' },
    ],
  },
  {
    id: 'code-crawler-investigation',
    label: 'Code Crawler Investigation',
    description: 'Ask questions about a codebase: an agent uses the code crawler to find symbols, trace call paths and explain how features work.',
    icon: 'search',
    type: 'workflow',
    nodes: [
      { id: 'ccinv_lm', type: 'language_model', x: 100, y: 128, name: 'Language Model', status: 'idle',
        params: { llm: '' } },
      { id: 'ccinv_tool', type: 'code_search_tool', x: 100, y: 460, name: 'Code Crawler', status: 'idle',
        // Select one or more repos; the agent searches across all of them.
        params: { repos: '' } },
      { id: 'ccinv_agent', type: 'agent', x: 660, y: 200, name: 'Investigation Agent', status: 'idle',
        params: { maxIter: '10', system: CODE_INVESTIGATION_SYSTEM } },
    ],
    edges: [
      { id: 'ccinv_e_tool', source: 'ccinv_tool', sourceSlot: 'tool', target: 'ccinv_agent', targetSlot: 'tools' },
      { id: 'ccinv_e_lm', source: 'ccinv_lm', sourceSlot: 'lm', target: 'ccinv_agent', targetSlot: 'lm' },
    ],
  },
  {
    id: 'scheduled-oncall-report',
    label: 'Scheduled On-Call Report',
    description: 'Run SQL on a schedule and publish the results to a wiki page — a starting point for a daily on-call status report.',
    icon: 'calendar',
    type: 'workflow',
    nodes: [
      { id: 'soc_schedule', type: 'schedule', x: 108, y: 148, name: 'Schedule', status: 'idle',
        params: { frequency: 'Daily', time: '09:00', days: 'Mon,Tue,Wed,Thu,Fri', tz: 'UTC', enabled: false } },
      { id: 'soc_database', type: 'database', x: 178, y: 560, name: 'Database', status: 'idle',
        params: { server: '' } },
      { id: 'soc_orchestrator', type: 'orchestrator', x: 725, y: 273, name: 'SQL Orchestrator', status: 'idle',
        params: { sqlFile: 'OnCall Report.sql', sqlContent: EXAMPLE_SQL } },
      { id: 'soc_wiki', type: 'wiki', x: 1115, y: 496, name: 'Wiki', status: 'idle',
        params: { format: 'Table', platform: 'Azure DevOps Wiki', wikiUrl: '', pagePath: '',
                  project: '', pat: '', tokenVar: 'ADO_WIKI_PAT' } },
    ],
    edges: [
      { id: 'soc_e_sched', source: 'soc_schedule', sourceSlot: 'trigger', target: 'soc_orchestrator', targetSlot: 'trigger' },
      { id: 'soc_e_db', source: 'soc_database', sourceSlot: 'tool', target: 'soc_orchestrator', targetSlot: 'tool' },
      { id: 'soc_e_wiki', source: 'soc_orchestrator', sourceSlot: 'result', target: 'soc_wiki', targetSlot: 'msg' },
    ],
  },
  {
    id: 'full-stack-incident-rca',
    label: 'Full-Stack Incident RCA',
    description: 'Analyze CloudWatch logs, then trace each error into the owning codebase(s) and the database to find the root cause across the whole stack.',
    icon: 'layers',
    type: 'workflow',
    nodes: [
      { id: 'fsr_schedule', type: 'schedule', x: 760, y: 40, name: 'Schedule', status: 'idle',
        params: { frequency: 'Daily', time: '09:00', days: 'Mon,Tue,Wed,Thu,Fri', tz: 'UTC', enabled: false } },
      { id: 'fsr_lm', type: 'language_model', x: 120, y: 120, name: 'Language Model', status: 'idle',
        params: { llm: '', temp: '0.2' } },
      { id: 'fsr_cw', type: 'cloudwatch_tool', x: 120, y: 380, name: 'CloudWatch Tool', status: 'idle',
        params: { region: 'us-east-1', profile: '', groups: '', analysis: 'error-patterns',
                  range: '1h', threshold: '10', alerts: 'false', activeAlarmsOnly: 'false', analysis_depth: 'deep' } },
      { id: 'fsr_code', type: 'code_search_tool', x: 120, y: 640, name: 'Code Crawler', status: 'idle',
        params: { repos: '' } },
      { id: 'fsr_db', type: 'database', x: 120, y: 880, name: 'Database', status: 'idle',
        params: { server: '' } },
      { id: 'fsr_agent', type: 'agent', x: 780, y: 380, name: 'Full-Stack RCA Agent', status: 'idle',
        params: { maxIter: '15', system: FULLSTACK_RCA_SYSTEM } },
    ],
    edges: [
      { id: 'fsr_e_lm', source: 'fsr_lm', sourceSlot: 'lm', target: 'fsr_agent', targetSlot: 'lm' },
      { id: 'fsr_e_cw', source: 'fsr_cw', sourceSlot: 'tool', target: 'fsr_agent', targetSlot: 'tools' },
      { id: 'fsr_e_code', source: 'fsr_code', sourceSlot: 'tool', target: 'fsr_agent', targetSlot: 'tools' },
      { id: 'fsr_e_db', source: 'fsr_db', sourceSlot: 'tool', target: 'fsr_agent', targetSlot: 'tools' },
      { id: 'fsr_e_sched', source: 'fsr_schedule', sourceSlot: 'trigger', target: 'fsr_agent', targetSlot: 'trigger' },
    ],
  },
  {
    id: 'routed-rca-specialists',
    label: 'Routed RCA Specialists',
    description: 'A semantic router classifies the incident from its CloudWatch errors and dispatches to a specialist agent — code-defect (code crawler) or data (database).',
    icon: 'route',
    type: 'workflow',
    nodes: [
      { id: 'rr_lm', type: 'language_model', x: 100, y: 60, name: 'Language Model', status: 'idle',
        params: { llm: '', temp: '0.2' } },
      { id: 'rr_schedule', type: 'schedule', x: 100, y: 300, name: 'Schedule', status: 'idle',
        params: { frequency: 'Daily', time: '09:00', days: 'Mon,Tue,Wed,Thu,Fri', tz: 'UTC', enabled: false } },
      { id: 'rr_cw', type: 'cloudwatch_tool', x: 100, y: 540, name: 'CloudWatch Tool', status: 'idle',
        params: { region: 'us-east-1', profile: '', groups: '', analysis: 'error-patterns',
                  range: '1h', threshold: '10', alerts: 'false', activeAlarmsOnly: 'false', analysis_depth: 'deep' } },
      { id: 'rr_router', type: 'router', x: 440, y: 160, name: 'Semantic Router', status: 'idle',
        params: {
          routes: { code: 'rr_code_agent', data: 'rr_db_agent' },
          routes_description: {
            code: 'Application or code-level errors: exceptions, stack traces, null references, logic bugs, failed deployments.',
            data: 'Database or data-level errors: SQL errors, constraint violations, query timeouts, connection-pool exhaustion, missing or inconsistent rows.',
          },
        } },
      { id: 'rr_code', type: 'code_search_tool', x: 460, y: 560, name: 'Code Crawler', status: 'idle',
        params: { repos: '' } },
      { id: 'rr_db', type: 'database', x: 460, y: 780, name: 'Database', status: 'idle',
        params: { server: '' } },
      { id: 'rr_code_agent', type: 'agent', x: 860, y: 60, name: 'Code-RCA Agent', status: 'idle',
        params: { maxIter: '12', system: CODE_RCA_SYSTEM } },
      { id: 'rr_db_agent', type: 'agent', x: 860, y: 380, name: 'DB-RCA Agent', status: 'idle',
        params: { maxIter: '12', system: DB_RCA_SYSTEM } },
    ],
    edges: [
      { id: 'rr_e_lm_router', source: 'rr_lm', sourceSlot: 'lm', target: 'rr_router', targetSlot: 'model' },
      { id: 'rr_e_lm_code', source: 'rr_lm', sourceSlot: 'lm', target: 'rr_code_agent', targetSlot: 'lm' },
      { id: 'rr_e_lm_db', source: 'rr_lm', sourceSlot: 'lm', target: 'rr_db_agent', targetSlot: 'lm' },
      { id: 'rr_e_sched', source: 'rr_schedule', sourceSlot: 'trigger', target: 'rr_router', targetSlot: 'trigger' },
      { id: 'rr_e_route_code', source: 'rr_router', sourceSlot: 'route-output', target: 'rr_code_agent', targetSlot: 'input' },
      { id: 'rr_e_route_db', source: 'rr_router', sourceSlot: 'route-output', target: 'rr_db_agent', targetSlot: 'input' },
      { id: 'rr_e_cw_code', source: 'rr_cw', sourceSlot: 'tool', target: 'rr_code_agent', targetSlot: 'tools' },
      { id: 'rr_e_cw_db', source: 'rr_cw', sourceSlot: 'tool', target: 'rr_db_agent', targetSlot: 'tools' },
      { id: 'rr_e_code', source: 'rr_code', sourceSlot: 'tool', target: 'rr_code_agent', targetSlot: 'tools' },
      { id: 'rr_e_db', source: 'rr_db', sourceSlot: 'tool', target: 'rr_db_agent', targetSlot: 'tools' },
    ],
  },
];

export default WORKFLOW_TEMPLATES;
