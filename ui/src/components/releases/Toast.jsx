import React, { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { CheckCircle2, XCircle, AlertTriangle, Info, X } from 'lucide-react';

/**
 * Toast Component
 * 
 * Transient notification component for displaying temporary messages
 * 
 * Features:
 * - Auto-dismiss after timeout
 * - Multiple types (success, error, warning, info)
 * - Manual dismiss
 * - Slide-in animation
 */

const Toast = ({ 
  message, 
  type, 
  duration, 
  onClose, 
  position,
  show 
}) => {
  const [visible, setVisible] = useState(show);
  const [exiting, setExiting] = useState(false);

  useEffect(() => {
    setVisible(show);
    setExiting(false);
  }, [show]);

  useEffect(() => {
    if (visible && duration > 0) {
      const timer = setTimeout(() => {
        handleClose();
      }, duration);

      return () => clearTimeout(timer);
    }
  }, [visible, duration]);

  const handleClose = () => {
    setExiting(true);
    setTimeout(() => {
      setVisible(false);
      if (onClose) onClose();
    }, 300); // Match animation duration
  };

  if (!visible) return null;

  const typeStyles = {
    success: {
      bg: 'bg-green-50',
      border: 'border-green-200',
      text: 'text-green-800',
      icon: 'text-green-600',
      Icon: CheckCircle2
    },
    error: {
      bg: 'bg-red-50',
      border: 'border-red-200',
      text: 'text-red-800',
      icon: 'text-red-600',
      Icon: XCircle
    },
    warning: {
      bg: 'bg-yellow-50',
      border: 'border-yellow-200',
      text: 'text-yellow-800',
      icon: 'text-yellow-600',
      Icon: AlertTriangle
    },
    info: {
      bg: 'bg-blue-50',
      border: 'border-blue-200',
      text: 'text-blue-800',
      icon: 'text-blue-600',
      Icon: Info
    }
  };

  const styles = typeStyles[type];
  const Icon = styles.Icon;

  const positionStyles = {
    'top-right': 'top-4 right-4',
    'top-left': 'top-4 left-4',
    'bottom-right': 'bottom-4 right-4',
    'bottom-left': 'bottom-4 left-4',
    'top-center': 'top-4 left-1/2 -translate-x-1/2',
    'bottom-center': 'bottom-4 left-1/2 -translate-x-1/2'
  };

  const animationClass = exiting 
    ? 'opacity-0 translate-y-2' 
    : 'opacity-100 translate-y-0';

  return (
    <div 
      className={`fixed ${positionStyles[position]} z-50 transition-all duration-300 ${animationClass}`}
      style={{ minWidth: '300px', maxWidth: '500px' }}
    >
      <div className={`${styles.bg} ${styles.border} border rounded-lg shadow-lg p-4`}>
        <div className="flex items-start gap-3">
          <Icon className={`w-5 h-5 ${styles.icon} flex-shrink-0 mt-0.5`} />
          
          <p className={`flex-1 ${styles.text} text-sm`}>
            {message}
          </p>

          <button
            onClick={handleClose}
            className={`${styles.icon} hover:opacity-70 flex-shrink-0`}
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};

Toast.propTypes = {
  message: PropTypes.string.isRequired,
  type: PropTypes.oneOf(['success', 'error', 'warning', 'info']),
  duration: PropTypes.number,
  onClose: PropTypes.func,
  position: PropTypes.oneOf([
    'top-right',
    'top-left',
    'bottom-right',
    'bottom-left',
    'top-center',
    'bottom-center'
  ]),
  show: PropTypes.bool
};

Toast.defaultProps = {
  type: 'info',
  duration: 5000,
  onClose: null,
  position: 'top-right',
  show: true
};

export default Toast;

/**
 * ToastContainer Component
 * 
 * Container for managing multiple toast notifications
 */

export const ToastContainer = ({ toasts, onRemove }) => {
  return (
    <>
      {toasts.map((toast, index) => (
        <Toast
          key={toast.id || index}
          message={toast.message}
          type={toast.type}
          duration={toast.duration}
          position={toast.position}
          show={toast.show !== false}
          onClose={() => onRemove && onRemove(toast.id || index)}
        />
      ))}
    </>
  );
};

ToastContainer.propTypes = {
  toasts: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
      message: PropTypes.string.isRequired,
      type: PropTypes.oneOf(['success', 'error', 'warning', 'info']),
      duration: PropTypes.number,
      position: PropTypes.string,
      show: PropTypes.bool
    })
  ).isRequired,
  onRemove: PropTypes.func
};

ToastContainer.defaultProps = {
  onRemove: null
};
