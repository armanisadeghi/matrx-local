"""Photos tools — read macOS Photos library via PHPhotoLibrary (macOS only).

Requires:
  - TCC grant: Photos (System Settings → Privacy & Security → Photos)
  - Entitlement: com.apple.security.personal-information.photos-library
  - pyobjc-framework-Photos
"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
from typing import Any

from app.common.platform_ctx import PLATFORM
from app.services.action_needed import os_permission_needed
from app.tools.session import ToolSession
from app.tools.types import ToolResult, ToolResultType

logger = logging.getLogger(__name__)

_PERMISSION_HINT = (
    "Photos access is required. "
    "Grant it in System Settings → Privacy & Security → Photos, then restart the app."
)

# PHAccessLevel constants
_PH_ACCESS_READ_WRITE = 2


def _ensure_photos_access() -> None:
    import Photos  # type: ignore[import]

    status = Photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(_PH_ACCESS_READ_WRITE)
    # 0=notDetermined, 1=restricted, 2=denied, 3=authorized, 4=limited
    if status in (3, 4):
        return
    if status == 0:
        raise PermissionError("Photos access has not been requested.")
    raise PermissionError(f"Photos authorization status={status}. {_PERMISSION_HINT}")


def _asset_to_dict(asset: Any) -> dict[str, Any]:
    # PHAssetMediaType: 0=unknown, 1=image, 2=video, 3=audio
    media_type_map = {0: "unknown", 1: "image", 2: "video", 3: "audio"}
    media_type = media_type_map.get(int(asset.mediaType()), "unknown")

    creation_date = None
    modification_date = None
    try:
        if asset.creationDate():
            creation_date = asset.creationDate().description()
        if asset.modificationDate():
            modification_date = asset.modificationDate().description()
    except Exception:
        pass

    return {
        "identifier": str(asset.localIdentifier()),
        "media_type": media_type,
        "width": int(asset.pixelWidth()),
        "height": int(asset.pixelHeight()),
        "duration": float(asset.duration()) if media_type == "video" else None,
        "creation_date": creation_date,
        "modification_date": modification_date,
        "favorite": bool(asset.isFavorite()),
        "hidden": bool(asset.isHidden()),
        "location": {
            "latitude": asset.location().coordinate().latitude if asset.location() else None,
            "longitude": asset.location().coordinate().longitude if asset.location() else None,
        } if asset.location() else None,
    }


def _search_photos_sync(
    media_type: str,
    limit: int,
    favorites_only: bool,
) -> list[dict[str, Any]]:
    import Photos  # type: ignore[import]

    _ensure_photos_access()

    fetch_options = Photos.PHFetchOptions.alloc().init()
    fetch_options.setFetchLimit_(limit)

    # Sort by creation date descending (most recent first)
    sort_descriptor = (
        __import__("Foundation", fromlist=["NSSortDescriptor"])
        .NSSortDescriptor.sortDescriptorWithKey_ascending_("creationDate", False)
    )
    fetch_options.setSortDescriptors_([sort_descriptor])

    if favorites_only:
        import Foundation  # type: ignore[import]
        fetch_options.setPredicate_(Foundation.NSPredicate.predicateWithFormat_("isFavorite == YES"))

    # PHAssetMediaType: 1=image, 2=video
    type_map = {"image": 1, "video": 2, "all": None}
    ph_type = type_map.get(media_type.lower())

    if ph_type is not None:
        result = Photos.PHAsset.fetchAssetsWithMediaType_options_(ph_type, fetch_options)
    else:
        result = Photos.PHAsset.fetchAssetsWithOptions_(fetch_options)

    assets = []
    for i in range(min(result.count(), limit)):
        try:
            assets.append(_asset_to_dict(result.objectAtIndex_(i)))
        except Exception as exc:
            logger.debug("Skipping asset at index %d: %s", i, exc)
    return assets


async def tool_search_photos(
    session: ToolSession,
    media_type: str = "image",
    limit: int = 25,
    favorites_only: bool = False,
) -> ToolResult:
    """Search and list photos/videos from the macOS Photos library.

    Args:
        media_type: "image", "video", or "all" (default "image").
        limit: Maximum assets to return (default 25, max 200).
        favorites_only: If True, only return favorited assets.
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Photos tool is only available on macOS.", type=ToolResultType.ERROR)

    limit = max(1, min(limit, 200))
    if media_type not in ("image", "video", "all"):
        media_type = "image"

    try:
        assets = await asyncio.get_event_loop().run_in_executor(
            None, _search_photos_sync, media_type, limit, favorites_only
        )
    except PermissionError as exc:
        return ToolResult(output=f"Photos permission denied. {_PERMISSION_HINT}\nDetail: {exc}", type=ToolResultType.ERROR, action_needed=os_permission_needed(feature="Photos library", permission_key="photos", source="tool.photos"))
    except Exception as exc:
        logger.exception("tool_search_photos failed")
        return ToolResult(output=f"Failed to search photos: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Found {len(assets)} {media_type} asset(s).",
        metadata={"assets": assets, "count": len(assets), "media_type": media_type},
        type=ToolResultType.SUCCESS,
    )


def _get_photo_sync(identifier: str, thumbnail_size: int) -> dict[str, Any]:
    import Photos  # type: ignore[import]
    import AppKit  # type: ignore[import]

    _ensure_photos_access()

    result = Photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_([identifier], None)
    if result.count() == 0:
        raise FileNotFoundError(f"No asset found with identifier: {identifier}")

    asset = result.objectAtIndex_(0)
    asset_dict = _asset_to_dict(asset)

    # Request a thumbnail image
    manager = Photos.PHImageManager.defaultManager()
    target_size = __import__("Foundation", fromlist=["NSMakeSize"]).NSMakeSize(
        thumbnail_size, thumbnail_size
    )

    img_result: list[Any] = [None]
    done = threading.Event()

    options = Photos.PHImageRequestOptions.alloc().init()
    options.setSynchronous_(True)
    options.setDeliveryMode_(Photos.PHImageRequestOptionsDeliveryModeHighQualityFormat)
    options.setResizeMode_(Photos.PHImageRequestOptionsResizeModeExact)

    def handler(image: Any, info: Any) -> None:
        img_result[0] = image
        done.set()

    manager.requestImageForAsset_targetSize_contentMode_options_resultHandler_(
        asset, target_size, Photos.PHImageContentModeAspectFit, options, handler
    )
    done.wait(timeout=15.0)

    thumbnail_b64 = None
    if img_result[0] is not None:
        try:
            tiff_data = img_result[0].TIFFRepresentation()
            bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(tiff_data)
            png_data = bitmap.representationUsingType_properties_(
                AppKit.NSBitmapImageFileTypePNG, {}
            )
            thumbnail_b64 = base64.b64encode(bytes(png_data)).decode()
        except Exception as exc:
            logger.debug("Failed to encode thumbnail: %s", exc)

    asset_dict["thumbnail_png_base64"] = thumbnail_b64
    asset_dict["thumbnail_size"] = thumbnail_size
    return asset_dict


async def tool_get_photo(
    session: ToolSession,
    identifier: str,
    thumbnail_size: int = 512,
) -> ToolResult:
    """Get a single photo asset with a thumbnail by its identifier.

    Args:
        identifier: The PHAsset localIdentifier (from search_photos results).
        thumbnail_size: Max thumbnail dimension in pixels (default 512, max 2048).
    """
    if not PLATFORM["is_mac"]:
        return ToolResult(output="Photos tool is only available on macOS.", type=ToolResultType.ERROR)

    thumbnail_size = max(64, min(thumbnail_size, 2048))

    try:
        asset = await asyncio.get_event_loop().run_in_executor(
            None, _get_photo_sync, identifier, thumbnail_size
        )
    except PermissionError as exc:
        return ToolResult(output=f"Photos permission denied. {_PERMISSION_HINT}\nDetail: {exc}", type=ToolResultType.ERROR, action_needed=os_permission_needed(feature="Photos library", permission_key="photos", source="tool.photos"))
    except FileNotFoundError as exc:
        return ToolResult(output=str(exc), type=ToolResultType.ERROR)
    except Exception as exc:
        logger.exception("tool_get_photo failed")
        return ToolResult(output=f"Failed to get photo: {exc}", type=ToolResultType.ERROR)

    return ToolResult(
        output=f"Photo asset: {asset['identifier']} ({asset['media_type']}, {asset['width']}×{asset['height']})",
        metadata=asset,
        type=ToolResultType.SUCCESS,
    )
