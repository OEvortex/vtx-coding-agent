"""Message tool for sending messages to users."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from loguru import logger

from vtx_claw.agent.tools.base import Tool, tool_parameters
from vtx_claw.agent.tools.context import ContextAware, RequestContext
from vtx_claw.agent.tools.path_utils import resolve_workspace_path
from vtx_claw.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from vtx_claw.bus.events import OutboundMessage
from vtx_claw.config.paths import get_workspace_path
from vtx_claw.security.workspace_access import current_tool_workspace


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema(
            "Content to send proactively/cross-channel (not a normal reply)."
        ),
        channel=StringSchema(
            "Optional target channel for proactive/cross-channel delivery."
        ),
        chat_id=StringSchema(
            "Optional target chat/user id. Omit on WebSocket to use the conversation id."
        ),
        media=ArraySchema(
            StringSchema(""),
            description=(
                "Optional file paths to attach. Use generate_image artifact paths for images."
            ),
        ),
        buttons=ArraySchema(
            ArraySchema(StringSchema("Button label")),
            description="Optional inline buttons: list of rows, each a list of labels.",
        ),
        required=["content"],
    )
)
class MessageTool(Tool, ContextAware):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        workspace: str | Path | None = None,
        restrict_to_workspace: bool = False,
    ):
        self._send_callback = send_callback
        self._workspace = (
            Path(workspace).expanduser() if workspace is not None else get_workspace_path()
        )
        self._restrict_to_workspace = restrict_to_workspace
        self._default_channel: ContextVar[str] = ContextVar(
            "message_default_channel", default=default_channel
        )
        self._default_chat_id: ContextVar[str] = ContextVar(
            "message_default_chat_id", default=default_chat_id
        )
        self._default_message_id: ContextVar[str | None] = ContextVar(
            "message_default_message_id", default=default_message_id
        )
        self._default_metadata: ContextVar[dict[str, Any]] = ContextVar(
            "message_default_metadata", default={}
        )
        self._sent_in_turn_var: ContextVar[bool] = ContextVar(
            "message_sent_in_turn", default=False
        )
        self._turn_delivered_media_var: ContextVar[tuple[str, ...]] = ContextVar(
            "message_turn_delivered_media", default=()
        )
        self._record_channel_delivery_var: ContextVar[bool] = ContextVar(
            "message_record_channel_delivery", default=False
        )
        self._suppress_delivery_var: ContextVar[bool] = ContextVar(
            "message_suppress_delivery", default=False
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        send_callback = ctx.bus.publish_outbound if ctx.bus else None
        return cls(
            send_callback=send_callback,
            workspace=ctx.workspace,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
        )

    def set_context(self, ctx: RequestContext) -> None:
        """Set the current message context."""
        self._default_channel.set(ctx.channel)
        self._default_chat_id.set(ctx.chat_id)
        self._default_message_id.set(ctx.message_id)
        self._default_metadata.set(dict(ctx.metadata or {}))

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._sent_in_turn = False
        self._turn_delivered_media_var.set(())

    def turn_delivered_media_paths(self) -> list[str]:
        """Absolute paths attached via this tool to the active chat in the current turn."""
        return list(self._turn_delivered_media_var.get())

    def set_record_channel_delivery(self, active: bool):
        """Mark tool-sent messages as proactive channel deliveries."""
        return self._record_channel_delivery_var.set(active)

    def reset_record_channel_delivery(self, token) -> None:
        """Restore previous proactive delivery recording state."""
        self._record_channel_delivery_var.reset(token)

    def set_suppress_delivery(self, active: bool):
        """Acknowledge but don't deliver tool sends (heartbeat internal check)."""
        return self._suppress_delivery_var.set(active)

    def reset_suppress_delivery(self, token) -> None:
        """Restore previous delivery-suppression state."""
        self._suppress_delivery_var.reset(token)

    @property
    def _sent_in_turn(self) -> bool:
        return self._sent_in_turn_var.get()

    @_sent_in_turn.setter
    def _sent_in_turn(self, value: bool) -> None:
        self._sent_in_turn_var.set(value)

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Proactively send a message to a user/channel with optional attachments. "
            "Use for reminders and cross-channel delivery, not the normal reply. "
            "Use media paths to deliver generated images. Never call this to reply "
            "in the current chat."
        )

    def _resolve_media(self, media: list[str]) -> list[str]:
        """Resolve local media attachments and enforce workspace restriction when enabled."""
        resolved: list[str] = []
        access = current_tool_workspace(
            self._workspace, restrict_to_workspace=self._restrict_to_workspace
        )
        workspace = access.project_path or self._workspace
        for p in media:
            if p.startswith(("http://", "https://")):
                resolved.append(p)
            elif not access.restrict_to_workspace:
                path = Path(p).expanduser()
                resolved.append(p if path.is_absolute() else str(workspace / path))
            else:
                resolved.append(str(resolve_workspace_path(p, workspace, access.allowed_root)))
        return resolved

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        buttons: list[list[str]] | None = None,
        **kwargs: Any,
    ) -> str:
        from vtx_claw.utils.helpers import strip_think

        content = strip_think(content)

        if buttons is not None and (
            not isinstance(buttons, list)
            or any(
                not isinstance(row, list) or any(not isinstance(label, str) for label in row)
                for row in buttons
            )
        ):
            return "Error: buttons must be a list of list of strings"
        default_channel = self._default_channel.get()
        default_chat_id = self._default_chat_id.get()
        channel = channel or default_channel
        explicit_chat_id = chat_id
        if (
            default_channel == "websocket"
            and channel == "websocket"
            and explicit_chat_id is not None
            and str(explicit_chat_id).strip() != ""
            and str(explicit_chat_id).strip() != str(default_chat_id).strip()
        ):
            return (
                "Error: chat_id does not match the active WebSocket conversation. "
                "Omit chat_id (and usually channel) so delivery uses the current "
                "conversation id from context — WebSocket client_id strings "
                "(e.g. anon-…) are not chat ids."
            )
        chat_id = chat_id or default_chat_id
        # Only inherit default message_id when targeting the same channel+chat.
        # Cross-chat sends must not carry the original message_id, because
        # some channels (e.g. Feishu) use it to determine the target
        # conversation via their Reply API, which would route the message
        # to the wrong chat entirely.
        same_target = channel == default_channel and chat_id == default_chat_id
        message_id = message_id or self._default_message_id.get() if same_target else None

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        if media:
            try:
                media = self._resolve_media(media)
            except (OSError, PermissionError, ValueError) as e:
                return f"Error: media path is not allowed: {e!s}"

        metadata = dict(self._default_metadata.get()) if same_target else {}
        if message_id:
            metadata["message_id"] = message_id
        if self._record_channel_delivery_var.get() or media:
            metadata["_record_channel_delivery"] = True

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            buttons=buttons or [],
            metadata=metadata,
        )

        if self._suppress_delivery_var.get():
            logger.debug("MessageTool: delivery suppressed during internal check")
            return f"Message acknowledged for {channel}:{chat_id} (not delivered)"

        try:
            await self._send_callback(msg)
            if channel == default_channel and chat_id == default_chat_id:
                self._sent_in_turn = True
                if media:
                    prev = self._turn_delivered_media_var.get()
                    self._turn_delivered_media_var.set(prev + tuple(str(p) for p in media))
            media_info = f" with {len(media)} attachments" if media else ""
            button_info = f" with {sum(len(row) for row in buttons)} button(s)" if buttons else ""
            return f"Message sent to {channel}:{chat_id}{media_info}{button_info}"
        except Exception as e:
            return f"Error sending message: {e!s}"
