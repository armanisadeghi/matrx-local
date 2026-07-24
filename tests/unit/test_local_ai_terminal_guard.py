from types import SimpleNamespace

import pytest

from app.services.ai.local_ai_task import _enforce_visible_terminal_output
from matrx_ai.config import ThinkingContent, UnifiedConfig, UnifiedMessage, UnifiedResponse


@pytest.mark.anyio
async def test_reasoning_only_success_is_converted_to_failed_turn() -> None:
    assistant = UnifiedMessage(
        role="assistant",
        content=[
            ThinkingContent(
                text="<tool_call><function=web>{}</function></tool_call>"
            )
        ],
    )
    config = UnifiedConfig.from_dict(
        {
            "model": "local/test",
            "messages": [
                {"role": "user", "content": "help"},
                assistant.to_storage_dict(),
            ],
        }
    )
    completed = SimpleNamespace(
        metadata={},
        request=SimpleNamespace(config=config),
        final_response=UnifiedResponse(messages=[assistant], finish_reason="stop"),
    )

    await _enforce_visible_terminal_output(
        completed,
        SimpleNamespace(store=False, conversation_id="conversation-1"),
    )

    assert completed.metadata["status"] == "failed"
    assert completed.metadata["error_type"] == "unparsed_tool_call"
    assert config.messages.get_last_by_role("assistant").status == "failed"


@pytest.mark.anyio
async def test_visible_answer_remains_successful() -> None:
    config = UnifiedConfig.from_dict(
        {
            "model": "local/test",
            "messages": [
                {"role": "user", "content": "help"},
                {"role": "assistant", "content": "Here is the answer."},
            ],
        }
    )
    completed = SimpleNamespace(
        metadata={},
        request=SimpleNamespace(config=config),
        final_response=UnifiedResponse(messages=[]),
    )

    await _enforce_visible_terminal_output(
        completed,
        SimpleNamespace(store=False, conversation_id="conversation-1"),
    )

    assert completed.metadata == {}
