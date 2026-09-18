"""CAD-host adapter implementations."""

from .base import KernelAdapter
from .freecad import FreeCADKernelAdapter, FreeCADUnavailableError
from .memory import MemoryKernelAdapter

__all__ = [
    "FreeCADKernelAdapter",
    "FreeCADUnavailableError",
    "KernelAdapter",
    "MemoryKernelAdapter",
]
