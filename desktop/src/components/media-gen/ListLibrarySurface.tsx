import { ListLibraryCore } from "./ListLibrarySection";
import { MediaGenSurface } from "./surfaces/MediaGenSurface";

export interface ListLibrarySurfaceProps {
  surface: "dialog" | "popover";
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger?: React.ReactNode;
  title?: string;
  description?: string;
}

/** Dialog or popover host — same component as the Lists tab. */
export function ListLibrarySurface({
  surface,
  open,
  onOpenChange,
  trigger,
  title = "Lists",
  description = "Named option lists for {{variable}} sweeps.",
}: ListLibrarySurfaceProps) {
  return (
    <MediaGenSurface
      kind={surface}
      open={open}
      onOpenChange={onOpenChange}
      trigger={trigger}
      title={title}
      description={description}
      contentClassName="p-3"
    >
      <ListLibraryCore />
    </MediaGenSurface>
  );
}
