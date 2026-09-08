"""Reminder tool implementation.

Uses MongoDB for authenticated users. SQLite remains as a legacy fallback for
local/tests that do not provide a user id.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from app.database import clear_reminders_table, get_all_reminders, init_db, insert_reminder
from app.database.mongodb import create_user_reminder, list_user_reminders

logger = logging.getLogger(__name__)


def create_reminder(
    title: str,
    remind_at: Optional[str] = None,
    time_or_delay: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a reminder for the authenticated user when user_id is provided."""
    if not title or not isinstance(title, str) or not title.strip():
        return {"status": "error", "error": "Parameter 'title' is required and must be a non-empty string."}

    target_time = remind_at or time_or_delay
    if not target_time or not isinstance(target_time, str) or not target_time.strip():
        return {"status": "error", "error": "Parameter 'remind_at' is required and must be a non-empty string."}

    clean_title = title.strip()
    clean_remind_at = target_time.strip()

    try:
        if user_id:
            record = create_user_reminder(user_id=user_id, title=clean_title, remind_at=clean_remind_at)
            logger.info("Persisted MongoDB reminder %s for user %s", record.get("id"), user_id)
        else:
            init_db()
            record = insert_reminder(title=clean_title, remind_at=clean_remind_at)
            logger.info("Persisted legacy SQLite reminder %s", record.get("id"))

        return {
            "status": "success",
            "tool": "create_reminder",
            "reminder": record,
            "message": f"Reminder created: '{record['title']}' for {record['remind_at']}.",
        }
    except sqlite3.Error as db_err:
        logger.error("SQLite database error creating reminder: %s", db_err)
        return {"status": "error", "error": f"Database error creating reminder: {db_err}"}
    except Exception as exc:
        logger.exception("Unexpected error creating reminder: %s", exc)
        return {"status": "error", "error": f"Failed to create reminder: {exc}"}


def list_reminders(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if user_id:
        return list_user_reminders(user_id)
    init_db()
    return get_all_reminders()


def clear_reminders() -> None:
    init_db()
    clear_reminders_table()
