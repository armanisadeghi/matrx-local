"""Kokoro TTS voice catalog.

Curated list of all 54 built-in voices from Kokoro v1.0, organised by
language.  Each entry is self-describing so the router and UI can derive
all display / filtering / playback information without hardcoded strings.

Voice quality grades are sourced from the official VOICES.md on HuggingFace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TtsVoice:
    """A single TTS voice entry."""

    voice_id: str
    """Kokoro voice identifier (e.g. 'af_heart')."""

    name: str
    """Human-readable display name."""

    gender: Literal["female", "male"]

    language: str
    """Full language label (e.g. 'American English')."""

    lang_code: str
    """Kokoro lang_code used in KPipeline / kokoro-onnx (e.g. 'a')."""

    quality_grade: str
    """Official quality grade from VOICES.md (e.g. 'A', 'B-', 'C+')."""

    traits: list[str] = field(default_factory=list)
    """Optional UI-facing tags (e.g. ['flagship', 'warm'])."""

    is_custom: bool = False
    is_default: bool = False


# ── Language metadata ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TtsLanguage:
    lang_code: str
    name: str
    flag: str
    espeak_fallback: str


LANGUAGES: list[TtsLanguage] = [
    TtsLanguage(lang_code="a", name="American English", flag="us", espeak_fallback="en-us"),
    TtsLanguage(lang_code="b", name="British English", flag="gb", espeak_fallback="en-gb"),
    TtsLanguage(lang_code="j", name="Japanese", flag="jp", espeak_fallback="ja"),
    TtsLanguage(lang_code="z", name="Mandarin Chinese", flag="cn", espeak_fallback="cmn"),
    TtsLanguage(lang_code="e", name="Spanish", flag="es", espeak_fallback="es"),
    TtsLanguage(lang_code="f", name="French", flag="fr", espeak_fallback="fr-fr"),
    TtsLanguage(lang_code="h", name="Hindi", flag="in", espeak_fallback="hi"),
    TtsLanguage(lang_code="i", name="Italian", flag="it", espeak_fallback="it"),
    TtsLanguage(lang_code="p", name="Brazilian Portuguese", flag="br", espeak_fallback="pt-br"),
]

LANGUAGE_MAP: dict[str, TtsLanguage] = {lang.lang_code: lang for lang in LANGUAGES}


# ── Voice catalog ─────────────────────────────────────────────────────────────

BUILTIN_VOICES: list[TtsVoice] = [
    # ── American English (lang_code='a') ──────────────────────────────────────
    TtsVoice(voice_id="af_heart",   name="Heart",   gender="female", language="American English", lang_code="a", quality_grade="A",  traits=["flagship"], is_default=True),
    TtsVoice(voice_id="af_bella",   name="Bella",   gender="female", language="American English", lang_code="a", quality_grade="A-", traits=["warm"]),
    TtsVoice(voice_id="af_nicole",  name="Nicole",  gender="female", language="American English", lang_code="a", quality_grade="B-"),
    TtsVoice(voice_id="af_aoede",   name="Aoede",   gender="female", language="American English", lang_code="a", quality_grade="C+"),
    TtsVoice(voice_id="af_kore",    name="Kore",    gender="female", language="American English", lang_code="a", quality_grade="C+"),
    TtsVoice(voice_id="af_sarah",   name="Sarah",   gender="female", language="American English", lang_code="a", quality_grade="C+"),
    TtsVoice(voice_id="af_alloy",   name="Alloy",   gender="female", language="American English", lang_code="a", quality_grade="C"),
    TtsVoice(voice_id="af_nova",    name="Nova",    gender="female", language="American English", lang_code="a", quality_grade="C"),
    TtsVoice(voice_id="af_jessica", name="Jessica", gender="female", language="American English", lang_code="a", quality_grade="D"),
    TtsVoice(voice_id="af_river",   name="River",   gender="female", language="American English", lang_code="a", quality_grade="D"),
    TtsVoice(voice_id="af_sky",     name="Sky",     gender="female", language="American English", lang_code="a", quality_grade="C-"),
    TtsVoice(voice_id="am_fenrir",  name="Fenrir",  gender="male",   language="American English", lang_code="a", quality_grade="C+"),
    TtsVoice(voice_id="am_michael", name="Michael", gender="male",   language="American English", lang_code="a", quality_grade="C+"),
    TtsVoice(voice_id="am_puck",    name="Puck",    gender="male",   language="American English", lang_code="a", quality_grade="C+"),
    TtsVoice(voice_id="am_adam",    name="Adam",    gender="male",   language="American English", lang_code="a", quality_grade="F+"),
    TtsVoice(voice_id="am_echo",    name="Echo",    gender="male",   language="American English", lang_code="a", quality_grade="D"),
    TtsVoice(voice_id="am_eric",    name="Eric",    gender="male",   language="American English", lang_code="a", quality_grade="D"),
    TtsVoice(voice_id="am_liam",    name="Liam",    gender="male",   language="American English", lang_code="a", quality_grade="D"),
    TtsVoice(voice_id="am_onyx",    name="Onyx",    gender="male",   language="American English", lang_code="a", quality_grade="D"),
    TtsVoice(voice_id="am_santa",   name="Santa",   gender="male",   language="American English", lang_code="a", quality_grade="D-"),

    # ── British English (lang_code='b') ───────────────────────────────────────
    TtsVoice(voice_id="bf_alice",    name="Alice",    gender="female", language="British English", lang_code="b", quality_grade="D"),
    TtsVoice(voice_id="bf_emma",     name="Emma",     gender="female", language="British English", lang_code="b", quality_grade="B-"),
    TtsVoice(voice_id="bf_isabella", name="Isabella", gender="female", language="British English", lang_code="b", quality_grade="C"),
    TtsVoice(voice_id="bf_lily",     name="Lily",     gender="female", language="British English", lang_code="b", quality_grade="D"),
    TtsVoice(voice_id="bm_daniel",   name="Daniel",   gender="male",   language="British English", lang_code="b", quality_grade="D"),
    TtsVoice(voice_id="bm_fable",    name="Fable",    gender="male",   language="British English", lang_code="b", quality_grade="C"),
    TtsVoice(voice_id="bm_george",   name="George",   gender="male",   language="British English", lang_code="b", quality_grade="C"),
    TtsVoice(voice_id="bm_lewis",    name="Lewis",    gender="male",   language="British English", lang_code="b", quality_grade="D+"),

    # ── Japanese (lang_code='j') ──────────────────────────────────────────────
    TtsVoice(voice_id="jf_alpha",      name="Alpha",      gender="female", language="Japanese", lang_code="j", quality_grade="C+"),
    TtsVoice(voice_id="jf_gongitsune", name="Gongitsune", gender="female", language="Japanese", lang_code="j", quality_grade="C"),
    TtsVoice(voice_id="jf_nezumi",     name="Nezumi",     gender="female", language="Japanese", lang_code="j", quality_grade="C-"),
    TtsVoice(voice_id="jf_tebukuro",   name="Tebukuro",   gender="female", language="Japanese", lang_code="j", quality_grade="C"),
    TtsVoice(voice_id="jm_kumo",       name="Kumo",       gender="male",   language="Japanese", lang_code="j", quality_grade="C-"),

    # ── Mandarin Chinese (lang_code='z') ──────────────────────────────────────
    TtsVoice(voice_id="zf_xiaobei",  name="Xiaobei",  gender="female", language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zf_xiaoni",   name="Xiaoni",   gender="female", language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zf_xiaoxiao", name="Xiaoxiao", gender="female", language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zf_xiaoyi",   name="Xiaoyi",   gender="female", language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zm_yunjian",  name="Yunjian",  gender="male",   language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zm_yunxi",    name="Yunxi",    gender="male",   language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zm_yunxia",   name="Yunxia",   gender="male",   language="Mandarin Chinese", lang_code="z", quality_grade="D"),
    TtsVoice(voice_id="zm_yunyang",  name="Yunyang",  gender="male",   language="Mandarin Chinese", lang_code="z", quality_grade="D"),

    # ── Spanish (lang_code='e') ───────────────────────────────────────────────
    TtsVoice(voice_id="ef_dora",  name="Dora",  gender="female", language="Spanish", lang_code="e", quality_grade="C"),
    TtsVoice(voice_id="em_alex",  name="Alex",  gender="male",   language="Spanish", lang_code="e", quality_grade="C"),
    TtsVoice(voice_id="em_santa", name="Santa", gender="male",   language="Spanish", lang_code="e", quality_grade="C"),

    # ── French (lang_code='f') ────────────────────────────────────────────────
    TtsVoice(voice_id="ff_siwis", name="Siwis", gender="female", language="French", lang_code="f", quality_grade="B-"),

    # ── Hindi (lang_code='h') ─────────────────────────────────────────────────
    TtsVoice(voice_id="hf_alpha", name="Alpha", gender="female", language="Hindi", lang_code="h", quality_grade="C"),
    TtsVoice(voice_id="hf_beta",  name="Beta",  gender="female", language="Hindi", lang_code="h", quality_grade="C"),
    TtsVoice(voice_id="hm_omega", name="Omega", gender="male",   language="Hindi", lang_code="h", quality_grade="C"),
    TtsVoice(voice_id="hm_psi",   name="Psi",   gender="male",   language="Hindi", lang_code="h", quality_grade="C"),

    # ── Italian (lang_code='i') ───────────────────────────────────────────────
    TtsVoice(voice_id="if_sara",   name="Sara",   gender="female", language="Italian", lang_code="i", quality_grade="C"),
    TtsVoice(voice_id="im_nicola", name="Nicola", gender="male",   language="Italian", lang_code="i", quality_grade="C"),

    # ── Brazilian Portuguese (lang_code='p') ──────────────────────────────────
    TtsVoice(voice_id="pf_dora",  name="Dora",  gender="female", language="Brazilian Portuguese", lang_code="p", quality_grade="C"),
    TtsVoice(voice_id="pm_alex",  name="Alex",  gender="male",   language="Brazilian Portuguese", lang_code="p", quality_grade="C"),
    TtsVoice(voice_id="pm_santa", name="Santa", gender="male",   language="Brazilian Portuguese", lang_code="p", quality_grade="C"),
]

VOICE_MAP: dict[str, TtsVoice] = {v.voice_id: v for v in BUILTIN_VOICES}

DEFAULT_VOICE_ID = "af_heart"

# ── Model file metadata ───────────────────────────────────────────────────────
# URLs, exact byte sizes, and SHA-256 hashes for the kokoro-onnx v1.0 release.
# Both files are byte-for-byte stable on the GitHub releases page; we verify
# both size AND hash after download to catch silent corruption.

ONNX_MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_BIN_FILENAME = "voices-v1.0.bin"

ONNX_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
VOICES_BIN_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)

ONNX_MODEL_SIZE_BYTES = 325_532_387  # ~310 MB
VOICES_BIN_SIZE_BYTES = 28_214_398   # ~27 MB

# SHA-256 of the published release artifacts. Verified locally against the
# upstream files. If kokoro-onnx ever ships a v1.0 patch we'll need to update.
ONNX_MODEL_SHA256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
VOICES_BIN_SHA256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"

SAMPLE_RATE = 24_000

# ── Streaming protocol v2 ─────────────────────────────────────────────────────
# Wire format: each frame is `1 byte tag · 4 bytes BE uint32 length · N payload`.
# Type tags:
#   0x01  CHUNK  — payload is a complete self-contained WAV blob (one phrase)
#   0x02  END    — payload is empty (length=0); marks clean stream completion
#   0xFF  ERROR  — payload is UTF-8 JSON {"code": "...", "message": "..."}
# The protocol version is advertised via the X-TTS-Stream-Protocol response
# header so older clients can detect a mismatch and fail loudly.
STREAM_PROTOCOL_VERSION = 2
STREAM_TAG_CHUNK = 0x01
STREAM_TAG_END = 0x02
STREAM_TAG_ERROR = 0xFF

# Hybrid chunker threshold: text shorter than this goes through a single
# kokoro.create() call; longer text uses kokoro.create_stream() for early
# playback. ~280 chars is roughly two short sentences.
STREAM_THRESHOLD_CHARS = 280

# Per-chunk watchdog inside the streaming wrapper. If a phoneme batch takes
# longer than this we abort the stream with an error frame.
STREAM_CHUNK_TIMEOUT_SECONDS = 15.0

# Cap on a single uploaded voice embedding (5 MB; real embeddings are ~520 KB).
MAX_VOICE_IMPORT_BYTES = 5 * 1024 * 1024

# Exact embedding shape Kokoro expects. Validated on import.
EMBEDDING_SHAPE = (510, 1, 256)


# ─────────────────────────────────────────────────────────────────────────────
# Remote-catalog accessors — the canonical read path
#
# The live catalogs are the remote `catalog_entries` table (kinds `tts_voice`
# / `tts_language` / `tts_model_file`) served through app/services/catalogs.
# The compiled lists/constants above stay only as the explicit defaults tier
# (first boot / total network failure) — adapted into CatalogEntry form by
# app/services/catalogs/compiled.py. Read through these accessors, never the
# lists directly.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TtsModelFile:
    """One downloadable Kokoro runtime artifact (URL + integrity metadata)."""

    filename: str
    url: str
    size_bytes: int
    sha256: str
    role: str  # "onnx_model" | "voices_bin"
    sample_rate: int = SAMPLE_RATE


_COMPILED_MODEL_FILES: tuple[TtsModelFile, ...] = (
    TtsModelFile(
        filename=ONNX_MODEL_FILENAME,
        url=ONNX_MODEL_URL,
        size_bytes=ONNX_MODEL_SIZE_BYTES,
        sha256=ONNX_MODEL_SHA256,
        role="onnx_model",
    ),
    TtsModelFile(
        filename=VOICES_BIN_FILENAME,
        url=VOICES_BIN_URL,
        size_bytes=VOICES_BIN_SIZE_BYTES,
        sha256=VOICES_BIN_SHA256,
        role="voices_bin",
    ),
)


def get_builtin_voices() -> list[TtsVoice]:
    """Resolved built-in voice catalog (remote > cache > compiled fallback)."""
    from app.services.catalogs import get_catalog  # noqa: PLC0415 — lazy: avoids import cycle
    from app.services.catalogs.adapt import entries_to_dataclasses  # noqa: PLC0415

    return entries_to_dataclasses(get_catalog("tts_voice"), TtsVoice)


def get_voice_map() -> dict[str, TtsVoice]:
    """voice_id → TtsVoice over the resolved catalog."""
    return {v.voice_id: v for v in get_builtin_voices()}


def get_default_voice_id() -> str:
    """The catalog's flagged default voice (is_default)."""
    from app.common.system_logger import get_logger  # noqa: PLC0415

    for v in get_builtin_voices():
        if v.is_default:
            return v.voice_id
    get_logger().warning(
        "[tts] no catalog voice flagged is_default — falling back to the "
        "compiled DEFAULT_VOICE_ID (%s)",
        DEFAULT_VOICE_ID,
    )
    return DEFAULT_VOICE_ID


def get_languages() -> list[TtsLanguage]:
    """Resolved language catalog (remote > cache > compiled fallback)."""
    from app.services.catalogs import get_catalog  # noqa: PLC0415
    from app.services.catalogs.adapt import entries_to_dataclasses  # noqa: PLC0415

    return entries_to_dataclasses(get_catalog("tts_language"), TtsLanguage)


def get_language_map() -> dict[str, TtsLanguage]:
    """lang_code → TtsLanguage over the resolved catalog."""
    return {lang.lang_code: lang for lang in get_languages()}


def get_tts_model_files() -> list[TtsModelFile]:
    """Resolved Kokoro runtime artifacts (URL + size + SHA-256 per file).

    LOUD RECOVERY: the engine's loader contractually needs BOTH compiled
    filenames on disk — if the resolved catalog is missing one (a bad remote
    edit), the compiled spec for the missing file is re-added with an error
    log so TTS setup can never be broken by a catalog row deletion.
    """
    from app.common.system_logger import get_logger  # noqa: PLC0415
    from app.services.catalogs import get_catalog  # noqa: PLC0415
    from app.services.catalogs.adapt import entries_to_dataclasses  # noqa: PLC0415

    files = entries_to_dataclasses(get_catalog("tts_model_file"), TtsModelFile)
    present = {f.filename for f in files}
    for compiled in _COMPILED_MODEL_FILES:
        if compiled.filename not in present:
            get_logger().error(
                "[tts] RECOVERY: resolved tts_model_file catalog is missing the "
                "required artifact %s — re-adding the compiled spec (fix the "
                "catalog row!)",
                compiled.filename,
            )
            files.append(compiled)
    return files


def get_tts_model_file(filename: str) -> TtsModelFile:
    """Resolved spec for one required artifact (always answers — see above)."""
    for f in get_tts_model_files():
        if f.filename == filename:
            return f
    # Unreachable for the two compiled filenames (recovery above); loud for
    # anything else.
    raise KeyError(f"unknown TTS model file: {filename!r}")
