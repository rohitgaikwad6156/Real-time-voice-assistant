"""Database package for SQLite persistence."""

from app.database.database import (
    clear_notes_table,
    clear_reminders_table,
    get_all_reminders,
    get_connection,
    get_db_path,
    init_db,
    insert_note,
    insert_reminder,
    search_notes_db,
)
from app.database.models import NoteRecord, ReminderRecord

__all__ = [
    "ReminderRecord",
    "NoteRecord",
    "get_db_path",
    "get_connection",
    "init_db",
    "insert_reminder",
    "get_all_reminders",
    "clear_reminders_table",
    "insert_note",
    "search_notes_db",
    "clear_notes_table",
]
