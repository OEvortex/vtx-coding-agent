"""Shared fixtures for the SDK test suite."""

from __future__ import annotations

import pytest

from vtx.llm.providers.mock import MockProvider


@pytest.fixture
def text_provider() -> MockProvider:
    return MockProvider(scenario="simple_text")


@pytest.fixture
def tool_provider() -> MockProvider:
    return MockProvider(scenario="thinking_text_tool")


@pytest.fixture
def multi_tool_provider() -> MockProvider:
    return MockProvider(scenario="default")
