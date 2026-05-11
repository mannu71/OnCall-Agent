import React from 'react';
import PropTypes from 'prop-types';
import { Loader2 } from 'lucide-react';

/**
 * LoadingSpinner Component
 * 
 * Loading indicator component for async operations
 * 
 * Features:
 * - Multiple sizes
 * - Optional message
 * - Centered or inline display
 */

const LoadingSpinner = ({ 
  size, 
  message, 
  centered, 
  className,
  fullScreen 
}) => {
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-6 h-6',
    lg: 'w-8 h-8',
    xl: 'w-12 h-12'
  };

  const textSizeClasses = {
    sm: 'text-xs',
    md: 'text-sm',
    lg: 'text-base',
    xl: 'text-lg'
  };

  const spinnerContent = (
    <div className={`flex ${centered ? 'flex-col items-center justify-center' : 'items-center gap-2'} ${className}`}>
      <Loader2 className={`${sizeClasses[size]} text-slate-400 animate-spin`} />
      {message && (
        <p className={`${textSizeClasses[size]} text-slate-600 ${centered ? 'mt-3' : ''}`}>
          {message}
        </p>
      )}
    </div>
  );

  if (fullScreen) {
    return (
      <div className="fixed inset-0 bg-white bg-opacity-90 flex items-center justify-center z-50">
        {spinnerContent}
      </div>
    );
  }

  if (centered) {
    return (
      <div className="flex items-center justify-center py-12">
        {spinnerContent}
      </div>
    );
  }

  return spinnerContent;
};

LoadingSpinner.propTypes = {
  size: PropTypes.oneOf(['sm', 'md', 'lg', 'xl']),
  message: PropTypes.string,
  centered: PropTypes.bool,
  className: PropTypes.string,
  fullScreen: PropTypes.bool
};

LoadingSpinner.defaultProps = {
  size: 'md',
  message: '',
  centered: false,
  className: '',
  fullScreen: false
};

export default LoadingSpinner;
