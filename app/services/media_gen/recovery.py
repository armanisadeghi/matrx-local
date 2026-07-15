"""Canonical recovery controllers for local image and video generation."""

from __future__ import annotations

from typing import Any

from app.launcher import ServiceController, get_registry


def register_media_recovery_controllers() -> None:
    registry = get_registry()

    def image_snapshot() -> dict[str, Any]:
        from app.services.image_gen.jobs import get_image_job_runner
        from app.services.image_gen.service import get_image_gen_service

        return {
            "service": get_image_gen_service().get_status(),
            "worker": get_image_job_runner().snapshot(),
        }

    def image_probe() -> dict[str, Any]:
        snapshot = image_snapshot()
        status = snapshot["service"]
        worker = snapshot["worker"]
        stale_load = bool(status["is_loading"] and (status["load_age_seconds"] or 0) > 600)
        needs_worker = worker["queued_jobs"] > 0 and not worker["active"]
        return {**snapshot, "healthy": not stale_load and not needs_worker, "stale_load": stale_load}

    def image_start() -> dict[str, Any]:
        from app.services.image_gen.jobs import get_image_job_runner

        get_image_job_runner().ensure_running()
        registry.ready("image_gen", **image_probe())
        return image_snapshot()

    async def image_stop() -> dict[str, Any]:
        from app.services.image_gen.jobs import get_image_job_runner
        from app.services.image_gen.service import get_image_gen_service

        registry.stopping("image_gen")
        svc = get_image_gen_service()
        cancel = svc.request_cancel()
        await get_image_job_runner().stop()
        status = svc.get_status()
        if status["is_loading"] or status["is_generating"]:
            reason = "native model operation is still active; engine restart is the safe escalation"
            registry.degraded("image_gen", reason, cancel=cancel)
            return {"stopped": False, "cancel": cancel, "requires_engine_restart": True, "reason": reason}
        unload = await svc.unload_model()
        registry.stopped("image_gen", queue_preserved=True)
        return {"stopped": True, "unload": unload, "queue_preserved": True}

    async def image_repair() -> dict[str, Any]:
        before = image_probe()
        if before["stale_load"]:
            # Diffusers model loading is native/blocking and cannot be killed
            # safely in-process. State that explicitly instead of pretending.
            registry.degraded("image_gen", "stale native model load requires engine restart", **before)
            return {"repaired": False, "requires_engine_restart": True, "before": before}
        after = image_start()
        return {"repaired": True, "before": before, "after": after}

    registry.register_controller(
        "image_gen",
        ServiceController(
            probe=image_probe,
            refresh=image_snapshot,
            repair=image_repair,
            stop=image_stop,
            start=image_start,
            snapshot=image_snapshot,
        ),
    )

    def video_snapshot() -> dict[str, Any]:
        from app.services.video_gen.service import get_video_gen_service

        return get_video_gen_service().get_status()

    def video_probe() -> dict[str, Any]:
        status = video_snapshot()
        stale_load = bool(status["is_loading"] and (status["load_age_seconds"] or 0) > 600)
        return {"healthy": not stale_load, "stale_load": stale_load, **status}

    async def video_stop() -> dict[str, Any]:
        from app.services.video_gen.service import get_video_gen_service

        registry.stopping("video_gen")
        svc = get_video_gen_service()
        status = svc.get_status()
        active_id = status.get("active_job_id")
        cancel = svc.request_cancel_job(active_id) if active_id else {"found": False}
        if status["is_loading"] or active_id:
            reason = "native video operation is still active; engine restart is the safe escalation"
            registry.degraded("video_gen", reason, cancel=cancel)
            return {"stopped": False, "requires_engine_restart": True, "cancel": cancel, "reason": reason}
        result = await svc.unload_model()
        registry.stopped("video_gen", queue_preserved=True)
        return {"stopped": bool(result.get("success")), "queue_preserved": True, "result": result}

    def video_start() -> dict[str, Any]:
        status = video_probe()
        registry.ready("video_gen", **status)
        return status

    async def video_repair() -> dict[str, Any]:
        status = video_probe()
        if status["stale_load"] or status.get("active_job_id"):
            registry.degraded("video_gen", "active native operation cannot be reset safely in-process", **status)
            return {"repaired": False, "requires_engine_restart": True, "status": status}
        return {"repaired": True, "status": video_start()}

    registry.register_controller(
        "video_gen",
        ServiceController(
            probe=video_probe,
            refresh=video_snapshot,
            repair=video_repair,
            stop=video_stop,
            start=video_start,
            snapshot=video_snapshot,
        ),
    )
