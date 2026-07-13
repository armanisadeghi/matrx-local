"""LIVE batch generation against the real engine on port 22199.

Everything else about the batch feature is proven with stub pipelines. This
file proves the only thing a stub cannot: that a prompt-matrix batch, posted to
a real running engine, actually produces real images on disk — through the real
model, the real queue, and the real worker.

It is opt-in because it loads weights and uses the GPU:

    MATRX_LIVE_GEN=1 uv run pytest tests/smoke/test_media_gen_batch_live.py -v -s

Skipped by default (and on CI), so the normal suite stays fast and hermetic.
Uses sdxl-turbo at 1 step / 512px — seconds per image, not minutes.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

LIVE = os.getenv("MATRX_LIVE_GEN") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="Live generation test — set MATRX_LIVE_GEN=1 to run (loads weights, uses the GPU).",
)

MODEL_ID = "stabilityai/sdxl-turbo"
POLL_TIMEOUT_S = 600.0


def _downloaded(http: httpx.Client, model_id: str) -> bool:
    models = http.get("/image-gen/models").json()
    rows = models["models"] if isinstance(models, dict) else models
    return any(m["model_id"] == model_id and m["is_downloaded"] for m in rows)


def _await_batch(http: httpx.Client, batch_id: str) -> dict:
    """Poll until the batch stops having pending work."""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    last: dict = {}
    while time.monotonic() < deadline:
        batches = http.get("/image-gen/batches").json()
        found = next((b for b in batches if b["batch_id"] == batch_id), None)
        assert found is not None, "the batch vanished from /image-gen/batches"
        last = found
        if found["finished"]:
            return found
        time.sleep(1.0)
    pytest.fail(f"batch never finished within {POLL_TIMEOUT_S}s: {last}")


def test_a_real_prompt_matrix_batch_generates_real_images(http: httpx.Client) -> None:
    """The whole feature, for real: 4 runs of a 2-variable matrix, queued as one
    batch, drained by the engine, producing 4 distinct images in the library."""
    if not _downloaded(http, MODEL_ID):
        pytest.skip(f"{MODEL_ID} is not downloaded on this machine")

    # The matrix: {{subject}} × {{style}} = 2 × 2 = 4 runs, fixed seed, so the
    # ONLY difference between the images is the variable — exactly what the UI
    # builds and posts.
    subjects = ["cat", "owl"]
    styles = ["film noir", "watercolor"]
    jobs = [
        {
            "prompt": f"a {subject}, {style} style",
            "model_id": MODEL_ID,
            "steps": 1,          # sdxl-turbo is a 1-step model
            "guidance": 0.0,
            "width": 512,
            "height": 512,
            "seed": 4242,        # fixed across the sweep, on purpose
            "variables": {"subject": subject, "style": style},
            "combo_label": f"subject={subject} · style={style}",
        }
        for subject in subjects
        for style in styles
    ]

    r = http.post(
        "/image-gen/jobs/batch", json={"label": "Live sweep", "jobs": jobs}
    )
    assert r.status_code == 202, r.text
    body = r.json()
    batch_id = body["batch_id"]
    assert body["count"] == 4

    summary = _await_batch(http, batch_id)
    assert summary["completed"] == 4, f"not every run produced an image: {summary}"
    assert summary["failed"] == 0

    # Every job carries its variables, its combination label, the concrete seed,
    # and a real media-library item that actually resolves to bytes.
    all_jobs = http.get("/image-gen/jobs?limit=200").json()
    mine = sorted(
        (j for j in all_jobs if j["batch_id"] == batch_id),
        key=lambda j: j["batch_index"],
    )
    assert len(mine) == 4

    item_ids: list[str] = []
    for i, job in enumerate(mine):
        assert job["status"] == "completed", job
        assert job["batch_label"] == "Live sweep"
        assert job["batch_index"] == i and job["batch_size"] == 4
        assert set(job["variables"]) == {"subject", "style"}
        assert job["combo_label"].startswith("subject=")
        assert job["seed"] == 4242, "the fixed seed must be what actually ran"
        assert job["item_id"], "a completed job must carry its library item"

        # The image is really there.
        img = http.get(f"/media-library/file/{job['item_id']}")
        assert img.status_code == 200, img.text
        assert len(img.content) > 1000, "that is not a real PNG"
        item_ids.append(job["item_id"])

    assert len(set(item_ids)) == 4, "four DISTINCT images, not one reused"

    print(f"\n✓ Live batch {batch_id[:8]}: 4/4 images generated")
    for job in mine:
        print(f"    {job['combo_label']:<42} → {job['item_id']}")


def test_queue_control_works_against_the_live_engine(http: httpx.Client) -> None:
    """Pause / reorder / cancel-batch, exercised on the real engine — the
    controls a user reaches for mid-run."""
    if not _downloaded(http, MODEL_ID):
        pytest.skip(f"{MODEL_ID} is not downloaded on this machine")

    # Pause FIRST so nothing starts running and the test stays deterministic
    # (and cheap — no GPU time is spent).
    assert http.post("/image-gen/queue/pause").json()["paused"] is True

    try:
        jobs = [
            {
                "prompt": f"a test subject {i}",
                "model_id": MODEL_ID,
                "steps": 1,
                "width": 512,
                "height": 512,
                "variables": {"n": str(i)},
                "combo_label": f"n={i}",
            }
            for i in range(5)
        ]
        r = http.post(
            "/image-gen/jobs/batch", json={"label": "Control test", "jobs": jobs}
        )
        assert r.status_code == 202, r.text
        batch_id = r.json()["batch_id"]
        job_ids = r.json()["job_ids"]

        # Nothing ran: the queue is paused.
        state = http.get("/image-gen/queue").json()
        assert state["paused"] is True
        assert state["running"] == 0
        assert state["queued"] >= 5

        # Reorder: last run first.
        reordered = http.post(
            "/image-gen/queue/reorder",
            json={"job_ids": [job_ids[4], *job_ids[:4]]},
        )
        assert reordered.status_code == 200, reordered.text
        pending = [j["job_id"] for j in reordered.json() if j["status"] == "queued"]
        assert pending[0] == job_ids[4], "the dragged job is now next"

        # Cancel the batch: nothing of it is left pending.
        cancelled = http.delete(f"/image-gen/batches/{batch_id}")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancelled"] == 5

        summary = next(
            b for b in http.get("/image-gen/batches").json()
            if b["batch_id"] == batch_id
        )
        assert summary["cancelled"] == 5 and summary["finished"] is True
        print(f"\n✓ Live queue control: pause → reorder → cancel-batch all OK")
    finally:
        # Never leave the user's queue paused.
        http.post("/image-gen/queue/resume")
