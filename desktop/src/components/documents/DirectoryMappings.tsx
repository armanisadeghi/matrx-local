import { useState, useEffect, useCallback } from "react";
import {
  FolderSync,
  Plus,
  Trash2,
  HardDrive,
  FolderOpen,
  X,
  Loader2,
} from "lucide-react";
import { engine } from "@/lib/api";
import type { DocFolder, DocMappings } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface DirectoryMappingsProps {
  userId: string;
  folders: DocFolder[];
  onClose: () => void;
}

export function DirectoryMappings({
  userId,
  folders,
  onClose,
}: DirectoryMappingsProps) {
  const [mappings, setMappings] = useState<DocMappings | null>(null);
  const [loading, setLoading] = useState(true);
  const [newFolderId, setNewFolderId] = useState("");
  const [newPath, setNewPath] = useState("");
  const [adding, setAdding] = useState(false);

  const loadMappings = useCallback(async () => {
    try {
      const data = await engine.listMappings(userId);
      setMappings(data);
    } catch (err) {
      console.error("Failed to load mappings:", err);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadMappings();
  }, [loadMappings]);

  const handleAdd = async () => {
    if (!newFolderId || !newPath.trim()) return;
    setAdding(true);
    try {
      await engine.createMapping(userId, {
        folder_id: newFolderId,
        local_path: newPath.trim(),
      });
      setNewFolderId("");
      setNewPath("");
      await loadMappings();
    } catch (err) {
      console.error("Failed to create mapping:", err);
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (folderId: string, localPath: string) => {
    try {
      // Mappings are local-only; the path id segment is vestigial.
      await engine.deleteMapping("local", userId, folderId, localPath);
      await loadMappings();
    } catch (err) {
      console.error("Failed to delete mapping:", err);
    }
  };

  // local_mappings is Record<folder_id, local_path[]> — flatten for display.
  const activeMappings = Object.entries(mappings?.local_mappings ?? {}).flatMap(
    ([folderId, paths]) => paths.map((p) => ({ folder_id: folderId, local_path: p })),
  );

  // Flatten folder tree for the select dropdown
  const flatFolders: { id: string; name: string; path: string }[] = [];
  const flatten = (items: DocFolder[], prefix = "") => {
    for (const f of items) {
      const display = prefix ? `${prefix} / ${f.name}` : f.name;
      flatFolders.push({ id: f.id, name: display, path: f.path });
      if (f.children) flatten(f.children, display);
    }
  };
  flatten(folders);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-lg rounded-lg border bg-background p-4 shadow-lg">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <FolderSync className="h-4 w-4" />
            <h3 className="font-semibold">Directory Mappings</h3>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 hover:bg-accent"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <p className="text-xs text-muted-foreground mb-4">
          Map document folders to additional directories on your computer.
          When a note changes, it will be automatically synced to all mapped
          locations.
        </p>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {/* Existing mappings — from local_mappings. The old UI listed the
                (graveyarded) cloud_mappings, which was always empty, so saved
                mappings were invisible and undeletable. */}
            {activeMappings.length > 0 && (
              <div className="mb-4">
                <h4 className="text-xs font-medium text-muted-foreground mb-2">
                  Active Mappings
                </h4>
                <div className="flex flex-col gap-1">
                  {activeMappings.map((m) => {
                    const folder = flatFolders.find(
                      (f) => f.id === m.folder_id,
                    );
                    return (
                      <div
                        key={`${m.folder_id}:${m.local_path}`}
                        className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm"
                      >
                        <FolderOpen className="h-4 w-4 shrink-0 text-amber-500" />
                        <span className="font-medium">
                          {folder?.name ?? m.folder_id}
                        </span>
                        <span className="text-muted-foreground mx-1">→</span>
                        <HardDrive className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="flex-1 truncate text-xs font-mono text-muted-foreground">
                          {m.local_path}
                        </span>
                        <button
                          onClick={() => handleDelete(m.folder_id, m.local_path)}
                          className="text-muted-foreground hover:text-destructive"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Add new mapping */}
            <div className="border-t pt-3">
              <h4 className="text-xs font-medium text-muted-foreground mb-2">
                Add Mapping
              </h4>
              <div className="flex flex-col gap-2">
                <Select
                  value={newFolderId}
                  onValueChange={setNewFolderId}
                >
                  <SelectTrigger aria-label="Cloud folder">
                    <SelectValue placeholder="Select a folder..." />
                  </SelectTrigger>
                  <SelectContent>
                    {flatFolders.map((f) => (
                      <SelectItem key={f.id} value={f.id}>{f.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="flex items-center gap-2">
                  <Input
                    value={newPath}
                    onChange={(e) => setNewPath(e.target.value)}
                    placeholder="/path/to/local/directory"
                    className="flex-1 font-mono"
                  />
                  <Button
                    onClick={handleAdd}
                    disabled={adding || !newFolderId || !newPath.trim()}
                  >
                    {adding ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Plus className="h-4 w-4" />
                    )}
                    Add
                  </Button>
                </div>
              </div>
            </div>

            {/* Device info */}
            {mappings?.device_id && (
              <div className="mt-3 text-xs text-muted-foreground">
                Device ID: {mappings.device_id}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
