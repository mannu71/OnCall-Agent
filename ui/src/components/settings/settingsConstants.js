import { Cpu, Server, Shield, Sliders, ToggleRight } from 'lucide-react';
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

/** Current UTC-offset label for a zone, e.g. "GMT+8". Empty string on failure. */
function tzOffsetLabel(tz) {
    try {
        const part = new Intl.DateTimeFormat('en-US', {
            timeZone: tz,
            timeZoneName: 'shortOffset',
        })
            .formatToParts(new Date())
            .find((p) => p.type === 'timeZoneName');
        return part ? part.value : '';
    } catch {
        return '';
    }
}

/** Build the full IANA timezone option list (value + human label with offset). */
function buildTimezoneOptions() {
    let zones;
    try {
        zones = Intl.supportedValuesOf('timeZone');
    } catch {
        // Older runtimes without supportedValuesOf — fall back to a small set.
        zones = [
            'Europe/London', 'Europe/Berlin', 'America/New_York',
            'America/Los_Angeles', 'Asia/Singapore', 'Asia/Kolkata', 'Asia/Tokyo',
        ];
    }
    if (!zones.includes('UTC')) zones = ['UTC', ...zones];
    return zones.map((tz) => {
        const offset = tzOffsetLabel(tz);
        const pretty = tz.replace(/_/g, ' ').replace(/\//g, ' / ');
        return { value: tz, label: offset ? `${pretty} (${offset})` : pretty };
    });
}

export const TIMEZONE_OPTIONS = buildTimezoneOptions();

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
        {
            id: 'features',
            title: 'Feature flags',
            sub: 'Runtime toggles',
            icon: ToggleRight,
            count: null,
            desc: 'Turn optional agent capabilities on or off. Changes apply on the next run — no restart.',
        },
    ];
}

export function buildGauges({ apiHealth, llmCount, hasBedrockCredentials }) {
    const apiChecking = apiHealth == null;
    const apiOk = apiHealth?.status === 'healthy';

    return [
        {
            label: 'API',
            value: apiChecking ? 'Checking…' : apiOk ? 'Healthy' : 'Unreachable',
            meta: apiChecking
                ? 'Contacting API…'
                : apiOk
                  ? (apiHealth.latencyMs != null
                      ? `${apiHealth.latencyMs} ms · v${version}`
                      : `v${version}`)
                  : apiHealth.message || 'Check connection',
            v: apiChecking ? 0 : apiOk ? 100 : 12,
            c: apiChecking ? '#94a3b8' : apiOk ? '#10b981' : '#ef4444',
            num: apiChecking ? '—' : apiOk ? '✓' : '!',
        },
        {
            label: 'Models',
            value: llmCount === 1 ? '1 configured' : llmCount ? `${llmCount} configured` : 'None',
            meta: !llmCount
                ? 'None configured'
                : hasBedrockCredentials
                  ? 'Provider credentials configured'
                  : 'Add provider credentials in Models',
            v: !llmCount ? 0 : hasBedrockCredentials ? 100 : 45,
            c: '#7c3aed',
            num: String(llmCount),
        },
    ];
}
