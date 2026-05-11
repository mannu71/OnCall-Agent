import React from 'react';
import PropTypes from 'prop-types';
import { CheckCircle2, X } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';

/**
 * SuccessMessage Component
 * 
 * Success message component for displaying positive feedback
 * 
 * Features:
 * - Success message display
 * - Optional title
 * - Dismissible
 * - Action button support
 */

const SuccessMessage = ({ 
  message, 
  title, 
  onDismiss, 
  actionLabel, 
  onAction,
  className 
}) => {
  if (!message) return null;

  return (
    <Alert className={`border-green-200 bg-green-50 ${className}`}>
      <CheckCircle2 className="h-4 w-4 text-green-600" />
      <AlertDescription className="text-green-800">
        {title && <p className="font-semibold mb-1">{title}</p>}
        <p className={title ? 'text-sm' : ''}>{message}</p>
        
        {actionLabel && onAction && (
          <button
            onClick={onAction}
            className="mt-3 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded text-sm font-medium transition-colors"
          >
            {actionLabel}
          </button>
        )}
      </AlertDescription>
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="absolute right-2 top-2 text-green-600 hover:opacity-70"
          aria-label="Dismiss"
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </Alert>
  );
};

SuccessMessage.propTypes = {
  message: PropTypes.string.isRequired,
  title: PropTypes.string,
  onDismiss: PropTypes.func,
  actionLabel: PropTypes.string,
  onAction: PropTypes.func,
  className: PropTypes.string
};

SuccessMessage.defaultProps = {
  title: '',
  onDismiss: null,
  actionLabel: '',
  onAction: null,
  className: ''
};

export default SuccessMessage;
