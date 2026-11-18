"""Adaptadores de almacén incluidos. Los dos pasan las mismas secuencias del spec."""

from .memory import MemoryStore
from .sqlite import SqliteStore

__all__ = ["MemoryStore", "SqliteStore"]
