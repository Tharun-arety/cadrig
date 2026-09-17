"""Runtime discovery for independently distributed CAD-host adapters."""

from __future__ import annotations

from importlib.metadata import entry_points

from cadcopilot.adapters.base import KernelAdapter
from cadcopilot.contracts import AdapterMetadata

ENTRY_POINT_GROUP = "cadcopilot.adapters"


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, KernelAdapter] = {}

    def register(self, adapter: KernelAdapter) -> None:
        if not isinstance(adapter, KernelAdapter):
            raise TypeError("adapter does not implement KernelAdapter")
        metadata = adapter.metadata()
        if metadata.adapter_id in self._adapters:
            raise ValueError(f"adapter already registered: {metadata.adapter_id}")
        self._adapters[metadata.adapter_id] = adapter

    def discover(self) -> tuple[str, ...]:
        """Load adapter factories installed under the public plugin group."""

        loaded = []
        for entry_point in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda item: item.name):
            factory = entry_point.load()
            adapter = factory()
            self.register(adapter)
            loaded.append(adapter.metadata().adapter_id)
        return tuple(loaded)

    def get(self, adapter_id: str) -> KernelAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise KeyError(f"adapter is not registered: {adapter_id}") from exc

    def capabilities(self) -> tuple[AdapterMetadata, ...]:
        return tuple(
            self._adapters[key].metadata() for key in sorted(self._adapters)
        )
