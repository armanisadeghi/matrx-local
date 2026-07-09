"""Shared primitives for local media generation (image + video).

Both `app.services.image_gen` and `app.services.video_gen` build on these:

- ``paths``     — where model weights and generated outputs live on disk, plus
                  the "download complete" marker convention shared with the
                  universal DownloadManager.
- ``hardware``  — a single source of truth for the compute device
                  (mps/cuda/cpu) and RAM/VRAM budget, derived from the existing
                  hardware detector. Used to gate which models a machine may run.

Nothing here imports torch/diffusers — these modules are always importable even
when the optional image-gen packages are absent.
"""
