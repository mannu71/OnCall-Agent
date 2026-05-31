import { Cpu, Server, Shield, Sliders } from 'lucide-react';
import { version } from '../../../package.json';

export const LS_WORKSPACE = 'oncall.workspaceName';
export const LS_TIMEZONE = 'oncall.timezone';
export const LS_CONFIRM_DESTRUCTIVE = 'oncall.confirmDestructive';

export const DEFAULT_WORKSPACE = 'KYC Protect — Production';
export const DEFAULT_TIMEZONE = 'Europe/London';

export const TIMEZONE_LABELS = {
    'Europe/London': 'Europe / London',
    'Europe/Berlin': 'Europe / Berlin',
    'America/New_York': 'America / New York',
    'America/Los_Angeles': 'America / Los Angeles',
    'Asia/Singapore': 'Asia / Singapore',
};

export const TIMEZONE_OPTIONS = [
    { value: 'Europe/London', label: 'Europe / London (UTC+00)' },
    { value: 'Europe/Berlin', label: 'Europe / Berlin (UTC+01)' },
    { value: 'America/New_York', label: 'America / New York (UTC−05)' },
    { value: 'America/Los_Angeles', label: 'America / Los Angeles (UTC−08)' },
    { value: 'Asia/Singapore', label: 'Asia / Singapore (UTC+08)' },
];

export const EMPTY_MCP_FORM = {
    name: '',
    command: '',
    args: '',
    type: 'stdio',
    icon: '🔧',
    description: '',
    env: '',
};

export const EMPTY_LLM_FORM = {
    name: '',
    provider: 'AWS Bedrock',
    model: '',
    icon: '🧠',
    temperature: 0,
    use_for_embeddings: false,
};

export const EMPTY_BEDROCK_CREDS = {
    access_key_id: '',
    secret_access_key: '',
    session_token: '',
    region: 'us-east-1',
    description: '',
};

export function buildSections({ serverCount, llmCount, certCount }) {
    return [
        {
            id: 'general',
            title: 'General',
            sub: 'Workspace & defaults',
            icon: Sliders,
            count: null,
            desc: 'Look, feel, and default behavior of the OnCall Agent.',
        },
        {
            id: 'mcp',
            title: 'MCP servers',
            sub: 'Data sources & tools',
            icon: Server,
            count: serverCount,
            desc: 'Data sources and tools the agent can reach over MCP.',
        },
        {
            id: 'models',
            title: 'Models',
            sub: 'Credentials & LLMs',
            icon: Cpu,
            count: llmCount,
            desc: 'Provider credentials and the named models your workflows reference.',
        },
        {
            id: 'certs',
            title: 'Certificates',
            sub: 'Trusted CAs (TLS)',
            icon: Shield,
            count: certCount,
            desc: 'Trusted CAs for secure database and service connections.',
        },
    ];
}

export function buildGauges({ apiHealth, systemStatus, llmCount, connectedLlms }) {
    const apiChecking = apiHealth == null;
    const apiOk = apiHealth?.status === 'healthy';
    const schedulerRunning = systemStatus?.scheduler_running ?? apiHealth?.scheduler_running;
    const jobCount = systemStatus?.scheduled_jobs ?? 0;
    const activeExec = systemStatus?.active_executions ?? apiHealth?.active_workflows ?? 0;
    const testedLlms = connectedLlms || 0;
    const idleLlms = Math.max(0, llmCount - testedLlms);
    const modelsLive = testedLlms > 0 ? testedLlms : llmCount;

    return [
        {
            label: 'API',
            value: apiChecking ? 'Checking…' : apiOk ? 'Healthy' : 'Unreachable',
            meta: apiChecking
                ? 'Contacting API…'
                : apiOk
                  ? `${apiHealth.latencyMs ?? '—'} ms · v${version}`
                  : apiHealth.message || 'Check connection',
            v: apiChecking ? 0 : apiOk ? 100 : 12,
            c: apiChecking ? '#94a3b8' : apiOk ? '#10b981' : '#ef4444',
            num: apiChecking ? '—' : apiOk ? '✓' : '!',
        },
        {
            label: 'Scheduler',
            value: schedulerRunning ? 'Running' : 'Stopped',
            meta: `${jobCount} jobs queued`,
            v: schedulerRunning && jobCount > 0
                ? Math.min(92, 28 + jobCount * 16)
                : jobCount > 0
                  ? Math.min(48, jobCount * 16)
                  : 0,
            c: '#3b82f6',
            num: String(jobCount),
        },
        {
            label: 'Models',
            value: testedLlms > 0 ? `${testedLlms} live` : llmCount ? `${llmCount} configured` : 'None',
            meta: llmCount
                ? `${idleLlms} idle · ${llmCount} total`
                : 'None configured',
            v: llmCount
                ? (testedLlms > 0
                    ? Math.min(100, Math.round((testedLlms / llmCount) * 100))
                    : 0)
                : 0,
            c: '#7c3aed',
            num: llmCount ? `${modelsLive}/${llmCount}` : '0',
        },
        {
            label: 'Executions',
            value: `${activeExec} active`,
            meta: 'In-memory runs',
            v: activeExec > 0 ? Math.min(100, activeExec * 22 + 24) : 0,
            c: '#f59e0b',
            num: String(activeExec),
        },
    ];
}
