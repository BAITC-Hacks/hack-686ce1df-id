"""One immutable publication containing analytics and matching demo metadata."""

from dataclasses import dataclass

from .demo.models import DemoSource
from .store import ResultStore


@dataclass(frozen=True)
class PublishedSnapshot:
    store: ResultStore
    source: DemoSource | None = None
