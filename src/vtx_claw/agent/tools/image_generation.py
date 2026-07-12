"""Image generation tool."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from vtx_claw.agent.tools.base import Tool, tool_parameters
from vtx_claw.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from vtx_claw.config.paths import get_media_dir
from vtx_claw.config_base import Base
from vtx_claw.providers.image_generation import (
    ImageGenerationError,
    ImageGenerationProvider,
    get_image_gen_provider,
)
from vtx_claw.security.workspace_access import current_tool_workspace
from vtx_claw.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from vtx_claw.utils.artifacts import (
    ArtifactError,
    generated_image_tool_result,
    store_generated_image_artifact,
)
from vtx_claw.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from vtx_claw.config.schema import ProviderConfig


class ImageGenerationToolConfig(Base):
    """Image generation tool configuration."""

    enabled: bool = False
    provider: str = "openrouter"
    model: str = "openai/gpt-5.4-image-2"
    default_aspect_ratio: str = "1:1"
    default_image_size: str = "1K"
    max_images_per_turn: int = Field(default=4, ge=1, le=8)
    save_dir: str = "generated"


@tool_parameters(
    tool_parameters_schema(
        prompt=StringSchema(
            "Image generation/edit prompt: subject, style, composition, colors.",
            min_length=1,
        ),
        reference_images=ArraySchema(
            StringSchema("Local image path to use as an edit reference."),
            description="Optional image paths; use generated artifact paths for iterative edits.",
        ),
        aspect_ratio=StringSchema("Optional ratio e.g. 1:1, 16:9, 9:16, 4:3."),
        image_size=StringSchema("Optional size hint e.g. 1K, 2K, 4K, 1024x1024."),
        count=IntegerSchema(
            description="Images to generate this turn (1-8).", minimum=1, maximum=8
        ),
        required=["prompt"],
    )
)
class ImageGenerationTool(Tool):
    """Generate persistent image artifacts through the configured image provider."""

    config_key = "image_generation"

    @classmethod
    def config_cls(cls):
        return ImageGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.image_generation.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.image_generation,
            provider_configs=ctx.image_generation_provider_configs,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: ImageGenerationToolConfig,
        provider_config: ProviderConfig | None = None,
        provider_configs: dict[str, ProviderConfig] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.provider_configs = dict(provider_configs or {})
        if provider_config is not None and "openrouter" not in self.provider_configs:
            self.provider_configs["openrouter"] = provider_config

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate/edit images, stored as persistent artifacts (returns ids/paths). "
            "For edits, pass prior generated or user image paths as reference_images."
        )

    def _provider_config(self) -> ProviderConfig | None:
        return self.provider_configs.get(self.config.provider)

    def _provider_client(self) -> ImageGenerationProvider | None:
        provider = self._provider_config()
        cls = get_image_gen_provider(self.config.provider)
        if cls is None:
            return None
        from vtx_claw.config.schema import ProviderConfig as _PC

        provider_cfg: _PC | None = provider
        return cls(
            api_key=provider_cfg.api_key if provider_cfg else None,
            api_base=provider_cfg.api_base if provider_cfg else None,
            extra_headers=provider_cfg.extra_headers if provider_cfg else None,
            extra_body=provider_cfg.extra_body if provider_cfg else None,
        )

    def _resolve_reference_image(self, value: str) -> str:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                value,
                workspace=workspace,
                allowed_root=access.allowed_root,
                extra_allowed_roots=[get_media_dir()] if access.allowed_root is not None else None,
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise ImageGenerationError(
                "reference_images must be inside the workspace or vtx_claw media directory"
            ) from exc
        except OSError as exc:
            raise ImageGenerationError(f"reference image not found: {value}") from exc
        if not resolved.is_file():
            raise ImageGenerationError(f"reference image is not a file: {value}")
        raw = resolved.read_bytes()
        if detect_image_mime(raw) is None:
            raise ImageGenerationError(f"unsupported reference image: {value}")
        return str(resolved)

    def _resolve_reference_images(self, values: list[str] | None) -> list[str]:
        if not values:
            return []
        return [self._resolve_reference_image(value) for value in values if value]

    async def execute(
        self,
        prompt: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        count: int | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._provider_client()
        if client is None:
            return f"Error: unsupported image generation provider '{self.config.provider}'"

        requested = count or 1
        if requested > self.config.max_images_per_turn:
            return (
                "Error: count exceeds tools.imageGeneration.maxImagesPerTurn "
                f"({self.config.max_images_per_turn})"
            )

        try:
            refs = self._resolve_reference_images(reference_images)
            artifacts: list[dict[str, Any]] = []
            while len(artifacts) < requested:
                response = await client.generate(
                    prompt=prompt,
                    model=self.config.model,
                    reference_images=refs,
                    aspect_ratio=aspect_ratio or self.config.default_aspect_ratio,
                    image_size=image_size or self.config.default_image_size,
                )
                for image_data_url in response.images:
                    artifact = store_generated_image_artifact(
                        image_data_url,
                        prompt=prompt,
                        model=self.config.model,
                        source_images=refs,
                        save_dir=self.config.save_dir,
                        provider=self.config.provider,
                    )
                    artifacts.append(artifact)
                    if len(artifacts) >= requested:
                        break
            return generated_image_tool_result(artifacts)
        except (ArtifactError, ImageGenerationError, OSError) as exc:
            return f"Error: {exc}"
