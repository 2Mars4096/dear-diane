import { Component, type ReactNode } from "react";

interface Props {
  fallback?: ReactNode;
  name?: string;
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="flex items-center justify-center h-full">
          <div className="text-center px-6 py-8 max-w-sm">
            <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-3">
              <span className="text-red-400 text-lg">!</span>
            </div>
            <p className="text-sm font-medium text-gray-700 mb-1">
              {this.props.name || "This panel"} encountered an error
            </p>
            <p className="text-xs text-gray-400 mb-4 break-all">
              {this.state.error?.message}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="px-4 py-2 text-xs font-medium text-indigo-600 bg-indigo-50 hover:bg-indigo-100 rounded-lg transition-colors"
            >
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
