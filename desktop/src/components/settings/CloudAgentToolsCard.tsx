/**
 * "Cloud agent tools" settings card — which local tools cloud agents may use
 * on this machine.
 *
 * Reads GET {engineUrl}/chat/local-tools (each advertised tool carries
 * `enabled` from the `cloud_tools.disabled_tools` setting) and writes via
 * PUT {engineUrl}/chat/local-tools/exposure. The setting is
 * cloud-authoritative: it rides the whole-blob app_settings sync, so it is
 * controllable from the web too, and the delegation engine re-reads it on
 * every sweep — a toggle takes effect on the very next delegated call.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Wrench } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";

interface LocalToolItem {
  name: string;
  description: string;
  category: string;
  advertised: boolean;
  enabled: boolean;
  platforms: string[] | null;
}

interface LocalToolsResponse {
  tools?: Array<Partial<LocalToolItem> & { name: string }>;
}

const CATEGORY_LABELS: Record<string, string> = {
  desktop: "Desktop",
  "desktop-web": "Desktop Web",
};

function toolTitle(cloudName: string): string {
  return cloudName
    .replace(/^local_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function CloudAgentToolsCard({
  engineUrl,
}: {
  engineUrl: string | null;
}) {
  const [tools, setTools] = useState<LocalToolItem[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    if (!engineUrl) return;
    try {
      const response = await fetch(`${engineUrl}/chat/local-tools`, {
        signal: AbortSignal.timeout(6000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as LocalToolsResponse;
      const items = (payload.tools ?? [])
        .filter((t) => t.advertised)
        .map((t) => ({
          name: t.name,
          description: t.description ?? "",
          category: t.category ?? "desktop",
          advertised: true,
          enabled: t.enabled !== false,
          platforms: t.platforms ?? null,
        }));
      setTools(items);
      setLoadError(null);
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Engine unreachable",
      );
    }
  }, [engineUrl]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const grouped = useMemo(() => {
    const groups = new Map<string, LocalToolItem[]>();
    for (const tool of tools) {
      const list = groups.get(tool.category) ?? [];
      list.push(tool);
      groups.set(tool.category, list);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [tools]);

  const setToolEnabled = useCallback(
    async (name: string, enabled: boolean) => {
      if (!engineUrl) return;
      const next = tools.map((t) => (t.name === name ? { ...t, enabled } : t));
      setTools(next);
      setSaving(true);
      try {
        const disabled = next.filter((t) => !t.enabled).map((t) => t.name);
        const response = await fetch(
          `${engineUrl}/chat/local-tools/exposure`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ disabled_tools: disabled }),
            signal: AbortSignal.timeout(6000),
          },
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setLoadError(null);
      } catch (error) {
        setLoadError(
          error instanceof Error ? error.message : "Failed to save",
        );
        void refresh();
      } finally {
        setSaving(false);
      }
    },
    [engineUrl, tools, refresh],
  );

  const disabledCount = tools.filter((t) => !t.enabled).length;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Wrench className="h-4 w-4 text-primary" /> Cloud Agent Tools
          {disabledCount > 0 && (
            <Badge variant="secondary">{disabledCount} disabled</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Tools cloud agents may use on this computer. Synced to your account,
          so this is also controllable from the web. Disabled tools are
          refused with a clear error instead of executing.
        </p>
        {loadError && (
          <p className="text-xs text-red-700 dark:text-red-400">
            Could not reach the engine: {loadError}
          </p>
        )}
        {grouped.map(([category, items]) => (
          <div key={category} className="space-y-2">
            <Label className="text-xs uppercase text-muted-foreground">
              {CATEGORY_LABELS[category] ?? category}
            </Label>
            {items.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center justify-between gap-3"
              >
                <div className="min-w-0">
                  <p className="text-sm">{toolTitle(tool.name)}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {tool.description}
                  </p>
                </div>
                <Switch
                  checked={tool.enabled}
                  disabled={saving}
                  onCheckedChange={(checked) =>
                    void setToolEnabled(tool.name, checked)
                  }
                />
              </div>
            ))}
          </div>
        ))}
        {!loadError && tools.length === 0 && (
          <p className="text-xs text-muted-foreground">Loading tools...</p>
        )}
      </CardContent>
    </Card>
  );
}
