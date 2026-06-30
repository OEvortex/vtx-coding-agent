"""MCP capability negotiation and data models for vtx_claw.

Tracks server capabilities, resource/prompt/sampling specs.
Adapted from Jarvis's MCP capabilities module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# SERVER CAPABILITIES TRACKING
# ============================================================================


@dataclass
class MCPServerCapabilities:
    """Structured representation of what an MCP server supports."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False
    sampling: bool = False
    logging: bool = False

    @classmethod
    def from_server_capabilities(cls, caps: Any | None) -> MCPServerCapabilities:
        """Create from the SDK's ServerCapabilities object."""
        if caps is None:
            return cls()
        return cls(
            tools=caps.tools is not None,
            resources=caps.resources is not None,
            prompts=caps.prompts is not None,
            logging=caps.logging is not None,
        )

    def to_dict(self) -> dict[str, bool]:
        return {
            "tools": self.tools,
            "resources": self.resources,
            "prompts": self.prompts,
            "sampling": self.sampling,
            "logging": self.logging,
        }


# ============================================================================
# DATA MODELS
# ============================================================================


@dataclass
class MCPResourceSpec:
    """Specification for an MCP resource."""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    server_name: str = ""

    @classmethod
    def from_sdk(cls, resource: Any, server_name: str = "") -> MCPResourceSpec:
        return cls(
            uri=str(resource.uri),
            name=resource.name or "",
            description=resource.description or "",
            mime_type=resource.mimeType or "",
            server_name=server_name,
        )


@dataclass
class MCPResourceContent:
    """Content returned by reading an MCP resource."""

    uri: str
    mime_type: str = ""
    text: str = ""
    blob: bytes | None = None


@dataclass
class MCPPromptArgument:
    """Argument for an MCP prompt template."""

    name: str
    description: str = ""
    required: bool = False


@dataclass
class MCPPromptSpec:
    """Specification for an MCP prompt template."""

    name: str
    description: str = ""
    arguments: list[MCPPromptArgument] = field(default_factory=list)
    server_name: str = ""

    @classmethod
    def from_sdk(cls, prompt: Any, server_name: str = "") -> MCPPromptSpec:
        args = []
        if prompt.arguments:
            for arg in prompt.arguments:
                args.append(
                    MCPPromptArgument(
                        name=arg.name or "",
                        description=arg.description or "",
                        required=arg.required or False,
                    )
                )
        return cls(
            name=prompt.name,
            description=prompt.description or "",
            arguments=args,
            server_name=server_name,
        )


@dataclass
class MCPPromptMessage:
    """A single message in a rendered MCP prompt."""

    role: str  # "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}
