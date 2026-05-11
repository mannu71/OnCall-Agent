import React from 'react';
import PropTypes from 'prop-types';
import { CheckCircle2, Circle, Loader2 } from 'lucide-react';

/**
 * ProgressBar Component
 * 
 * Progress indicator for multi-step operations
 * 
 * Features:
 * - Linear progress bar
 * - Step-by-step progress
 * - Percentage display
 * - Status indicators
 */

export const LinearProgressBar = ({ 
  progress, 
  label, 
  showPercentage, 
  variant,
  className 
}) => {
  const variantStyles = {
    default: 'bg-blue-600',
    success: 'bg-green-600',
    warning: 'bg-yellow-600',
    error: 'bg-red-600'
  };

  const percentage = Math.min(Math.max(progress, 0), 100);

  return (
    <div className={className}>
      {(label || showPercentage) && (
        <div className="flex items-center justify-between text-sm text-slate-600 mb-2">
          {label && <span>{label}</span>}
          {showPercentage && <span>{percentage}%</span>}
        </div>
      )}
      <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${variantStyles[variant]}`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
};

LinearProgressBar.propTypes = {
  progress: PropTypes.number.isRequired,
  label: PropTypes.string,
  showPercentage: PropTypes.bool,
  variant: PropTypes.oneOf(['default', 'success', 'warning', 'error']),
  className: PropTypes.string
};

LinearProgressBar.defaultProps = {
  label: '',
  showPercentage: true,
  variant: 'default',
  className: ''
};

/**
 * StepProgressBar Component
 * 
 * Step-by-step progress indicator
 */

export const StepProgressBar = ({ 
  steps, 
  currentStep, 
  className 
}) => {
  return (
    <div className={className}>
      <div className="flex items-center justify-between">
        {steps.map((step, index) => {
          const stepNumber = index + 1;
          const isCompleted = stepNumber < currentStep;
          const isCurrent = stepNumber === currentStep;
          const isPending = stepNumber > currentStep;

          return (
            <React.Fragment key={step.id || index}>
              {/* Step Circle */}
              <div className="flex flex-col items-center">
                <div
                  className={`
                    w-10 h-10 rounded-full flex items-center justify-center font-semibold text-sm
                    transition-all duration-300
                    ${isCompleted ? 'bg-green-600 text-white' : ''}
                    ${isCurrent ? 'bg-blue-600 text-white ring-4 ring-blue-100' : ''}
                    ${isPending ? 'bg-slate-200 text-slate-500' : ''}
                  `}
                >
                  {isCompleted ? (
                    <CheckCircle2 className="w-5 h-5" />
                  ) : isCurrent ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <Circle className="w-5 h-5" />
                  )}
                </div>
                <p
                  className={`
                    mt-2 text-xs font-medium text-center max-w-[100px]
                    ${isCompleted || isCurrent ? 'text-slate-900' : 'text-slate-500'}
                  `}
                >
                  {step.label}
                </p>
              </div>

              {/* Connector Line */}
              {index < steps.length - 1 && (
                <div className="flex-1 h-0.5 mx-2 mb-6">
                  <div
                    className={`
                      h-full transition-all duration-300
                      ${isCompleted ? 'bg-green-600' : 'bg-slate-200'}
                    `}
                  />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};

StepProgressBar.propTypes = {
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
      label: PropTypes.string.isRequired
    })
  ).isRequired,
  currentStep: PropTypes.number.isRequired,
  className: PropTypes.string
};

StepProgressBar.defaultProps = {
  className: ''
};

/**
 * CircularProgressBar Component
 * 
 * Circular progress indicator
 */

export const CircularProgressBar = ({ 
  progress, 
  size, 
  strokeWidth, 
  showPercentage,
  className 
}) => {
  const percentage = Math.min(Math.max(progress, 0), 100);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percentage / 100) * circumference;

  return (
    <div className={`relative inline-flex items-center justify-center ${className}`}>
      <svg width={size} height={size} className="transform -rotate-90">
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="currentColor"
          strokeWidth={strokeWidth}
          fill="none"
          className="text-slate-200"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          stroke="currentColor"
          strokeWidth={strokeWidth}
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="text-blue-600 transition-all duration-300"
        />
      </svg>
      {showPercentage && (
        <span className="absolute text-sm font-semibold text-slate-900">
          {Math.round(percentage)}%
        </span>
      )}
    </div>
  );
};

CircularProgressBar.propTypes = {
  progress: PropTypes.number.isRequired,
  size: PropTypes.number,
  strokeWidth: PropTypes.number,
  showPercentage: PropTypes.bool,
  className: PropTypes.string
};

CircularProgressBar.defaultProps = {
  size: 80,
  strokeWidth: 8,
  showPercentage: true,
  className: ''
};

export default LinearProgressBar;
