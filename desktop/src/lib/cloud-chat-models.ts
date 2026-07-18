import type { ModelOption } from "@/hooks/use-chat";

export interface CloudModelOption extends ModelOption {
  catalogId: string;
}

export function cloudModelDisplayName(
  modelReference: string,
  models: CloudModelOption[],
): string {
  if (!modelReference) return "";
  return models.find(
    (model) => model.id === modelReference || model.catalogId === modelReference,
  )?.label ?? modelReference;
}
