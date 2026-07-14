"""On-disk persistence for the desktop prompt-matrix library + templates."""

from app.services.prompt_matrix.store import (
    PromptMatrixStore,
    get_prompt_matrix_store,
)

__all__ = ["PromptMatrixStore", "get_prompt_matrix_store"]
