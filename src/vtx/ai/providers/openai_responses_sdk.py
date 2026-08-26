"""Provider for models served over the OpenAI **Responses** API
(``/responses``): native openai-responses slugs and any provider whose
catalog marks the endpoint as Responses-style."""

from __future__ import annotations

from typing import ClassVar

from vtx.ai.providers.openai_sdk import OpenAISDKProvider


class OpenAIResponsesSDKProvider(OpenAISDKProvider):
    """Same conversion/processing pipeline as the Chat Completions provider,
    but routed through the Responses-API SDK (reasoning effort rides
    ``reasoning: {effort}``, tool calls use ``call_id``/``function_call``
    items).

    Transport is the official ``openai`` package's Responses flow
    (``AsyncOpenAI().responses.create(..., stream=True)``); typed stream
    events are lowered into the same chunk vocabulary the base pipeline
    consumes.
    """

    name = "openai-responses"
    thinking_levels: ClassVar[list[str]] = [
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]

    def __init__(self, config):
        super().__init__(config)
        # Swap the transport built by the base class for a Responses one,
        # keeping the resolved api key/base URL.
        from vtx.ai.sdk.openai_responses import OpenAIResponsesSDK

        self._sdk = OpenAIResponsesSDK(
            api_key=self._sdk.api_key,
            base_url=self.config.base_url,
            provider_slug=self.config.provider,
        )
