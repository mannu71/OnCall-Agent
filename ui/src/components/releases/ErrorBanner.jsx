import React from 'react';
import PropTypes from 'prop-types';
import { AlertTriangle, XCircle, X } from 'lucide-react';

/**
 * ErrorBanner Component
 * 
 * Page-level error banner for displaying prominent errors
 * 
 * Features:
 * - Full-width banner display
 * - Dismissible
 * - Multiple severity levels
 * - Action button support
 */

const ErrorBanner = ({ 
  message, 
  title, 
  severity, 
  onDismiss, 
  actionLabel, 
  onAction,
  className 
}) => {
  if (!message) return null;

  const severityStyles = {
    error: {
      bg: 'bg-red-50',
      border: 'border-red-200',
      text: 'text-red-800',
      icon: 'text-red-600',
      button: 'bg-red-600 hover:bg-red-700 text-white'
    },
    warning: {
      bg: 'bg-yellow-50',
      border: 'border-yellow-200',
      text: 'text-yellow-800',
      icon: 'text-yellow-600',
      button: 'bg-yellow-600 hover:bg-yellow-700 text-white'
    },
    info: {
      bg: 'bg-blue-50',
      border: 'border-blue-200',
      text: 'text-blue-800',
      icon: 'text-blue-600',
      button: 'bg-blue-600 hover:bg-blue-700 text-white'
    }
  };

  const styles = severityStyles[severity];
  const Icon = severity === 'warning' ? AlertTriangle : XCircle;

  return (
    <div className={`${styles.bg} ${styles.border} border rounded-lg p-4 ${className}`}>
      <div className="flex items-start gap-3">
        <Icon className={`w-5 h-5 ${styles.icon} flex-shrink-0 mt-0.5`} />
        
        <div className="flex-1 min-w-0">
          {title && (
            <h3 className={`font-semibold ${styles.text} mb-1`}>
              {title}
            </h3>
          )}
          <p className={`${styles.text} ${title ? 'text-sm' : ''}`}>
            {message}
          </p>
          
          {actionLabel && onAction && (
            <button
              onClick={onAction}
              className={`mt-3 px-4 py-2 rounded text-sm font-medium ${styles.button} transition-colors`}
            >
              {actionLabel}
            </button>
          )}
        </div>

        {onDismiss && (
          <button
            onClick={onDismiss}
            className={`${styles.icon} hover:opacity-70 flex-shrink-0`}
            aria-label="Dismiss"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>
    </div>
  );
};

ErrorBanner.propTypes = {
  message: PropTypes.string.isRequired,
  title: PropTypes.string,
  severity: PropTypes.oneOf(['error', 'warning', 'info']),
  onDismiss: PropTypes.func,
  actionLabel: PropTypes.string,
  onAction: PropTypes.func,
  className: PropTypes.string
};

ErrorBanner.defaultProps = {
  title: '',
  severity: 'error',
  onDismiss: null,
  actionLabel: '',
  onAction: null,
  className: ''
};

export default ErrorBanner;
