import pytest

from cadcopilot.adapters.memory import MemoryKernelAdapter
from cadcopilot.registry import AdapterRegistry


def test_registry_reports_capabilities_and_rejects_duplicates():
    registry = AdapterRegistry()
    registry.register(MemoryKernelAdapter())
    assert registry.capabilities()[0].adapter_id == "memory"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(MemoryKernelAdapter())


def test_registry_discovers_external_adapter_factories(monkeypatch):
    class FakeEntryPoint:
        name = "memory-plugin"

        @staticmethod
        def load():
            return MemoryKernelAdapter

    monkeypatch.setattr(
        "cadcopilot.registry.entry_points",
        lambda **kwargs: (FakeEntryPoint(),),
    )
    registry = AdapterRegistry()
    assert registry.discover() == ("memory",)
    assert registry.get("memory").metadata().native_host == "none"
