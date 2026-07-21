import type { MatrixOption } from "@/lib/prompt-matrix/types";
import type { NamedList } from "./types";

export function enabledOptionCount(options: readonly MatrixOption[]): number {
  return options.filter((o) => o.enabled && o.value.trim().length > 0).length;
}

export function enabledOptionCountForList(list: NamedList): number {
  return enabledOptionCount(list.options);
}

export function formatListSelectLabel(list: NamedList): string {
  const count = enabledOptionCountForList(list);
  return `${list.name} (${count})`;
}
