import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw, RotateCcw } from "lucide-react";
import { emitClientLog } from "@/hooks/use-unified-log";

interface Props { children: ReactNode; route: string; resetKey: number; onReset: () => void; onOpenRecovery: () => void }
interface State { error: Error | null }

export class SurfaceErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };
  static getDerivedStateFromError(error: Error): State { return { error }; }
  override componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  override componentDidCatch(error: Error, info: ErrorInfo) {
    emitClientLog("error", `Page ${this.props.route} crashed: ${error.message} — ${info.componentStack?.trim().split("\n")[0] ?? ""}`, "client");
  }
  override render() {
    if (!this.state.error) return this.props.children;
    return <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
      <AlertTriangle className="h-10 w-10 text-amber-500" />
      <h2 className="text-lg font-semibold">This view stopped working</h2>
      <p className="max-w-md text-center text-sm text-muted-foreground">{this.state.error.message}</p>
      <div className="flex gap-2"><button onClick={this.props.onReset} className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"><RotateCcw className="h-4 w-4" />Reset view</button><button onClick={this.props.onOpenRecovery} className="flex items-center gap-2 rounded-md border px-4 py-2 text-sm"><RefreshCw className="h-4 w-4" />Recovery Center</button></div>
    </div>;
  }
}
