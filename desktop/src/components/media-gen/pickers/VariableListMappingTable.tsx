/**
 * VariableListMappingTable — one row per {{token}} → list + option count.
 */

import { useListLibraryApp } from "@/contexts/ListLibraryContext";
import { LabelWithInfo } from "../prompts/LabelWithInfo";
import { NamedListPicker, resolveListOptionCount } from "./NamedListPicker";
import { NO_LIST_ID } from "./constants";

export function VariableListMappingTable({
  tokenNames,
  listByVariable,
  onListChange,
}: {
  tokenNames: readonly string[];
  listByVariable: Readonly<Record<string, string>>;
  onListChange: (tokenName: string, listId: string) => void;
}) {
  const [listState] = useListLibraryApp();

  if (tokenNames.length === 0) return null;

  return (
    <div>
      <LabelWithInfo
        label="Variable lists"
        info="Map each base variable once. Numbered uses such as {{color#1}} and {{color#2}} share the color list, draw independently with replacement, and reuse a draw wherever the exact same numbered token repeats."
      />
      <div className="mt-2 overflow-hidden rounded-lg border bg-card">
        <table className="w-full table-fixed text-xs">
          <thead>
            <tr className="border-b bg-muted/40 text-[10px] uppercase tracking-wide text-muted-foreground">
              <th className="w-[28%] px-3 py-2 text-left font-medium">
                Variable
              </th>
              <th className="px-3 py-2 text-left font-medium">List</th>
              <th className="w-16 px-3 py-2 text-right font-medium">Count</th>
            </tr>
          </thead>
          <tbody>
            {tokenNames.map((name) => {
              const listId = listByVariable[name] ?? NO_LIST_ID;
              const count = resolveListOptionCount(listState.lists, listId);
              return (
                <tr
                  key={name}
                  className="border-b last:border-b-0 hover:bg-muted/20"
                >
                  <td className="px-3 py-2 align-middle font-mono text-[11px] text-muted-foreground">
                    {`{{${name}}}`}
                  </td>
                  <td className="px-3 py-2 align-middle">
                    <NamedListPicker
                      value={listId}
                      onChange={(id) => onListChange(name, id)}
                    />
                  </td>
                  <td className="px-3 py-2 text-right align-middle tabular-nums text-muted-foreground">
                    {count !== null ? count : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
