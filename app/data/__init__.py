"""Dashboard data layer. The web layer only talks to a DataSource (demo or live)."""
from __future__ import annotations

from ..settings import settings
from .base import DataSource


_source: DataSource | None = None


def get_source() -> DataSource:
    global _source
    if _source is None:
        if settings.demo:
            from .demo import DemoSource
            _source = DemoSource()
        else:
            from .live import LiveSource
            _source = LiveSource()
    return _source
