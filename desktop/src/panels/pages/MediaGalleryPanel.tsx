/**
 * MediaGalleryPanel — the media library grid as a standalone panel window.
 *
 * Thin composition over the canonical MediaLibrarySection (one MediaDescriptor,
 * one thumb, one action set — see desktop/src/components/media/FEATURE.md).
 * There is no /media-gallery route in the main app; the gallery normally lives
 * inside Media Generation. This panel gives it its own window.
 */

import { MediaLibrarySection } from "@/components/media-gen/MediaLibrarySection";

export function MediaGalleryPanel() {
  return (
    <div className="p-4">
      <MediaLibrarySection />
    </div>
  );
}
