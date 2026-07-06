import { Component } from 'react';

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center p-8 max-w-md">
              <p className="text-red-400 text-lg font-medium mb-2">
                Rendering error
              </p>
              <p className="text-white/50 text-sm font-mono">
                {this.state.error?.message ?? 'Unknown error'}
              </p>
              <button
                onClick={() => this.setState({ hasError: false, error: null })}
                className="mt-4 px-4 py-2 bg-white/10 hover:bg-white/20 rounded text-sm transition-colors"
              >
                Retry
              </button>
            </div>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
