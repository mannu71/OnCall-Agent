import React from 'react';
import PropTypes from 'prop-types';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

/**
 * ErrorBoundary Component
 * 
 * React error boundary for catching and displaying errors in component tree
 * 
 * Features:
 * - Catches React errors
 * - Displays user-friendly error message
 * - Provides recovery options
 * - Logs errors to console
 */

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null
    };
  }

  static getDerivedStateFromError(error) {
    // Update state so the next render will show the fallback UI
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // Log error details to console
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    
    // Update state with error details
    this.setState({
      error,
      errorInfo
    });

    // Call onError callback if provided
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null
    });

    // Call onReset callback if provided
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  render() {
    if (this.state.hasError) {
      // Custom fallback UI if provided
      if (this.props.fallback) {
        return this.props.fallback({
          error: this.state.error,
          errorInfo: this.state.errorInfo,
          resetError: this.handleReset
        });
      }

      // Default fallback UI
      return (
        <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
          <Card className="max-w-2xl w-full border-red-200">
            <CardHeader className="border-b border-red-100 bg-red-50">
              <CardTitle className="text-xl font-bold text-red-900 flex items-center gap-2">
                <AlertTriangle className="w-6 h-6" />
                Something went wrong
              </CardTitle>
            </CardHeader>
            <CardContent className="p-6">
              <div className="space-y-4">
                <p className="text-slate-700">
                  {this.props.errorMessage || 
                    'An unexpected error occurred. Please try refreshing the page or contact support if the problem persists.'}
                </p>

                {this.state.error && (
                  <details className="bg-slate-50 border border-slate-200 rounded p-4">
                    <summary className="cursor-pointer font-semibold text-slate-900 mb-2">
                      Error Details
                    </summary>
                    <div className="space-y-2 text-sm">
                      <div>
                        <p className="font-semibold text-slate-700">Error:</p>
                        <code className="block bg-red-50 border border-red-200 rounded p-2 mt-1 text-red-800 overflow-x-auto">
                          {this.state.error.toString()}
                        </code>
                      </div>
                      {this.state.errorInfo && (
                        <div>
                          <p className="font-semibold text-slate-700">Component Stack:</p>
                          <pre className="block bg-slate-100 border border-slate-200 rounded p-2 mt-1 text-xs text-slate-800 overflow-x-auto">
                            {this.state.errorInfo.componentStack}
                          </pre>
                        </div>
                      )}
                    </div>
                  </details>
                )}

                <div className="flex gap-3 pt-4">
                  <Button
                    onClick={this.handleReset}
                    className="bg-blue-600 hover:bg-blue-700 text-white"
                  >
                    <RefreshCw className="w-4 h-4 mr-2" />
                    Try Again
                  </Button>
                  
                  <Button
                    onClick={() => window.location.reload()}
                    variant="outline"
                  >
                    Reload Page
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      );
    }

    return this.props.children;
  }
}

ErrorBoundary.propTypes = {
  children: PropTypes.node.isRequired,
  fallback: PropTypes.func,
  errorMessage: PropTypes.string,
  onError: PropTypes.func,
  onReset: PropTypes.func
};

ErrorBoundary.defaultProps = {
  fallback: null,
  errorMessage: '',
  onError: null,
  onReset: null
};

export default ErrorBoundary;
