"""On-device wake word detection service using openWakeWord (ONNX backend).

This package provides a background detection service that streams microphone
audio through the openWakeWord ONNX model and fires events when a configured
keyword is detected.  It is the Python-sidecar counterpart to the Rust
whisper-tiny wake word engine — both can be installed simultaneously and the
user selects which engine to use via Settings.
"""

from .service import WakeWordService, get_wake_word_service
from .models import (
    OWWModelInfo,
    list_available_models,
    download_model,
    model_exists,
    oww_models_dir,
    get_bundled_models,
    get_pretrained_registry,
)

__all__ = [
    "WakeWordService",
    "get_wake_word_service",
    "OWWModelInfo",
    "list_available_models",
    "download_model",
    "model_exists",
    "oww_models_dir",
    "get_bundled_models",
    "get_pretrained_registry",
]
