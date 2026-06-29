export const MCP_SERVER_ICONS = [
    { value: '🔧', label: '🔧 Tool' },
    { value: '🎭', label: '🎭 Playwright' },
    { value: '🗄️', label: '🗄️ Database' },
    { value: '📊', label: '📊 Analytics' },
    { value: '🐘', label: '🐘 PostgreSQL' },
    { value: '🔍', label: '🔍 Search' },
    { value: '📁', label: '📁 FileSystem' },
    { value: '☁️', label: '☁️ Cloud' },
    { value: '🌐', label: '🌐 Web' },
    { value: '📡', label: '📡 API' },
    { value: '⚡', label: '⚡ Fast' },
    { value: '🔐', label: '🔐 Security' },
    { value: '📝', label: '📝 Notes' },
    { value: '🤖', label: '🤖 Bot' },
    { value: '💾', label: '💾 Storage' },
];

export const LLM_ICONS = [
    { value: '🧠', label: '🧠 Brain' },
    { value: '⚡', label: '⚡ Lightning' },
    { value: '🤖', label: '🤖 Robot' },
    { value: '✨', label: '✨ Sparkles' },
    { value: '🚀', label: '🚀 Rocket' },
    { value: '💡', label: '💡 Lightbulb' },
    { value: '🎯', label: '🎯 Target' },
    { value: '🔮', label: '🔮 Crystal Ball' },
    { value: '🌟', label: '🌟 Star' },
    { value: '💫', label: '💫 Dizzy' },
    { value: '🎓', label: '🎓 Graduation' },
    { value: '📚', label: '📚 Books' },
    { value: '🔬', label: '🔬 Microscope' },
    { value: '🎨', label: '🎨 Art' },
    { value: '🌈', label: '🌈 Rainbow' },
];

export const BEDROCK_MODEL_OPTIONS = [
    'anthropic.claude-3-5-sonnet-20241022-v2:0',
    'anthropic.claude-3-5-haiku-20241022-v1:0',
    'anthropic.claude-3-opus-20240229-v1:0',
    'anthropic.claude-3-sonnet-20240229-v1:0',
    'anthropic.claude-3-haiku-20240307-v1:0',
    'meta.llama3-1-70b-instruct-v1:0',
    'meta.llama3-1-8b-instruct-v1:0',
    'amazon.titan-embed-text-v1',
    'amazon.titan-embed-text-v2:0',
    'cohere.embed-english-v3',
    'cohere.embed-multilingual-v3',
];

export const REASONING_MODELS = ['o1', 'o1-mini', 'o1-preview', 'o3', 'o3-mini', 'o4-mini'];

export const isReasoningModel = (model) => {
    if (!model) return false;
    const modelLower = model.toLowerCase();
    return REASONING_MODELS.some((rm) => modelLower.startsWith(rm));
};

export const extractInputVariables = (argsString) => {
    const matches = argsString.match(/\$\{input:([^}]+)\}/g) || [];
    return matches.map((m) => m.match(/\$\{input:([^}]+)\}/)[1]);
};

export const parseArgsString = (argsString) => {
    let args = argsString
        .split('\n')
        .map((arg) => arg.trim())
        .filter((arg) => arg.length > 0);

    if (args.length !== 1 || !args[0].includes(', ')) {
        return args;
    }

    const singleArg = args[0];
    if (!singleArg.startsWith('-y, ') && !singleArg.startsWith('-y,')) {
        return args;
    }

    const parts = [];
    let remaining = singleArg;
    const firstComma = remaining.indexOf(',');

    if (firstComma === -1) {
        return args;
    }

    parts.push(remaining.substring(0, firstComma).trim());
    remaining = remaining.substring(firstComma + 1).trim();

    const urlStart = remaining.indexOf('://');
    const secondComma = remaining.indexOf(', ');

    if (secondComma !== -1 && (urlStart === -1 || secondComma < urlStart)) {
        parts.push(remaining.substring(0, secondComma).trim());
        remaining = remaining.substring(secondComma + 1).trim();
    }

    if (remaining) {
        parts.push(remaining);
    }

    return parts.length >= 2 ? parts : args;
};

export const parseEnvVars = (envString) => {
    if (!envString.trim()) {
        return {};
    }
    return JSON.parse(envString);
};

export const normalizeProvider = (p) => (p || '').toLowerCase().replace(/\s+/g, '');

export const findBedrockKey = (modelKeys) =>
    modelKeys.find(
        (k) =>
            k.provider === 'AWS Bedrock'
            || normalizeProvider(k.provider).includes('bedrock'),
    );

export const keyIsConfigured = (mk) =>
    mk && (mk.has_api_key || mk.has_secret_key || mk.has_access_credentials || mk.endpoint);

export const providerBadgeVariant = (p) => {
    switch (p) {
        case 'OpenAI':
            return 'info';
        case 'Anthropic':
            return 'violet';
        case 'Google':
            return 'success';
        case 'Azure OpenAI':
            return 'info';
        case 'AWS Bedrock':
            return 'warning';
        case 'Groq':
            return 'warning';
        default:
            return 'muted';
    }
};

export const formatCommandPreview = (command, args) => {
    const list = Array.isArray(args) ? args : [];
    const preview = list.slice(0, 2).join(' ');
    return `${command || ''}${preview ? ` ${preview}` : ''}${list.length > 2 ? '…' : ''}`.trim();
};
