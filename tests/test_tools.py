"""Integration and unit tests for Gemini function calling tools, registry, and executor.

Test categories
---------------
Unit tests (no external resources required)
    test_weather_empty_city_validation
    test_weather_missing_api_configuration
    test_weather_api_failure_timeout
    test_create_reminder_empty_title_returns_error
    test_create_reminder_empty_time_returns_error
    test_search_notes_empty_query_returns_error
    test_search_notes_invalid_limit_returns_error
    test_registry_* (all registry schema tests)
    test_tool_executor_handles_unknown_tool
    test_tool_executor_handles_argument_mismatch

Integration tests  (@pytest.mark.integration)
    Require network access and a valid WEATHER_API_KEY.
    Require GEMINI_API_KEY for Gemini Live session tests.
    May read/write the production assistant.db.
    Run with:  pytest -m integration
    Skip with: pytest -m "not integration"
"""

import asyncio
from typing import Any, Dict
import pytest
from google.genai import types

from app.services.session_manager import VoiceSession
from app.services.tool_executor import ToolExecutor, get_default_tool_executor
from app.tools.notes import add_note, clear_notes, search_notes
from app.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    create_default_registry,
    get_default_registry,
)
from app.tools.reminders import clear_reminders, create_reminder, list_reminders
from app.tools.weather import get_weather


# ==============================================================================
# 1. Weather Tool Tests (Step 9: Real Weather API Integration)
# ==============================================================================

@pytest.mark.integration
def test_weather_valid_city_pune():
    """Test 1: Valid city (Pune) returns real-time live weather metrics."""
    result = get_weather(city="Pune")
    assert result["status"] == "success"
    assert result["city"] == "Pune"
    assert result["country"] == "India"
    assert result["unit"] == "celsius"
    assert isinstance(result["temperature"], (int, float))
    assert result["condition"]
    assert "Pune" in result["description"]


@pytest.mark.integration
def test_weather_valid_city_fahrenheit():
    """Test 1b: Valid city with fahrenheit unit conversion."""
    result = get_weather(city="Tokyo", unit="fahrenheit")
    assert result["status"] == "success"
    assert result["unit"] == "fahrenheit"
    assert isinstance(result["temperature"], (int, float))


@pytest.mark.integration
def test_weather_invalid_unknown_city():
    """Test 2: Invalid / unknown city returns clean structured error."""
    result = get_weather(city="NonExistentCityXYZ99999999")
    assert result["status"] == "error"
    assert "not found" in result["error"].lower()


def test_weather_empty_city_validation():
    """Verify input validation rejects empty city string."""
    result = get_weather(city="   ")
    assert result["status"] == "error"
    assert "city" in result["error"].lower()


def test_weather_api_failure_timeout(monkeypatch):
    """Test 3: API failure / timeout returns structured error without crashing."""
    import httpx

    def mock_get(*args, **kwargs):
        raise httpx.TimeoutException("Connection timed out")

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    result = get_weather(city="London")
    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()


def test_weather_missing_api_configuration(monkeypatch):
    """Test 4: Missing API configuration returns structured error."""
    monkeypatch.setenv("WEATHER_API_KEY", "")
    result = get_weather(city="Pune")
    assert result["status"] == "error"
    assert "WEATHER_API_KEY" in result["error"]


# ==============================================================================
# 2. Reminder Tool Tests (Step 10: SQLite Persistence)
# ==============================================================================

@pytest.mark.integration
def test_create_reminder_sqlite_persistence():
    """Verify create_reminder stores and retrieves structured reminders from SQLite."""
    clear_reminders()
    result = create_reminder(
        title="Study",
        remind_at="tomorrow at 7 PM",
    )
    assert result["status"] == "success"
    rem = result["reminder"]
    assert rem["title"] == "Study"
    assert rem["remind_at"] == "tomorrow at 7 PM"
    assert isinstance(rem["id"], int)
    assert rem["id"] >= 1
    assert "Study" in result["message"]

    all_reminders = list_reminders()
    assert len(all_reminders) == 1
    assert all_reminders[0]["id"] == rem["id"]
    assert all_reminders[0]["title"] == "Study"


def test_create_reminder_empty_title_returns_error():
    """Verify create_reminder rejects empty title."""
    result = create_reminder(title="", remind_at="tomorrow at 7 PM")
    assert result["status"] == "error"
    assert "title" in result["error"].lower()


def test_create_reminder_empty_time_returns_error():
    """Verify create_reminder rejects empty remind_at."""
    result = create_reminder(title="Study", remind_at="   ")
    assert result["status"] == "error"
    assert "remind_at" in result["error"].lower()


# ==============================================================================
# 3. Notes Tool Tests (Step 10: SQLite Parameterized Search)
# ==============================================================================

def test_search_notes_machine_learning():
    """Verify search_notes finds notes matching 'machine learning' from SQLite."""
    result = search_notes(query="machine learning")
    assert result["status"] == "success"
    assert result["count"] >= 1
    assert any("machine learning" in n["title"].lower() or "machine learning" in n["content"].lower() for n in result["notes"])
    ml_note = result["notes"][0]
    assert "title" in ml_note
    assert "content" in ml_note
    assert isinstance(ml_note["id"], int)


def test_search_notes_architecture():
    """Verify search_notes finds notes matching 'architecture'."""
    result = search_notes(query="architecture")
    assert result["status"] == "success"
    assert result["count"] >= 1
    assert any("Architecture" in n["title"] for n in result["notes"])


def test_search_notes_empty_query_returns_error():
    """Verify search_notes rejects empty search query."""
    result = search_notes(query="")
    assert result["status"] == "error"
    assert "query" in result["error"].lower()


def test_search_notes_invalid_limit_returns_error():
    """Verify search_notes rejects non-positive limit."""
    result = search_notes(query="test", limit=-2)
    assert result["status"] == "error"
    assert "limit" in result["error"].lower()


# ==============================================================================
# 4. Tool Registry Tests
# ==============================================================================

def test_registry_contains_three_core_tools():
    """Verify default registry is populated with get_weather, create_reminder, and search_notes."""
    registry = get_default_registry()
    tool_names = [t["name"] for t in registry.list_tools()]

    assert "get_weather" in tool_names
    assert "create_reminder" in tool_names
    assert "search_notes" in tool_names


def test_registry_generates_gemini_tools_and_declarations():
    """Verify registry correctly builds Google GenAI types.Tool with FunctionDeclarations."""
    registry = create_default_registry()
    gemini_tools = registry.get_gemini_tools()

    assert len(gemini_tools) == 1
    tool = gemini_tools[0]
    assert isinstance(tool, types.Tool)
    decls = tool.function_declarations
    assert len(decls) == 3

    decl_names = [d.name for d in decls]
    assert "get_weather" in decl_names
    assert "create_reminder" in decl_names
    assert "search_notes" in decl_names

    # Check that required properties are set on schemas
    weather_decl = next(d for d in decls if d.name == "get_weather")
    assert "city" in weather_decl.parameters.required

    reminder_decl = next(d for d in decls if d.name == "create_reminder")
    assert "remind_at" in reminder_decl.parameters.required


# ==============================================================================
# 5. Tool Executor Tests
# ==============================================================================

@pytest.mark.integration
def test_tool_executor_executes_weather_successfully():
    """Verify ToolExecutor runs get_weather and returns a valid FunctionResponse."""
    executor = get_default_tool_executor()

    response = asyncio.run(
        executor.execute(
            name="get_weather",
            call_id="call_001",
            args={"city": "Paris", "unit": "celsius"},
        )
    )

    assert isinstance(response, types.FunctionResponse)
    assert response.name == "get_weather"
    assert response.id == "call_001"
    res = response.response["result"]
    assert res["status"] == "success"
    assert res["city"] == "Paris"


def test_tool_executor_handles_unknown_tool():
    """Verify ToolExecutor returns structured error for unknown tool."""
    executor = get_default_tool_executor()

    response = asyncio.run(
        executor.execute(
            name="non_existent_tool",
            call_id="call_999",
            args={},
        )
    )

    assert isinstance(response, types.FunctionResponse)
    res = response.response["result"]
    assert res["status"] == "error"
    assert "unknown tool" in res["error"].lower()


def test_tool_executor_handles_argument_mismatch():
    """Verify ToolExecutor handles invalid argument types gracefully without crashing."""
    executor = get_default_tool_executor()

    # Pass unexpected unexpected kwargs that violate signature
    response = asyncio.run(
        executor.execute(
            name="get_weather",
            call_id="call_002",
            args={"unexpected_argument_xyz": 123},
        )
    )

    assert isinstance(response, types.FunctionResponse)
    res = response.response["result"]
    assert res["status"] == "error"


@pytest.mark.integration
def test_tool_executor_batch_execution():
    """Verify ToolExecutor executes multiple calls concurrently."""
    executor = get_default_tool_executor()

    calls = [
        types.FunctionCall(name="get_weather", id="call_w1", args={"city": "Berlin"}),
        types.FunctionCall(name="create_reminder", id="call_r1", args={"title": "Meeting", "remind_at": "at 10am"}),
    ]

    responses = asyncio.run(executor.execute_calls(calls))
    assert len(responses) == 2
    assert responses[0].name == "get_weather"
    assert responses[0].response["result"]["status"] == "success"
    assert responses[1].name == "create_reminder"
    assert responses[1].response["result"]["status"] == "success"


# ==============================================================================
# 6. Session Manager Tool Calling Dispatch Tests
# ==============================================================================

@pytest.mark.integration
def test_session_manager_tool_execution_flow():
    """Verify VoiceSession catches tool_call, dispatches to client, executes, and sends responses."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    class MockGeminiSession:
        def __init__(self):
            self.sent_tool_responses = None

        async def send_tool_response(self, responses):
            self.sent_tool_responses = responses

    mock_ws = MockWebSocket()
    mock_gemini = MockGeminiSession()

    session = VoiceSession(session_id="test_tool_session", websocket=mock_ws)
    session.gemini_session = mock_gemini

    # Simulate Gemini Live sending a tool call
    fc = types.FunctionCall(
        name="get_weather",
        id="call_live_42",
        args={"location": "Sydney", "unit": "celsius"},
    )
    gemini_msg = types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(function_calls=[fc])
    )

    asyncio.run(session._process_gemini_message(gemini_msg))

    # 1. Verify frontend received tool_call notification
    tool_call_events = [m for m in mock_ws.sent_messages if m["type"] == "tool_call"]
    assert len(tool_call_events) == 1
    assert tool_call_events[0]["handled"] is True
    assert tool_call_events[0]["function_calls"][0]["name"] == "get_weather"

    # 2. Verify frontend received tool_result notification
    tool_result_events = [m for m in mock_ws.sent_messages if m["type"] == "tool_result"]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["name"] == "get_weather"
    assert tool_result_events[0]["result"]["status"] == "success"
    assert "Sydney" in tool_result_events[0]["result"]["location"]

    # 3. Verify tool response was returned to Gemini Live session
    assert mock_gemini.sent_tool_responses is not None
    assert len(mock_gemini.sent_tool_responses) == 1
    assert mock_gemini.sent_tool_responses[0].id == "call_live_42"
