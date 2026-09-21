"""The reasoning layer behind a vendor-neutral port.

`LlmPort` is the only thing the runner knows about. `ClaudeLlm` is the one
implementation. A manual tool loop is used rather than the SDK's tool runner
because a turn can pause for hours on a person's click and must resume from
Redis in a different process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .types import ToolCall, ToolSpec


class LlmUnavailable(Exception):
    """The model could not be reached or is overloaded. Safe to retry later."""


class LlmRequestError(Exception):
    """The request itself was rejected. Retrying the same request will not help."""


@dataclass(frozen=True)
class LlmStep:
    content: list[dict[str, Any]]  # assistant content blocks, stored and replayed verbatim
    tool_calls: list[ToolCall]
    text: str
    stop_reason: str
    input_tokens: int
    output_tokens: int
    model: str


class LlmPort(Protocol):
    async def step(self, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]]) -> LlmStep: ...


class ClaudeLlm:
    def __init__(self, api_key: str | None = None, model: str = "claude-opus-5", max_tokens: int = 16000, client: Any = None):
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def step(self, system: str, tools: list[ToolSpec], messages: list[dict[str, Any]]) -> LlmStep:
        import anthropic

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=[
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema, "strict": True}
                    for t in tools
                ],
                thinking={"type": "adaptive"},
                messages=messages,
            )
        except anthropic.RateLimitError as exc:
            raise LlmUnavailable("rate limited") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmUnavailable("connection failed") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LlmUnavailable(f"server error {exc.status_code}") from exc
            raise LlmRequestError(f"request rejected {exc.status_code}") from exc

        content = [block.model_dump(exclude_none=True) for block in response.content]
        calls = [ToolCall(id=b.id, name=b.name, input=dict(b.input)) for b in response.content if b.type == "tool_use"]
        text = "".join(b.text for b in response.content if b.type == "text")
        return LlmStep(
            content=content,
            tool_calls=calls,
            text=text,
            stop_reason=response.stop_reason or "end_turn",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )
