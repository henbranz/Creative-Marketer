from dataclasses import dataclass
from typing import Protocol

from .domain import AssemblyPlan


@dataclass(frozen=True, slots=True)
class MaterializedSource:
    asset_id: str
    path: str
    media_kind: str
    has_audio: bool


@dataclass(frozen=True, slots=True)
class RenderResult:
    path: str
    renderer: str
    renderer_version: str
    duration_ms: int
    width: int
    height: int
    fps: int
    has_audio: bool
    byte_size: int
    digest: str


class MediaAssemblyRenderer(Protocol):
    async def render(
        self, plan: AssemblyPlan, sources: tuple[MaterializedSource, ...], workspace: str
    ) -> RenderResult: ...
