import { useState, useCallback } from 'react';

/**
 * useToast Hook
 * 
 * Custom hook for managing toast notifications
 * 
 * Usage:
 * const { toasts, showToast, removeToast, clearToasts } = useToast();
 * 
 * showToast('Success!', 'success');
 * showToast('Error occurred', 'error', 10000);
 */

let toastIdCounter = 0;

const useToast = () => {
  const [toasts, setToasts] = useState([]);

  const showToast = useCallback((message, type = 'info', duration = 5000, position = 'top-right') => {
    const id = ++toastIdCounter;
    
    const newToast = {
      id,
      message,
      type,
      duration,
      position,
      show: true
    };

    setToasts(prev => [...prev, newToast]);

    // Auto-remove after duration
    if (duration > 0) {
      setTimeout(() => {
        removeToast(id);
      }, duration);
    }

    return id;
  }, []);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(toast => toast.id !== id));
  }, []);

  const clearToasts = useCallback(() => {
    setToasts([]);
  }, []);

  const showSuccess = useCallback((message, duration, position) => {
    return showToast(message, 'success', duration, position);
  }, [showToast]);

  const showError = useCallback((message, duration, position) => {
    return showToast(message, 'error', duration, position);
  }, [showToast]);

  const showWarning = useCallback((message, duration, position) => {
    return showToast(message, 'warning', duration, position);
  }, [showToast]);

  const showInfo = useCallback((message, duration, position) => {
    return showToast(message, 'info', duration, position);
  }, [showToast]);

  return {
    toasts,
    showToast,
    removeToast,
    clearToasts,
    showSuccess,
    showError,
    showWarning,
    showInfo
  };
};

export default useToast;
