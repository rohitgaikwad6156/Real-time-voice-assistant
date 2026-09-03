"""SQLite-backed Notes tool implementation.

Provides structured note search functionality with validation and SQLite database queries.
Independent from any specific LLM client.
"""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from app.database import (
    clear_notes_table,
    init_db,
    insert_note,
    search_notes_db,
)

logger = logging.getLogger(__name__)


def search_notes(query: str, limit: int = 5) -> Dict[str, Any]:
    """Search personal notes stored in the SQLite database matching a query keyword or phrase.

    Args:
        query: Keyword or phrase to search for (e.g. "machine learning", "groceries").
        limit: Maximum number of notes to return (default: 5, max: 20).

    Returns:
        Structured dictionary containing matching note records or error details.
    """
    # 1. Validate query
    if not query or not isinstance(query, str) or not query.strip():
        return {
            "status": "error",
            "error": "Parameter 'query' is required and must be a non-empty string.",
        }

    # 2. Validate limit
    try:
        limit_val = int(limit)
        if limit_val <= 0:
            raise ValueError()
        limit_val = min(limit_val, 20)
    except (ValueError, TypeError):
        return {
            "status": "error",
            "error": f"Parameter 'limit' must be a positive integer (got: {limit}).",
        }

    clean_query = query.strip()

    # 3. Query SQLite database
    try:
        init_db()
        matches = search_notes_db(query=clean_query, limit=limit_val)
        logger.info("Found %d note(s) in SQLite matching '%s'.", len(matches), clean_query)

        count = len(matches)
        summary_text = (
            f"No notes found matching '{clean_query}'."
            if count == 0
            else f"Found {count} note(s) matching '{clean_query}'."
        )

        return {
            "status": "success",
            "tool": "search_notes",
            "query": clean_query,
            "count": count,
            "notes": matches,
            "summary": summary_text,
            "message": summary_text,
        }

    except sqlite3.Error as db_err:
        logger.error("SQLite error searching notes: %s", db_err)
        return {
            "status": "error",
            "error": f"Database error searching notes: {db_err}",
        }
    except Exception as exc:
        logger.exception("Unexpected error searching notes: %s", exc)
        return {
            "status": "error",
            "error": f"Failed to search notes: {exc}",
        }


def add_note(title: str, content: str) -> Dict[str, Any]:
    """Helper to insert a note directly into the SQLite database."""
    init_db()
    return insert_note(title=title, content=content)


def clear_notes() -> None:
    """Clear all notes from SQLite (useful for test isolation)."""
    init_db()
    clear_notes_table()
