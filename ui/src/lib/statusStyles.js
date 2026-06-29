/**
 * Status / severity → semantic Badge variant mapping (single source of truth).
 *
 * Replaces the scattered raw Tailwind color classes (`bg-blue-100 text-blue-700`…)
 * with the semantic `Badge` variants defined in `components/ui/badge.jsx`
 * (info/success/danger/warning/muted). Use these so status colors are consistent
 * and themeable in one place.
 */

/** Map an execution status to a Badge variant. */
export function statusToVariant(status) {
    switch ((status || '').toLowerCase()) {
        case 'running':
        case 'in_progress':
            return 'info';
        case 'completed':
        case 'success':
            return 'success';
        case 'failed':
        case 'error':
            return 'danger';
        case 'pending':
        case 'queued':
            return 'warning';
        case 'paused':
        default:
            return 'muted';
    }
}

/** Map an alert/issue severity to a Badge variant (collapses the 5-level scale). */
export function severityToVariant(severity) {
    switch ((severity || '').toLowerCase()) {
        case 'critical':
        case 'high':
            return 'danger';
        case 'medium':
        case 'warning':
            return 'warning';
        case 'low':
        case 'info':
            return 'info';
        default:
            return 'muted';
    }
}

// Full 5-level severity + 4-state alert-status class maps. Centralised here (was
// inline in LogWatchConfig) but the EXACT colors are preserved — these scales
// need more distinctions than the collapsed Badge variants above.
export const SEVERITY_BADGE_CLASS = {
    critical: 'bg-red-100 text-red-800 hover:bg-red-100',
    high: 'bg-orange-100 text-orange-800 hover:bg-orange-100',
    medium: 'bg-yellow-100 text-yellow-800 hover:bg-yellow-100',
    low: 'bg-blue-100 text-blue-800 hover:bg-blue-100',
    info: 'bg-green-100 text-green-800 hover:bg-green-100',
};

export const ALERT_STATUS_BADGE_CLASS = {
    new: 'bg-red-100 text-red-800 hover:bg-red-100',
    acknowledged: 'bg-orange-100 text-orange-800 hover:bg-orange-100',
    resolved: 'bg-green-100 text-green-800 hover:bg-green-100',
    dismissed: 'bg-gray-100 text-gray-800 hover:bg-gray-100',
};
