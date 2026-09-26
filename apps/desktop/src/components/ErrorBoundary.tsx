import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Failure } from "./ui";

interface Props {
  /** Changing this resets the boundary — used to clear a crash on navigation. */
  resetKey?: unknown;
  children: ReactNode;
}

interface State {
  error: Error | null;
  stack: string | null;
}

/**
 * A screen that throws must not take the window with it.
 *
 * Without this, one bad render leaves a black rectangle and no way out —
 * there is no address bar to reload and no tab to close. The boundary keeps
 * the navigation alive, shows what happened, and lets the user move on.
 *
 * Deliberately a class: React has no hook for this, and pulling in a library
 * for thirty lines would be the larger cost.
 */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null, stack: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept for the fold-out detail, and logged so a developer running from a
    // terminal sees it without opening the inspector.
    this.setState({ stack: info.componentStack ?? error.stack ?? null });
    console.error("VOLUM: a screen failed to render", error, info);
  }

  override componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null, stack: null });
    }
  }

  override render() {
    const { error, stack } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="py-10">
        <Failure
          message="This part of VOLUM stopped working."
          technical={[error.message, stack].filter(Boolean).join("\n")}
          suggestions={[
            "Move to another screen and come back.",
            "If it keeps happening, restart VOLUM.",
          ]}
        />
        <div className="mt-4">
          <Button onClick={() => this.setState({ error: null, stack: null })}>Try again</Button>
        </div>
      </div>
    );
  }
}
