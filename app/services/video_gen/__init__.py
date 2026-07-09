"""Local video generation (diffusers Wan / LTX pipelines).

Structure mirrors image_gen:

- ``models.py``  — curated model catalog (Wan 2.2 TI2V-5B default).
- ``jobs.py``    — async job records: one active generation at a time, history
                   persisted to ~/.matrx/generated/videos/jobs.json.
- ``service.py`` — pipeline load/unload/generate with lazy torch imports.

Packages come from the SAME shared install as image generation
(~/.matrx/image-gen-packages/) — there is no separate video installer.
"""
