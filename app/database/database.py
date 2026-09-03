"""SQLite database operations for Real-Time Voice Assistant.

Manages persistent SQLite storage for reminders and notes using safe parameterized SQL.
"""

from datetime import datetime, timezone
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

from app.database.models import NoteRecord, ReminderRecord

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "assistant.db"

# Seed data for notes (included for development and test scenarios)
INITIAL_SEED_NOTES = [
    {
        "title": "Machine Learning Concepts",
        "content": "Machine learning revolves around training models on data. Key areas include supervised learning, gradient descent, transformers, and model evaluation.",
    },
    {
        "title": "Voice Assistant Architecture",
        "content": "Built with FastAPI WebSockets, Web Audio API streaming (16 kHz PCM up, 24 kHz down), Google Gemini Live API, and SQLite tools.",
    },
    {
        "title": "Weekly Grocery List",
        "content": "Organic milk, free-range eggs, avocados, sourdough bread, cold brew, and fresh apples.",
    },
    {
        "title": "Recommended Reading",
        "content": "Designing Data-Intensive Applications by Martin Kleppmann, and Clean Architecture by Robert C. Martin.",
    },
]


def get_db_path(custom_path: Optional[str] = None) -> str:
    """Resolve database path from custom argument, environment, or default."""
    if custom_path:
        return custom_path
    return os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Establish and configure a thread-safe connection to SQLite."""
    path = get_db_path(db_path)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite database tables and seed initial development notes if empty."""
    path = get_db_path(db_path)
    logger.info("Initializing SQLite database at '%s'...", path)

    conn = get_connection(path)
    try:
        with conn:
            # 1. Create Reminders Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            # 2. Create Notes Table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            # 3. Seed notes if table is currently empty
            cursor = conn.execute("SELECT COUNT(*) FROM notes;")
            count = cursor.fetchone()[0]
            if count == 0:
                logger.info("Seeding initial development notes into SQLite...")
                now_str = datetime.now(timezone.utc).isoformat()
                for note in INITIAL_SEED_NOTES:
                    conn.execute(
                        "INSERT INTO notes (title, content, created_at) VALUES (?, ?, ?);",
                        (note["title"], note["content"], now_str),
                    )
    except sqlite3.Error as err:
        logger.error("SQLite initialization error: %s", err)
        raise
    finally:
        conn.close()


# ==============================================================================
# Reminders Operations
# ==============================================================================

def insert_reminder(
    title: str,
    remind_at: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new reminder record into the SQLite reminders table using parameterized SQL.

    Args:
        title: Description or subject of the reminder.
        remind_at: Scheduled date/time string.
        db_path: Optional path to SQLite file.

    Returns:
        Dictionary representation of the created ReminderRecord.
    """
    conn = get_connection(db_path)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO reminders (title, remind_at, created_at)
                VALUES (?, ?, ?);
                """,
                (title.strip(), remind_at.strip(), created_at),
            )
            row_id = cursor.lastrowid
            if row_id is None:
                raise sqlite3.Error("Failed to obtain last inserted rowid.")

            record = ReminderRecord(
                id=int(row_id),
                title=title.strip(),
                remind_at=remind_at.strip(),
                created_at=created_at,
            )
            logger.info("Inserted reminder #%d: '%s' for %s", record.id, record.title, record.remind_at)
            return record.to_dict()

    except sqlite3.Error as exc:
        logger.error("SQLite error inserting reminder: %s", exc)
        raise
    finally:
        conn.close()


def get_all_reminders(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all reminders from SQLite ordered by creation time."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT id, title, remind_at, created_at FROM reminders ORDER BY id ASC;")
        rows = cursor.fetchall()
        return [
            ReminderRecord(
                id=row["id"],
                title=row["title"],
                remind_at=row["remind_at"],
                created_at=row["created_at"],
            ).to_dict()
            for row in rows
        ]
    finally:
        conn.close()


def clear_reminders_table(db_path: Optional[str] = None) -> None:
    """Clear all records from reminders table (useful for test isolation)."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM reminders;")
    finally:
        conn.close()


# ==============================================================================
# Notes Operations
# ==============================================================================

def insert_note(
    title: str,
    content: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new note record into the SQLite notes table using parameterized SQL."""
    conn = get_connection(db_path)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO notes (title, content, created_at)
                VALUES (?, ?, ?);
                """,
                (title.strip(), content.strip(), created_at),
            )
            row_id = cursor.lastrowid
            if row_id is None:
                raise sqlite3.Error("Failed to obtain last inserted rowid.")

            record = NoteRecord(
                id=int(row_id),
                title=title.strip(),
                content=content.strip(),
                created_at=created_at,
            )
            logger.info("Inserted note #%d: '%s'", record.id, record.title)
            return record.to_dict()

    except sqlite3.Error as exc:
        logger.error("SQLite error inserting note: %s", exc)
        raise
    finally:
        conn.close()


def search_notes_db(
    query: str,
    limit: int = 5,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search notes in SQLite matching query in title or content using parameterized SQL.

    Args:
        query: Keyword or phrase to search for.
        limit: Maximum number of notes to return.
        db_path: Optional path to SQLite file.

    Returns:
        List of matching NoteRecord dictionaries.
    """
    conn = get_connection(db_path)
    search_param = f"%{query.strip()}%"

    try:
        cursor = conn.execute(
            """
            SELECT id, title, content, created_at
            FROM notes
            WHERE title LIKE ? OR content LIKE ?
            ORDER BY id DESC
            LIMIT ?;
            """,
            (search_param, search_param, limit),
        )
        rows = cursor.fetchall()
        return [
            NoteRecord(
                id=row["id"],
                title=row["title"],
                content=row["content"],
                created_at=row["created_at"],
            ).to_dict()
            for row in rows
        ]
    except sqlite3.Error as exc:
        logger.error("SQLite error searching notes: %s", exc)
        raise
    finally:
        conn.close()


def clear_notes_table(db_path: Optional[str] = None) -> None:
    """Clear all records from notes table (useful for test isolation)."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM notes;")
    finally:
        conn.close()
