/**
 * Shared constants for the oncall-agent
 * Centralizes configuration values for better maintainability
 */

// Regex patterns (pre-compiled for performance)
export const TEMPLATE_VAR_REGEX = /\{\{\s*([^}]+?)\s*\}\}/g;
export const ISO_DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;

// Default values
export const DEFAULT_CLIENT_META = { name: 'oncall-agent', version: '1.0.0' };

export default {
  TEMPLATE_VAR_REGEX,
  ISO_DATE_REGEX,
  DEFAULT_CLIENT_META,
};
