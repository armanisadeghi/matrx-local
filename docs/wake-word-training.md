# Wake Word — Training "Hey Matrix" (openWakeWord)

> **Operators / Arman** — End users currently ship with stock openWakeWord
> models (`hey_jarvis`, `alexa` are bundled; see `app/services/wake_word/models.py`
> `BUNDLED_MODELS`). There is **no custom `hey_matrix.onnx` yet** — it has never
> been trained. This doc is how to produce one.
>
> **⚠️ Corrected 2026-07-14.** An earlier version of this doc described a
> `python -m openwakeword.train generate_samples/download_background_data/train/
> evaluate` CLI. **That CLI does not exist** in openWakeWord and never did. The
> real training interface is a single YAML-config-driven run, and it depends on
> several external repos/datasets. The accurate process is below.

## Reality check — what training actually requires

Verified against the installed `openwakeword.train` (Python 3.13 venv). The real
entrypoint is:

```bash
python -m openwakeword.train --training_config hey_matrix.yaml \
  --generate_clips --augment_clips --train_model
```

…and the YAML config must supply all of these inputs (from `train.py`'s
`config[...]` reads):

| Config key | What it is | Where to get it |
|---|---|---|
| `target_phrase` | e.g. `"hey matrix"` (must be dictionary-pronounceable — "matrx" won't synthesize) | you pick |
| `piper_sample_generator_path` | clone of **piper-sample-generator** + a piper TTS model (~63 MB) — generates synthetic positive clips | `github.com/rhasspy/piper-sample-generator` |
| `feature_data_files` | precomputed openWakeWord **negative** features (`.npy`) | HF `davidscripka/openwakeword_features` — multi-GB |
| `background_paths` | background/negative audio for augmentation | AudioSet / FMA subsets |
| `rir_paths` | room-impulse-response wavs for reverb augmentation | MIT RIR survey |
| `false_positive_validation_data_path` | held-out FP validation set | openWakeWord docs |
| `n_samples` / `n_samples_val` / `steps` / `model_name` / `output_dir` | training hyperparams | you pick |

So this is the full openWakeWord "automatic model training" pipeline — a
multi-GB, multi-repo setup, not a quick local run. CPU training is slow; a GPU
is strongly preferred.

## Recommended path: openWakeWord's official Colab notebook

Because the datasets and piper-sample-generator are already wired in it, the
**official Google Colab "training a custom model" notebook** is the fastest way
to get `hey_matrix.onnx` — free GPU, preassembled datasets, ~1 hr end to end.
Search the openWakeWord repo (`dscripka/openWakeWord`) `notebooks/` →
`automatic_model_training.ipynb`. Set `target_phrase="hey matrix"`, run all,
download the resulting `.onnx`.

## Local path (if we want it fully in-house)

1. **Env (Python 3.13 works, but the `[train]` extra is incomplete):**
   ```bash
   python3.13 -m venv ~/wakeword-train && source ~/wakeword-train/bin/activate
   pip install "openwakeword[train]"
   # the extra OMITS these — install explicitly:
   pip install torch torchaudio torchinfo torchmetrics "scipy<1.15" \
       pronouncing speechbrain acoustics audiomentations mutagen \
       torch_audiomentations pyyaml tqdm requests
   # NOTE: scipy MUST be <1.15 — `acoustics` imports scipy.special.sph_harm,
   # removed in scipy 1.15. (This env is already set up on Arman's Mac.)
   ```
2. Clone `piper-sample-generator`, download a piper voice model.
3. Download the feature/background/RIR datasets (multi-GB) referenced above.
4. Write `hey_matrix.yaml` with all keys from the table.
5. Run the `--generate_clips --augment_clips --train_model` command above.
6. Output: `hey_matrix.onnx` (~3 MB) in the config's `output_dir`.

## Delivery — ship via CDN, not the installer

Once `hey_matrix.onnx` exists, **do not bundle it in the installer.** Per
[CDN_ASSETS_PLAN.md](CDN_ASSETS_PLAN.md), upload it to `matrx-local/wake-word/`
and have `app/services/wake_word/models.py` download it on first voice use
(tiny 3 MB asset, voice is opt-in). Wire `models.py` to prefer: CDN download →
bundled fallback → `~/.matrx/oww_models/` (for a locally-trained copy). Pick
detection threshold `0.5` (balanced); `0.3–0.4` sensitive, `0.7–0.8` strict.
