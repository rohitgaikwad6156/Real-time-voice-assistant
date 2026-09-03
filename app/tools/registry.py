"""Tool Registry for Gemini Function Calling.

Maintains tool definitions, parameter schemas, and mapping to callable functions.
Generates official Google Gemini Tool declarations for Live API sessions.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from google.genai import types

from app.tools.notes import search_notes
from app.tools.reminders import create_reminder
from app.tools.weather import get_weather


@dataclass
class ToolDefinition:
    """Represents a callable tool with its Gemini schema and metadata."""

    name: str
    description: str
    parameters: types.Schema
    func: Callable[..., Dict[str, Any]]

    def to_function_declaration(self) -> types.FunctionDeclaration:
        """Convert to official Google GenAI FunctionDeclaration."""
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )


class ToolRegistry:
    """Registry holding available tools and their Gemini function declarations."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool definition.

        Args:
            tool_def: ToolDefinition instance.
        """
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Retrieve a tool definition by name.

        Args:
            name: Name of the tool.

        Returns:
            ToolDefinition if found, otherwise None.
        """
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, str]]:
        """Return a summary of all registered tools."""
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
        ]

    def get_function_declarations(self) -> List[types.FunctionDeclaration]:
        """Generate list of FunctionDeclarations for all registered tools."""
        return [tool.to_function_declaration() for tool in self._tools.values()]

    def get_gemini_tools(self) -> List[types.Tool]:
        """Generate official types.Tool list for Gemini Live API configuration."""
        declarations = self.get_function_declarations()
        if not declarations:
            return []
        return [types.Tool(function_declarations=declarations)]


# ==============================================================================
# Parameter Schemas for the 3 Core Assessment Tools
# ==============================================================================

WEATHER_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "city": types.Schema(
            type=types.Type.STRING,
            description="Name of the city, e.g. 'Pune', 'London', 'Tokyo', or 'San Francisco'.",
        ),
        "unit": types.Schema(
            type=types.Type.STRING,
            description="Temperature scale: 'celsius' or 'fahrenheit'.",
            enum=["celsius", "fahrenheit"],
        ),
    },
    required=["city"],
)

REMINDER_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "title": types.Schema(
            type=types.Type.STRING,
            description="Short description or subject of the reminder.",
        ),
        "remind_at": types.Schema(
            type=types.Type.STRING,
            description="Target date and time for the reminder, e.g. 'tomorrow at 7 PM', 'at 5:00 PM', 'in 15 minutes'.",
        ),
    },
    required=["title", "remind_at"],
)

NOTES_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "query": types.Schema(
            type=types.Type.STRING,
            description="Keyword or topic to search for across personal notes.",
        ),
        "limit": types.Schema(
            type=types.Type.INTEGER,
            description="Maximum number of notes to return (default: 5, max: 20).",
        ),
    },
    required=["query"],
)


def create_default_registry() -> ToolRegistry:
    """Create and populate the default registry with the 3 required tools."""
    registry = ToolRegistry()

    # 1. get_weather
    registry.register(
        ToolDefinition(
            name="get_weather",
            description="Retrieve current weather report for a given location.",
            parameters=WEATHER_SCHEMA,
            func=get_weather,
        )
    )

    # 2. create_reminder
    registry.register(
        ToolDefinition(
            name="create_reminder",
            description="Create a new reminder with title, scheduled time or delay, and priority.",
            parameters=REMINDER_SCHEMA,
            func=create_reminder,
        )
    )

    # 3. search_notes
    registry.register(
        ToolDefinition(
            name="search_notes",
            description="Search personal notes by keywords or topic phrases.",
            parameters=NOTES_SCHEMA,
            func=search_notes,
        )
    )

    return registry


# Global default registry instance
default_tool_registry = create_default_registry()


def get_default_registry() -> ToolRegistry:
    """Access the default tool registry."""
    return default_tool_registry
