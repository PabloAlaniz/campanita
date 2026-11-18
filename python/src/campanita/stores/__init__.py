"""Adaptadores de almacén incluidos. Todos pasan las secuencias del spec."""

from .memory import MemoryPreferenceStore, MemoryStore
from .sqlite import SqlitePreferenceStore, SqliteStore

__all__ = ["MemoryPreferenceStore", "MemoryStore", "SqlitePreferenceStore", "SqliteStore"]
