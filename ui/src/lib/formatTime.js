/**
 * Timestamp display helpers.
 *
 * The backend emits all timestamps in UTC (ISO-8601 with a trailing `Z`).
 * For display we localize to the operator-configured global timezone, which
 * is persisted server-side and cached in localStorage (key `oncall.timezone`)
 * by the Settings page.
 *
 * Scheduling/epoch math stays on the backend in UTC — this is display only.
 */

const LS_TIMEZONE = 'oncall.timezone';
const DEFAULT_TIMEZONE = 'Europe/London';

/** Read the operator's global timezone from the localStorage cache. */
export function getDisplayTimezone() {
    try {
        const raw = globalThis.localStorage?.getItem(LS_TIMEZONE);
        if (raw) {
            // usePersistedState stores JSON-encoded values.
            try {
                return JSON.parse(raw);
            } catch {
                return raw;
            }
        }
    } catch {
        /* localStorage unavailable (SSR / sandbox) */
    }
    return DEFAULT_TIMEZONE;
}

/**
 * Format a UTC timestamp in the configured global timezone.
 *
 * @param {string|number|Date} utcValue ISO string, epoch ms, or Date.
 * @param {object} [options] Intl.DateTimeFormat options (merged over a sane default).
 * @param {string} [tz] Override timezone; defaults to the global setting.
 * @returns {string} Localized string, or '' for falsy input, or the raw value if unparseable.
 */
export function formatInTimezone(utcValue, options = {}, tz = getDisplayTimezone()) {
    if (utcValue === null || utcValue === undefined || utcValue === '') return '';
    const date = utcValue instanceof Date ? utcValue : new Date(utcValue);
    if (Number.isNaN(date.getTime())) return String(utcValue);

    const fmtOptions = {
        year: 'numeric',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
        ...options,
    };

    try {
        return new Intl.DateTimeFormat(undefined, { timeZone: tz, ...fmtOptions }).format(date);
    } catch {
        // Invalid tz — fall back to UTC rather than throwing.
        return new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', ...fmtOptions }).format(date);
    }
}

/**
 * Format a timestamp as a short clock time (e.g. "14:05") in the configured
 * global timezone — for chat bubbles, trace steps, and other compact displays.
 *
 * @param {string|number|Date} utcValue ISO string, epoch ms, or Date.
 * @param {string} [tz] Override timezone; defaults to the global setting.
 * @returns {string}
 */
export function formatClock(utcValue, tz = getDisplayTimezone()) {
    if (utcValue === null || utcValue === undefined || utcValue === '') return '';
    const date = utcValue instanceof Date ? utcValue : new Date(utcValue);
    if (Number.isNaN(date.getTime())) return String(utcValue);
    const opts = { hour: '2-digit', minute: '2-digit', hour12: false };
    try {
        return new Intl.DateTimeFormat(undefined, { timeZone: tz, ...opts }).format(date);
    } catch {
        return new Intl.DateTimeFormat(undefined, { timeZone: 'UTC', ...opts }).format(date);
    }
}
