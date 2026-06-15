"""Base SDK class for LLM providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    role: str
    content: str
    metadata: dict[str, Any] | None = None
    image_parts: list[str] | None = None


@dataclass
class GenerationConfig:
    model: str
    temperature: float = 0.7
    max_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    stop_sequences: list[str] | None = None
    tool_choice: str | dict | bool | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class GenerationResponse:
    content: str
    model: str
    finish_reason: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: dict[str, int] | None = None
    reasoning_content: str = ""


class BaseLLMSDK(ABC):
    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url

    @property
    @abstractmethod
    def client(self): ...

    @abstractmethod
    async def generate(
        self, messages: list[Message], config: GenerationConfig, stream: bool = False
    ) -> GenerationResponse | AsyncGenerator: ...

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        config: GenerationConfig,
        stream: bool = False,
    ) -> GenerationResponse | AsyncGenerator: ...

    @abstractmethod
    def get_available_models(self) -> list[str]: ...

    def convert_messages_to_dict(self, messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.image_parts:
                content: list[dict[str, Any]] = [{"type": "text", "text": msg.content}]
                for image_url in msg.image_parts:
                    content.append({"type": "image_url", "image_url": {"url": image_url}})
                result.append({"role": msg.role, "content": content, **(msg.metadata or {})})
            else:
                result.append({"role": msg.role, "content": msg.content, **(msg.metadata or {})})
        return result
