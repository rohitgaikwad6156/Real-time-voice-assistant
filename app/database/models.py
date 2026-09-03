"""Data models for SQLite-backed storage.

Provides typed record structures for reminders and notes.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass
class ReminderRecord:
    """Represents a reminder row in SQLite."""

    id: int
    title: str
    remind_at: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        return asdict(self)


@dataclass
class NoteRecord:
    """Represents a note row in SQLite."""

    id: int
    title: str
    content: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary."""
        return asdict(self)
