"""Tools package for Real-Time Voice Assistant.

Exposes tools, registry, and schemas for Gemini function calling.
"""

from app.tools.notes import add_note, clear_notes, search_notes
from app.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    create_default_registry,
    default_tool_registry,
    get_default_registry,
)
from app.tools.reminders import clear_reminders, create_reminder, list_reminders
from app.tools.weather import get_weather

__all__ = [
    "get_weather",
    "create_reminder",
    "list_reminders",
    "clear_reminders",
    "search_notes",
    "add_note",
    "clear_notes",
    "ToolDefinition",
    "ToolRegistry",
    "default_tool_registry",
    "get_default_registry",
    "create_default_registry",
]
