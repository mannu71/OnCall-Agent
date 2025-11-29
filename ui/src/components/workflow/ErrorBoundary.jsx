import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    // Filter out ResizeObserver errors and other harmless errors
    if (
      error?.message?.includes('ResizeObserver loop completed with undelivered notifications') ||
      error?.message?.includes('ResizeObserver loop limit exceeded') ||
      error?.name === 'ResizeObserverError'
    ) {
      return { hasError: false };
    }
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Filter out ResizeObserver errors
    if (
      error?.message?.includes('ResizeObserver loop completed with undelivered notifications') ||
      error?.message?.includes('ResizeObserver loop limit exceeded') ||
      error?.name === 'ResizeObserverError'
    ) {
      return;
    }
    
    console.error('ErrorBoundary caught an error:', error, errorInfo);
  }

  componentDidMount() {
    // Add additional error handling for the component
    window.addEventListener('error', this.handleWindowError);
    window.addEventListener('unhandledrejection', this.handlePromiseRejection);
  }

  componentWillUnmount() {
    window.removeEventListener('error', this.handleWindowError);
    window.removeEventListener('unhandledrejection', this.handlePromiseRejection);
  }

  handleWindowError = (event) => {
    if (
      event.error?.message?.includes('ResizeObserver loop completed with undelivered notifications') ||
      event.error?.message?.includes('ResizeObserver loop limit exceeded')
    ) {
      event.preventDefault();
      return false;
    }
  };

  handlePromiseRejection = (event) => {
    if (
      event.reason?.message?.includes('ResizeObserver loop completed with undelivered notifications') ||
      event.reason?.message?.includes('ResizeObserver loop limit exceeded')
    ) {
      event.preventDefault();
      return false;
    }
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ 
          padding: '20px', 
          textAlign: 'center', 
          border: '1px solid #ff6b6b', 
          borderRadius: '8px',
          backgroundColor: '#fff5f5',
          color: '#ff6b6b'
        }}>
          <h2>Something went wrong</h2>
          <p>An error occurred in the React Flow component.</p>
          <button 
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              padding: '8px 16px',
              backgroundColor: '#ff6b6b',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;