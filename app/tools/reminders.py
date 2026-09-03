"""SQLite-backed Reminders tool implementation.

Provides structured reminder creation with validation and SQLite database persistence.
Independent from any specific LLM client.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from app.database import (
    clear_reminders_table,
    get_all_reminders,
    init_db,
    insert_reminder,
)

logger = logging.getLogger(__name__)


def create_reminder(
    title: str,
    remind_at: Optional[str] = None,
    time_or_delay: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new reminder and persist it into the SQLite database.

    Args:
        title: Short description or subject of the reminder.
        remind_at: Scheduled target time/date (e.g. "tomorrow at 7 PM", "at 5:00 PM", "in 15 minutes").
        time_or_delay: Alias for remind_at for backward compatibility.

    Returns:
        Structured dictionary containing the created reminder record or error details.
    """
    # 1. Validate title
    if not title or not isinstance(title, str) or not title.strip():
        return {
            "status": "error",
            "error": "Parameter 'title' is required and must be a non-empty string.",
        }

    # 2. Validate remind_at
    target_time = remind_at or time_or_delay
    if not target_time or not isinstance(target_time, str) or not target_time.strip():
        return {
            "status": "error",
            "error": "Parameter 'remind_at' is required and must be a non-empty string.",
        }

    clean_title = title.strip()
    clean_remind_at = target_time.strip()

    # 3. Persist in SQLite
    try:
        init_db()
        record = insert_reminder(title=clean_title, remind_at=clean_remind_at)
        logger.info("Successfully persisted reminder #%d in SQLite.", record["id"])

        return {
            "status": "success",
            "tool": "create_reminder",
            "reminder": record,
            "message": f"Reminder #{record['id']} created: '{record['title']}' for {record['remind_at']}.",
        }

    except sqlite3.Error as db_err:
        logger.error("SQLite database error creating reminder: %s", db_err)
        return {
            "status": "error",
            "error": f"Database error creating reminder: {db_err}",
        }
    except Exception as exc:
        logger.exception("Unexpected error creating reminder: %s", exc)
        return {
            "status": "error",
            "error": f"Failed to create reminder: {exc}",
        }


def list_reminders() -> List[Dict[str, Any]]:
    """Retrieve all reminders from SQLite database."""
    init_db()
    return get_all_reminders()


def clear_reminders() -> None:
    """Clear all reminders from SQLite (useful for test isolation)."""
    init_db()
    clear_reminders_table()
