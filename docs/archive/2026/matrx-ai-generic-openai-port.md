> ARCHIVED 2026-08-20 — moved from `docs/`. `GenericOpenAIChat` exists live in aidream
> (`packages/matrx-ai/matrx_ai/providers/generic_openai/`) — the port this doc requested is done.
> Doc-migration board row 33.

# matrx-ai: Port `GenericOpenAIChat` Provider

**Status:** Required for local LLM agent routing in `matrx-local`  
**Target version:** `matrx-ai >= 0.1.23`  
**Source of truth:** `aidream/ai/providers/generic_openai/`  
**Requested by:** `matrx-local` — see `app/services/ai/local_llm_registry.py`

---

## Background

`matrx-local` runs a local `llama-server` (llama.cpp) sidecar that exposes an OpenAI-compatible
`/v1/chat/completions` endpoint on `127.0.0.1:{port}`. To route agents and conversations through
this local model, `matrx-ai` needs a generic OpenAI-compatible provider class.

The implementation already exists in `aidream/ai/providers/generic_openai/`. This document describes
exactly what to copy and adapt for `matrx-ai`.

---

## Files to Create

### 1. `matrx_ai/providers/generic_openai/__init__.py`

```python
from matrx_ai.providers.generic_openai.generic_openai_api import GenericOpenAIChat
from matrx_ai.providers.generic_openai.translator import GenericOpenAITranslator

__all__ = ["GenericOpenAIChat", "GenericOpenAITranslator"]
```

### 2. `matrx_ai/providers/generic_openai/generic_openai_api.py`

Copy `aidream/ai/providers/generic_openai/generic_openai_api.py` with these import substitutions:

| aidream import | matrx-ai replacement |
|---|---|
| `from ai.config import ...` | `from matrx_ai.config import ...` |
| `from aidream.api.emitter_protocol import Emitter` | `from matrx_ai.context.emitter_protocol import Emitter` |
| `from aidream.api.middleware.context import get_app_context` | `from matrx_ai.context.app_context import get_app_context` |
| `from ai.providers.errors import classify_provider_error` | `from matrx_ai.providers.errors import classify_provider_error` |
| `from .translator import GenericOpenAITranslator` | unchanged |

`classify_provider_error` already exists in `matrx_ai/providers/errors.py` line 154. No changes needed there.

**Full adapted file:**

```python
from __future__ import annotations

import asyncio
import json
import os
import traceback
from typing import Any

from matrx_utils import vcprint
from openai import AsyncOpenAI

from matrx_ai.config import (
    FinishReason,
    TextContent,
    TokenUsage,
    ToolCallContent,
    UnifiedConfig,
    UnifiedMessage,
    UnifiedResponse,
)
from matrx_ai.context.emitter_protocol import Emitter

from .translator import GenericOpenAITranslator

DEBUG_OVERRIDE = False


class GenericOpenAIChat:
    """
    Generic OpenAI-compatible endpoint implementation.

    Works with any provider that exposes an OpenAI-compatible chat completions API:
    - llama-server (llama.cpp) — used by matrx-local for local LLM inference
    - HuggingFace Inference Endpoints (TGI)
    - vLLM
    - LocalAI
    - Ollama
    - Any other OpenAI-compatible server

    Usage:
        client = GenericOpenAIChat(
            base_url="http://127.0.0.1:11434",
            api_key_env="",
            provider_name="local_llama",
            api_key="none",
        )
    """

    client: AsyncOpenAI
    endpoint_name: str
    provider_name: str
    debug: bool

    def __init__(
        self,
        base_url: str,
        api_key_env: str = "",
        provider_name: str = "generic_openai",
        api_key: str | None = None,
        debug: bool = False,
    ):
        resolved_key = api_key or (os.environ.get(api_key_env, "") if api_key_env else "none")
        self.client = AsyncOpenAI(
            api_key=resolved_key or "none",
            base_url=base_url.rstrip("/") + "/v1",
        )
        self.provider_name = provider_name
        self.endpoint_name = f"[{provider_name.upper()} CHAT]"
        self.translator = GenericOpenAITranslator(debug=debug)
        self.debug = debug

        if DEBUG_OVERRIDE:
            self.debug = True

    def to_provider_config(self, config: UnifiedConfig, api_class: str) -> dict[str, Any]:
        return self.translator.to_generic_openai(config, self.provider_name)

    def to_unified_response(self, response: Any, model: str = "") -> UnifiedResponse:
        return self.translator.from_generic_openai(response, self.provider_name)

    async def execute(
        self,
        unified_config: UnifiedConfig,
        api_class: str,
        debug: bool = False,
    ) -> UnifiedResponse:
        from matrx_ai.context.app_context import get_app_context

        emitter = get_app_context().emitter
        self.debug = debug
        if DEBUG_OVERRIDE:
            self.debug = True
        self.translator.debug = debug

        config_data = self.to_provider_config(unified_config, api_class)

        vcprint(config_data, f"{self.endpoint_name} Config Data", color="blue", verbose=debug)

        try:
            if config_data.get("stream", False):
                return await self._execute_streaming(config_data, emitter, unified_config.model)
            else:
                return await self._execute_non_streaming(config_data, emitter, unified_config.model)

        except Exception as e:
            from matrx_ai.providers.errors import classify_provider_error

            error_info = classify_provider_error(self.provider_name, e)

            await emitter.send_error(
                error_type=error_info.error_type,
                message=error_info.message,
                user_message=error_info.user_message,
            )
            vcprint(e, f"{self.endpoint_name} Error", color="red")
            traceback.print_exc()

            e.error_info = error_info
            raise

    async def _execute_non_streaming(
        self,
        config_data: dict[str, Any],
        emitter: Emitter,
        model: str,
    ) -> UnifiedResponse:
        vcprint(f"{self.endpoint_name} Starting API call (non-streaming)...", color="cyan")

        response = await self.client.chat.completions.create(**config_data)

        vcprint(f"{self.endpoint_name} API call completed, processing response...", color="cyan")
        vcprint(response, f"{self.endpoint_name} Response", color="green", verbose=self.debug)

        converted_response = self.to_unified_response(response, model)
        vcprint(
            f"{self.endpoint_name} Conversion complete. {len(converted_response.messages)} messages",
            color="cyan",
        )

        for message in converted_response.messages:
            for content in message.content:
                if isinstance(content, TextContent):
                    await emitter.send_chunk(content.text)
                elif isinstance(content, ToolCallContent):
                    await emitter.send_status_update(
                        status="processing",
                        system_message=f"Executing {content.name}",
                        user_message=f"Using tool {content.name}",
                        metadata={"tool_call": content.name},
                    )

        vcprint(f"{self.endpoint_name} Non-streaming execution completed successfully", color="green")
        return converted_response

    async def _execute_streaming(
        self,
        config_data: dict[str, Any],
        emitter: Emitter,
        model: str,
    ) -> UnifiedResponse:
        vcprint(f"{self.endpoint_name} Starting API call (streaming)...", color="cyan")

        stream = await self.client.chat.completions.create(**config_data)

        vcprint(f"{self.endpoint_name} Stream connection established, processing chunks...", color="cyan")

        accumulated_content = ""
        accumulated_reasoning = ""
        accumulated_tool_calls: list[dict[str, str]] = []
        usage_data = None
        finish_reason = None
        response_id = None
        in_think_block = False

        async for chunk in stream:
            response_id = chunk.id

            if chunk.usage:
                usage_data = chunk.usage

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # llama.cpp / Qwen3-thinking streams reasoning in delta.reasoning_content
            reasoning_chunk = getattr(delta, "reasoning_content", None)
            if reasoning_chunk:
                accumulated_reasoning += reasoning_chunk
                if not in_think_block:
                    await emitter.send_chunk("<reasoning>")
                    in_think_block = True
                await emitter.send_chunk(reasoning_chunk)
                await asyncio.sleep(0)

            if delta.content:
                # Close reasoning block before first real content token
                if in_think_block:
                    await emitter.send_chunk("\n</reasoning>\n")
                    in_think_block = False
                accumulated_content += delta.content
                await emitter.send_chunk(delta.content)
                await asyncio.sleep(0)

            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    tc_index_raw = (
                        tc.get("index") if isinstance(tc, dict) else getattr(tc, "index", None)
                    )
                    tc_index = int(tc_index_raw) if tc_index_raw is not None else None
                    if tc_index is None:
                        continue

                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    tc_func = (
                        tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                    )

                    while len(accumulated_tool_calls) <= tc_index:
                        accumulated_tool_calls.append({"id": "", "name": "", "arguments": ""})

                    if tc_id:
                        accumulated_tool_calls[tc_index]["id"] = tc_id

                    if tc_func:
                        func_name = (
                            tc_func.get("name") if isinstance(tc_func, dict) else getattr(tc_func, "name", None)
                        )
                        func_args = (
                            tc_func.get("arguments") if isinstance(tc_func, dict) else getattr(tc_func, "arguments", None)
                        )
                        if func_name:
                            accumulated_tool_calls[tc_index]["name"] = func_name
                        if func_args:
                            if isinstance(func_args, dict):
                                accumulated_tool_calls[tc_index]["arguments"] = json.dumps(func_args)
                            else:
                                accumulated_tool_calls[tc_index]["arguments"] += func_args

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        # Close unclosed reasoning block (e.g. hit max_tokens mid-reasoning)
        if in_think_block:
            await emitter.send_chunk("\n</reasoning>")

        content = []

        if accumulated_content:
            content.append(TextContent(text=accumulated_content))
        elif accumulated_reasoning:
            content.append(TextContent(text=f"<think>{accumulated_reasoning}</think>"))

        for tc_data in accumulated_tool_calls:
            if tc_data["name"]:
                arguments = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
                content.append(
                    ToolCallContent(id=tc_data["id"], name=tc_data["name"], arguments=arguments)
                )

        messages = []
        if content:
            messages.append(UnifiedMessage(role="assistant", content=content, id=response_id))

        token_usage = None
        if usage_data:
            token_usage = TokenUsage(
                input_tokens=usage_data.prompt_tokens,
                output_tokens=usage_data.completion_tokens,
                matrx_model_name=model,
                provider_model_name=model,
                api=self.provider_name,
                response_id=response_id or "",
            )

        unified_finish_reason = None
        if finish_reason == "stop":
            unified_finish_reason = FinishReason.STOP
        elif finish_reason == "length":
            unified_finish_reason = FinishReason.MAX_TOKENS
        elif finish_reason == "tool_calls":
            unified_finish_reason = FinishReason.TOOL_CALLS
        elif finish_reason == "content_filter":
            unified_finish_reason = FinishReason.CONTENT_FILTER

        vcprint(f"{self.endpoint_name} Streaming execution completed successfully", color="green")

        return UnifiedResponse(
            messages=messages,
            usage=token_usage,
            finish_reason=unified_finish_reason,
            stop_reason=finish_reason,
        )
```

### 3. `matrx_ai/providers/generic_openai/translator.py`

Copy `aidream/ai/providers/generic_openai/translator.py` with these import substitutions:

| aidream import | matrx-ai replacement |
|---|---|
| `from ai.config import ...` | `from matrx_ai.config import ...` |
| `from ai.providers.base_translator import BaseTranslator` | see note below |
| `from ai.tools.registry import ToolRegistryV2` | `from matrx_ai.tools.registry import ToolRegistryV2` |

**Note on `BaseTranslator`:** Check if `matrx_ai/providers/base_translator.py` exists. If not, either
copy it from `aidream/ai/providers/base_translator.py` or inline the `get_system_text()` helper method
directly into `GenericOpenAITranslator`. The method simply returns `config.system_instruction or ""`.

**Full adapted file:**

```python
from __future__ import annotations

import json
from typing import Any

from matrx_utils import vcprint

from matrx_ai.config import (
    FinishReason,
    TextContent,
    TokenUsage,
    ToolCallContent,
    ToolResultContent,
    UnifiedConfig,
    UnifiedMessage,
    UnifiedResponse,
    YouTubeVideoContent,
)
from matrx_ai.tools.registry import ToolRegistryV2


class GenericOpenAITranslator:
    """Translates between unified format and any OpenAI-compatible API."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    def get_system_text(self, config: UnifiedConfig) -> str:
        return config.system_instruction or ""

    def to_generic_openai(self, config: UnifiedConfig, provider_name: str = "generic_openai") -> dict[str, Any]:
        """
        Convert unified config to OpenAI-compatible chat completion format.

        Works with any OpenAI-compatible endpoint (llama-server, HuggingFace TGI,
        vLLM, LocalAI, Ollama, etc.)
        """
        messages = []

        system_text = self.get_system_text(config)
        if system_text:
            messages.append({"role": "system", "content": system_text})

        for msg in config.messages:
            if msg.role == "tool":
                for content in msg.content:
                    if isinstance(content, ToolResultContent):
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": content.tool_use_id or content.call_id,
                                "content": json.dumps(content.content)
                                if isinstance(content.content, (dict, list))
                                else str(content.content),
                            }
                        )
            else:
                message_dict = {"role": msg.role}
                text_parts = []
                tool_calls = []

                for content in msg.content:
                    if isinstance(content, TextContent):
                        text_parts.append(content.text)
                    elif isinstance(content, ToolCallContent):
                        tool_calls.append(
                            {
                                "id": content.id,
                                "type": "function",
                                "function": {
                                    "name": content.name,
                                    "arguments": json.dumps(content.arguments),
                                },
                            }
                        )
                    elif isinstance(content, YouTubeVideoContent):
                        vcprint(
                            f"YouTube URL '{content.youtube_url}' is not supported by generic OpenAI-compatible endpoints and will be skipped.",
                            "YouTube URL Warning",
                            color="yellow",
                        )

                if text_parts:
                    message_dict["content"] = "".join(text_parts)
                elif tool_calls:
                    message_dict["content"] = None
                else:
                    message_dict["content"] = ""

                if tool_calls:
                    message_dict["tool_calls"] = tool_calls

                if text_parts or tool_calls or message_dict["content"] == "":
                    messages.append(message_dict)

        request: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
        }

        if config.max_output_tokens:
            request["max_tokens"] = config.max_output_tokens
        if config.temperature is not None:
            request["temperature"] = config.temperature
        if config.top_p is not None:
            request["top_p"] = config.top_p
        if config.stop_sequences:
            request["stop"] = config.stop_sequences
        if config.response_format:
            request["response_format"] = config.response_format
        if config.stream:
            request["stream"] = True

        if config.tools:
            request["tools"] = ToolRegistryV2.get_instance().get_provider_tools(
                config.tools, "generic_openai"
            )
            if config.tool_choice:
                request["tool_choice"] = config.tool_choice

        vcprint(request, f"--> {provider_name} Request", color="magenta", verbose=False)
        return request

    def from_generic_openai(self, response: Any, provider_name: str = "generic_openai") -> UnifiedResponse:
        """Convert OpenAI-compatible response to unified format."""
        messages = []

        if not response.choices:
            return UnifiedResponse(messages=[], finish_reason=FinishReason.ERROR)

        choice = response.choices[0]
        message = choice.message
        content = []

        reasoning = getattr(message, "reasoning_content", None)
        main_text = message.content or ""

        if reasoning and main_text:
            content.append(TextContent(text=f"<reasoning>{reasoning}</reasoning>\n{main_text}"))
        elif main_text:
            content.append(TextContent(text=main_text))
        elif reasoning:
            content.append(TextContent(text=f"<reasoning>{reasoning}</reasoning>"))

        if message.tool_calls:
            for tc in message.tool_calls:
                arguments = (
                    json.loads(tc.function.arguments)
                    if isinstance(tc.function.arguments, str)
                    else tc.function.arguments
                )
                content.append(
                    ToolCallContent(
                        id=tc.id, name=tc.function.name, arguments=arguments
                    )
                )

        if content:
            messages.append(
                UnifiedMessage(role="assistant", content=content, id=response.id)
            )

        token_usage = None
        if response.usage:
            token_usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                matrx_model_name=getattr(response, "model", ""),
                provider_model_name=getattr(response, "model", ""),
                api=provider_name,
                response_id=response.id,
            )

        finish_reason = None
        if choice.finish_reason == "stop":
            finish_reason = FinishReason.STOP
        elif choice.finish_reason == "length":
            finish_reason = FinishReason.MAX_TOKENS
        elif choice.finish_reason == "tool_calls":
            finish_reason = FinishReason.TOOL_CALLS
        elif choice.finish_reason == "content_filter":
            finish_reason = FinishReason.CONTENT_FILTER

        return UnifiedResponse(
            messages=messages,
            usage=token_usage,
            finish_reason=finish_reason,
            stop_reason=choice.finish_reason,
            raw_response=response,
        )
```

---

## Files to Modify

### 4. `matrx_ai/providers/__init__.py`

Add these imports and exports:

```python
# Add after existing imports:
from .generic_openai import GenericOpenAIChat, GenericOpenAITranslator

# Add to __all__:
"GenericOpenAIChat",
"GenericOpenAITranslator",
```

### 5. `matrx_ai/providers/unified_client.py`

**In `API_CLASS_TO_ENDPOINT`**, add two entries:

```python
"generic_openai_standard": "generic_openai_chat",
"huggingface_standard": "generic_openai_chat",
```

**In `UnifiedAIClient.__init__`**, add:

```python
from matrx_ai.providers.generic_openai import GenericOpenAIChat
# NOTE: base_url is intentionally empty here — matrx-local's local_llm_registry
# creates its own GenericOpenAIChat instance with the correct port and registers
# it via the module-level _local_chat_instance. The UnifiedAIClient dispatch
# branch reads that instance at call time.
self.generic_openai_chat = None  # populated at call time via _get_generic_openai_chat()
```

Actually, the cleaner approach: add a module-level registry in `unified_client.py`:

```python
# Module-level registry for dynamic generic-openai instances (e.g. local LLM)
_generic_openai_instances: dict[str, "GenericOpenAIChat"] = {}

def register_generic_openai_instance(name: str, instance: "GenericOpenAIChat") -> None:
    """Register a GenericOpenAIChat instance by name for use in execute()."""
    _generic_openai_instances[name] = instance

def unregister_generic_openai_instance(name: str) -> None:
    _generic_openai_instances.pop(name, None)

def get_generic_openai_instance(name: str) -> "GenericOpenAIChat | None":
    return _generic_openai_instances.get(name)
```

**In `UnifiedAIClient.execute()`**, add the dispatch branch:

```python
elif endpoint == "generic_openai_chat":
    # Look up a registered instance by model name (allows dynamic base_url per model)
    instance = _generic_openai_instances.get(model_name) or _generic_openai_instances.get("default")
    if instance is None:
        raise ValueError(
            f"No GenericOpenAIChat instance registered for model '{model_name}'. "
            "Call register_generic_openai_instance() before making requests."
        )
    return await instance.execute(config, api_class, debug)
```

### 6. `matrx_ai/tools/models.py`

In `get_provider_format()`, the existing fallback `formatters.get(provider, self.to_openai_format)()` already handles `generic_openai` correctly (falls back to OpenAI function call format). No change strictly required, but for explicitness you can add:

```python
"generic_openai": self.to_openai_format,
"huggingface": self.to_openai_format,
```

---

## Version

Bump `matrx-ai` to `>= 0.1.23` and tag `v0.1.23` after these changes are merged.

Update `matrx-local/pyproject.toml`:
```toml
"matrx-ai>=0.1.23",
```

---

## Testing

After the changes are in place, run `uv sync` in `matrx-local` and start the engine. The startup log
should show:

```
[local_llm_registry] matrx-ai GenericOpenAIChat support: AVAILABLE ✓
```

If you see instead:
```
[local_llm_registry] matrx-ai does not yet include GenericOpenAIChat. Local LLM routing is DISABLED.
See instructions: docs/matrx-ai-generic-openai-port.md
```

…then the package version has not been updated yet.

To test end-to-end:
1. Start a local llama-server on port 11434
2. POST to `http://localhost:22140/chat/local-llm/connect` with `{"port": 11434, "model_name": "qwen3-8b"}`
3. Send an agent request with `config_overrides: {"model": "local/qwen3-8b"}`
4. Verify the request reaches llama-server and streams back correctly
