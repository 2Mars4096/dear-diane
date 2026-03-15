import { Component, type ReactNode } from "react";

interface Props {
  fallback?: ReactNode;
  name?: string;
  isActive?: boolean;
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  componentStack?: string;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, componentStack: undefined };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, componentStack: undefined };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error(
      `[ErrorBoundary:${this.props.name ?? "unknown"}]`,
      error,
      info.componentStack,
    );
    this.setState({ componentStack: info.componentStack });
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && this.props.isActive && !prevProps.isActive) {
      this.setState({ hasError: false, error: null, componentStack: undefined });
    }
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
            {this.state.componentStack && (
              <pre className="mb-4 max-h-40 overflow-auto rounded bg-gray-50 px-3 py-2 text-left text-[10px] text-gray-500 whitespace-pre-wrap">
                {this.state.componentStack.trim()}
              </pre>
            )}
            <button
              onClick={() =>
                this.setState({
                  hasError: false,
                  error: null,
                  componentStack: undefined,
                })
              }
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
