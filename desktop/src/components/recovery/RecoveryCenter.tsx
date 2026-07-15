import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { recovery } from "@/lib/recovery";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { getEngineRecoveryStatus, runEngineRecoveryAction, type EngineRecoveryStatus, type RecoveryServiceAction } from "@/lib/api";

interface Props { open: boolean; onOpenChange: (open: boolean) => void; route: string }

export function RecoveryCenter({ open, onOpenChange, route }: Props) {
  const operations = useSyncExternalStore(recovery.subscribe, recovery.getSnapshot, recovery.getSnapshot);
  const [engineRecovery, setEngineRecovery] = useState<EngineRecoveryStatus | null>(null);
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [escalation, setEscalation] = useState<string | null>(null);
  const refreshServices = useCallback(async () => {
    try {
      setEngineRecovery(await getEngineRecoveryStatus());
      setServiceError(null);
    } catch (cause) {
      setServiceError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);
  useEffect(() => { if (open) void refreshServices(); }, [open, refreshServices]);
  const runServiceAction = useCallback(async (service: string, action: RecoveryServiceAction) => {
    setEscalation(null);
    const result = await recovery.repairService(service, action, async () => {
      const operation = await runEngineRecoveryAction(service, action);
      if (JSON.stringify(operation.result ?? {}).includes('"requires_engine_restart":true')) {
        setEscalation(`${service} could not be repaired safely in place. Restart the engine to recover it.`);
      }
    });
    if (!result.ok) setServiceError(result.error ?? result.message);
    await refreshServices();
  }, [refreshServices]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Recovery Center</DialogTitle>
          <DialogDescription>Start with the narrowest recovery. Your durable jobs, downloads, and settings are preserved.</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Button variant="outline" onClick={() => void recovery.refreshSurface(route)}><RefreshCw className="mr-2 h-4 w-4" />Refresh data</Button>
          <Button variant="outline" onClick={() => void recovery.resetSurface(route)}><RotateCcw className="mr-2 h-4 w-4" />Reset view</Button>
          <Button variant="outline" onClick={() => void recovery.reloadRenderer()}>Reload interface</Button>
          <Button variant="destructive" onClick={() => void recovery.restartEngine()}>Restart engine</Button>
          <Button variant="destructive" onClick={() => void recovery.restartApp("Recovery Center application restart")}>Restart app</Button>
        </div>
        <section className="space-y-2 border-t pt-3">
          <div className="flex items-center justify-between"><h3 className="text-sm font-medium">Feature health</h3><Button size="sm" variant="ghost" onClick={() => void refreshServices()}><RefreshCw className="mr-1 h-3 w-3" />Refresh</Button></div>
          {serviceError && <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">{serviceError}</p>}
          {escalation && <div className="flex items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs"><span>{escalation}</span><Button size="sm" variant="destructive" onClick={() => void recovery.restartEngine()}>Restart engine</Button></div>}
          {!engineRecovery && !serviceError && <p className="text-xs text-muted-foreground">Loading service capabilities…</p>}
          {engineRecovery && Object.entries(engineRecovery.services).map(([name, service]) => {
            const actions = (["probe", "refresh", "repair", "restart"] as const).filter((action) => service.capabilities.includes(action));
            return <div key={name} className="flex items-center gap-2 rounded-lg border p-2 text-xs">
              <div className="min-w-0 flex-1"><div className="font-medium">{name.replace(/_/g, " ")}</div><div className="text-muted-foreground">{service.state}{service.error ? ` · ${service.error}` : ""}</div></div>
              {actions.map((action) => <Button key={action} size="sm" variant={action === "restart" ? "destructive" : "outline"} onClick={() => void runServiceAction(name, action)}>{action === "probe" ? "Check" : action}</Button>)}
            </div>;
          })}
        </section>
        <div className="flex items-center justify-between pt-2">
          <h3 className="text-sm font-medium">Recent attempts</h3>
          <Button size="sm" variant="ghost" onClick={recovery.clearHistory}><Trash2 className="mr-1 h-3 w-3" />Clear</Button>
        </div>
        <div className="max-h-72 space-y-2 overflow-y-auto">
          {operations.length === 0 && <p className="rounded-lg border p-4 text-sm text-muted-foreground">No recovery attempts in this session.</p>}
          {operations.map((item) => (
            <div key={item.id} className="flex items-start gap-3 rounded-lg border p-3 text-sm">
              {item.status === "running" ? <Loader2 className="mt-0.5 h-4 w-4 animate-spin" /> : item.status === "succeeded" ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-500" /> : <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-500" />}
              <div className="min-w-0 flex-1"><div className="font-medium capitalize">{item.level.replace(/-/g, " ")} · {item.target}</div><div className="text-muted-foreground">{item.error ?? item.message}</div></div>
              <span className="text-xs text-muted-foreground">{new Date(item.startedAt).toLocaleTimeString()}</span>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
