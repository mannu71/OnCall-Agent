import React from 'react';
import PropTypes from 'prop-types';
import { AlertTriangle, XCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';

/**
 * ErrorMessage Component
 * 
 * Inline error message component for displaying errors within forms or sections
 * 
 * Features:
 * - Inline error display
 * - Icon support
 * - Customizable styling
 */

const ErrorMessage = ({ message, title, variant, className, onDismiss }) => {
  if (!message) return null;

  const variantStyles = {
    error: 'border-red-200 bg-red-50',
    warning: 'border-yellow-200 bg-yellow-50',
    info: 'border-blue-200 bg-blue-50'
  };

  const iconStyles = {
    error: 'text-red-600',
    warning: 'text-yellow-600',
    info: 'text-blue-600'
  };

  const textStyles = {
    error: 'text-red-800',
    warning: 'text-yellow-800',
    info: 'text-blue-800'
  };

  const Icon = variant === 'warning' ? AlertTriangle : XCircle;

  return (
    <Alert className={`${variantStyles[variant]} ${className}`}>
      <Icon className={`h-4 w-4 ${iconStyles[variant]}`} />
      <AlertDescription className={textStyles[variant]}>
        {title && <p className="font-semibold mb-1">{title}</p>}
        <p className={title ? 'text-sm' : ''}>{message}</p>
      </AlertDescription>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className={`absolute right-2 top-2 ${iconStyles[variant]} hover:opacity-70`}
          aria-label="Dismiss"
        >
          <XCircle className="w-4 h-4" />
        </button>
      )}
    </Alert>
  );
};

ErrorMessage.propTypes = {
  message: PropTypes.string,
  title: PropTypes.string,
  variant: PropTypes.oneOf(['error', 'warning', 'info']),
  className: PropTypes.string,
  onDismiss: PropTypes.func
};

ErrorMessage.defaultProps = {
  message: '',
  title: '',
  variant: 'error',
  className: '',
  onDismiss: null
};

export default ErrorMessage;
