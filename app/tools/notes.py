"""User-scoped MongoDB note search with a legacy SQLite fallback for local tests."""

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from app.database import (
    clear_notes_table,
    init_db,
    insert_note,
    search_notes_db,
)
from app.database.mongodb import search_user_notes

logger = logging.getLogger(__name__)


def search_notes(query: str, limit: int = 5, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Search authenticated MongoDB notes, or legacy SQLite notes without a user id.

    Args:
        query: Keyword or phrase to search for (e.g. "machine learning", "groceries").
        limit: Maximum number of notes to return (default: 5, max: 20).
        user_id: Authenticated owner id. When omitted, use the local SQLite fallback.

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

    # 3. Query the authenticated store or the isolated legacy fallback.
    try:
        if user_id:
            matches = search_user_notes(user_id=user_id, query=clean_query, limit=limit_val)
            logger.info("Found %d MongoDB note(s) for authenticated user.", len(matches))
        else:
            init_db()
            matches = search_notes_db(query=clean_query, limit=limit_val)
            logger.info("Found %d note(s) in legacy SQLite search.", len(matches))

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
