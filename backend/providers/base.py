"""Provider interfaces. Each provider returns raw bytes; the worker stores them."""
from dataclasses import dataclass, field
from typing import Callable

Report = Callable[[str, int], None]  # (stage text, progress 0..100)


class ProviderError(Exception):
    """A failure that is safe to show to the customer (credits are refunded)."""


class ContentBlocked(ProviderError):
    pass


@dataclass
class ImageResult:
    data: bytes
    ext: str
    seed: int | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class MeshResult:
    glb: bytes
    meta: dict = field(default_factory=dict)
