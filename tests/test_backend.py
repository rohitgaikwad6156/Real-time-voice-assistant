"""Backend unit test suite -- Step 15 (cleaned up).

All tests in this module are pure unit tests:
  - No real external API calls are made (weather APIs are fully mocked).
  - No real GEMINI_API_KEY is required.
  - SQLite writes always go to a per-test temporary file, never to assistant.db.

Coverage:
  - /health endpoint
  - Tool registry (registration, lookup, Gemini declarations)
  - Weather tool: input validation (no real API call needed)
  - Weather tool: mocked external HTTP responses
  - Reminder creation (SQLite isolated DB)
  - Reminder input validation
  - Notes search (SQLite DB seeded by init_db)
  - Empty notes query (no matching results)
  - Invalid / mismatched tool arguments
  - Database error simulation (SQLite error path)
  - Database isolation proof

For live-API and production-DB tests see tests/test_tools.py
(marked with @pytest.mark.integration).

Design decisions
----------------
* External weather APIs are fully mocked via monkeypatch / unittest.mock.
* SQLite is redirected via DATABASE_PATH env var; tests never touch assistant.db.
* No GEMINI_API_KEY is needed - a fixture blanks it for every test.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_gemini_key(monkeypatch):
    """Ensure no real Gemini API key leaks into any unit test."""
    monkeypatch.setenv("GEMINI_API_KEY", "")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect DATABASE_PATH to a per-test temporary SQLite file.

    Isolation guarantee
    -------------------
    monkeypatch scopes the DATABASE_PATH override to this single test;
    the env-var is restored automatically after the test completes.
    The temp directory is unique per test invocation, so tests cannot
    share or pollute each other's SQLite state.
    The production assistant.db is never opened or modified by any
    test that uses this fixture.

    Yields the absolute path string of the temporary DB file.
    """
    db_file = str(tmp_path / "test_assistant.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    from app.database import init_db
    init_db(db_path=db_file)
    yield db_file


@pytest.fixture()
def api_client():
    """Return a FastAPI TestClient for the main app."""
    from app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. /health endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """Tests for the GET /health HTTP endpoint."""

    def test_health_returns_200(self, api_client):
        """GET /health must return HTTP 200."""
        assert api_client.get("/health").status_code == 200

    def test_health_returns_ok_status(self, api_client):
        """GET /health body must be {"status": "ok"}."""
        assert api_client.get("/health").json() == {"status": "ok"}

    def test_health_content_type_is_json(self, api_client):
        """GET /health must respond with application/json."""
        resp = api_client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 2. Tool registry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    """Tests for ToolRegistry registration, lookup, and Gemini schema generation."""

    def test_default_registry_has_three_tools(self):
        """Default registry must expose exactly three tools."""
        from app.tools.registry import get_default_registry
        assert len(get_default_registry().list_tools()) == 3

    def test_registry_contains_expected_tool_names(self):
        """All three core tool names must be present."""
        from app.tools.registry import get_default_registry
        names = {t["name"] for t in get_default_registry().list_tools()}
        assert names == {"get_weather", "create_reminder", "search_notes"}

    def test_registry_get_returns_tool_definition(self):
        """registry.get() must return a ToolDefinition for each registered tool."""
        from app.tools.registry import get_default_registry, ToolDefinition
        registry = get_default_registry()
        for name in ("get_weather", "create_reminder", "search_notes"):
            td = registry.get(name)
            assert td is not None, f"Tool '{name}' not found in registry"
            assert isinstance(td, ToolDefinition)
            assert callable(td.func)

    def test_registry_get_unknown_tool_returns_none(self):
        """registry.get() must return None for an unrecognised tool name."""
        from app.tools.registry import get_default_registry
        assert get_default_registry().get("completely_unknown_tool_xyz") is None

    def test_registry_list_tools_has_descriptions(self):
        """Every tool summary returned by list_tools() must have a non-empty description."""
        from app.tools.registry import get_default_registry
        for t in get_default_registry().list_tools():
            assert t.get("description"), f"Tool '{t['name']}' has empty description"

    def test_registry_generates_one_gemini_tool_object(self):
        """get_gemini_tools() must return exactly one types.Tool wrapping all declarations."""
        from google.genai import types
        from app.tools.registry import create_default_registry
        tools = create_default_registry().get_gemini_tools()
        assert len(tools) == 1
        assert isinstance(tools[0], types.Tool)

    def test_registry_function_declarations_count(self):
        """The single types.Tool must contain exactly three FunctionDeclarations."""
        from app.tools.registry import create_default_registry
        decls = create_default_registry().get_gemini_tools()[0].function_declarations
        assert len(decls) == 3

    def test_weather_schema_required_field(self):
        """Weather FunctionDeclaration must mark 'city' as a required parameter."""
        from app.tools.registry import create_default_registry
        decls = create_default_registry().get_gemini_tools()[0].function_declarations
        wd = next(d for d in decls if d.name == "get_weather")
        assert "city" in wd.parameters.required

    def test_reminder_schema_required_fields(self):
        """Reminder FunctionDeclaration must mark 'title' and 'remind_at' as required."""
        from app.tools.registry import create_default_registry
        decls = create_default_registry().get_gemini_tools()[0].function_declarations
        rd = next(d for d in decls if d.name == "create_reminder")
        assert "title" in rd.parameters.required
        assert "remind_at" in rd.parameters.required

    def test_notes_schema_required_field(self):
        """Notes FunctionDeclaration must mark 'query' as a required parameter."""
        from app.tools.registry import create_default_registry
        decls = create_default_registry().get_gemini_tools()[0].function_declarations
        nd = next(d for d in decls if d.name == "search_notes")
        assert "query" in nd.parameters.required

    def test_empty_registry_returns_no_gemini_tools(self):
        """An empty ToolRegistry must return an empty list from get_gemini_tools()."""
        from app.tools.registry import ToolRegistry
        assert ToolRegistry().get_gemini_tools() == []


# ---------------------------------------------------------------------------
# 3. Weather tool -- input validation (no network calls)
# ---------------------------------------------------------------------------

class TestWeatherValidation:
    """Tests for get_weather() input validation -- all validation paths return before HTTP call."""

    def test_missing_api_key_returns_error(self, monkeypatch):
        """When WEATHER_API_KEY is absent the tool must return a structured error."""
        monkeypatch.setenv("WEATHER_API_KEY", "")
        from app.tools.weather import get_weather
        result = get_weather(city="London")
        assert result["status"] == "error"
        assert "WEATHER_API_KEY" in result["error"]

    def test_empty_city_returns_error(self, monkeypatch):
        """An empty or whitespace-only city string must be rejected."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        from app.tools.weather import get_weather
        result = get_weather(city="   ")
        assert result["status"] == "error"
        assert "city" in result["error"].lower()

    def test_none_city_returns_error(self, monkeypatch):
        """Passing city=None must be rejected with a validation error."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        from app.tools.weather import get_weather
        result = get_weather(city=None)
        assert result["status"] == "error"
        assert "city" in result["error"].lower()

    def test_invalid_unit_returns_error(self, monkeypatch):
        """An unrecognised temperature unit must be rejected immediately."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        from app.tools.weather import get_weather
        result = get_weather(city="Paris", unit="kelvin")
        assert result["status"] == "error"
        assert "kelvin" in result["error"].lower() or "unit" in result["error"].lower()

    def test_supported_units_are_celsius_and_fahrenheit(self):
        """SUPPORTED_UNITS must contain 'celsius' and 'fahrenheit'."""
        from app.tools.weather import SUPPORTED_UNITS
        assert "celsius" in SUPPORTED_UNITS
        assert "fahrenheit" in SUPPORTED_UNITS

    def test_unit_none_omitted_defaults_to_celsius(self, monkeypatch):
        """Passing unit=None (omitted) must silently default to celsius without an error.

        This test confirms that the sentinel-default path still works correctly
        after the unit validation fix.
        """
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        geo = {"results": [{"name": "Rome", "latitude": 41.9, "longitude": 12.5, "country": "Italy"}]}
        wx = {"current": {"temperature_2m": 22.0, "relative_humidity_2m": 55, "weather_code": 0, "wind_speed_10m": 8.0}}

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = geo if "geocoding" in url else wx
            return resp

        mc = MagicMock()
        mc.get.side_effect = mock_get
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        with patch("app.tools.weather.httpx.Client", return_value=mc):
            from app.tools.weather import get_weather
            result = get_weather(city="Rome")   # unit omitted -> None sentinel -> celsius
        assert result["status"] == "success"
        assert result["unit"] == "celsius"


    def test_unit_empty_string_rejected(self, monkeypatch):
        """An explicitly supplied empty string for unit must now be rejected.

        Before the fix, unit="" was silently coerced to "celsius" via
        `(unit or "celsius")`.  After the fix, any explicitly supplied
        value that is not in SUPPORTED_UNITS must return an error.
        """
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        from app.tools.weather import get_weather
        result = get_weather(city="London", unit="")
        assert result["status"] == "error"
        assert "unit" in result["error"].lower() or "celsius" in result["error"].lower() or "fahrenheit" in result["error"].lower()


# ---------------------------------------------------------------------------
# 4. Weather tool -- mocked external HTTP API
# ---------------------------------------------------------------------------

class TestWeatherMockedAPI:
    """Tests for get_weather() with the external HTTP API fully mocked."""

    def _make_mock_client(self, geo_data, weather_data):
        """Helper: build a mock httpx.Client that returns preset JSON for GET calls."""
        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = geo_data if "geocoding" in url else weather_data
            return resp

        mc = MagicMock()
        mc.get.side_effect = mock_get
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        return mc

    def test_mocked_successful_weather_celsius(self, monkeypatch):
        """A fully mocked Open-Meteo response must produce a success result."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        geo = {"results": [{"name": "London", "latitude": 51.5, "longitude": -0.12, "country": "United Kingdom"}]}
        wx = {"current": {"temperature_2m": 18.5, "relative_humidity_2m": 72, "weather_code": 2, "wind_speed_10m": 14.0}}
        with patch("app.tools.weather.httpx.Client", return_value=self._make_mock_client(geo, wx)):
            from app.tools.weather import get_weather
            result = get_weather(city="London", unit="celsius")
        assert result["status"] == "success"
        assert result["city"] == "London"
        assert result["temperature"] == 18.5
        assert result["unit"] == "celsius"
        assert result["humidity_percent"] == 72
        assert "London" in result["description"]

    def test_mocked_successful_weather_fahrenheit(self, monkeypatch):
        """Mocked response with fahrenheit unit must be reflected in the result."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        geo = {"results": [{"name": "Tokyo", "latitude": 35.68, "longitude": 139.69, "country": "Japan"}]}
        wx = {"current": {"temperature_2m": 77.0, "relative_humidity_2m": 60, "weather_code": 0, "wind_speed_10m": 10.0}}
        with patch("app.tools.weather.httpx.Client", return_value=self._make_mock_client(geo, wx)):
            from app.tools.weather import get_weather
            result = get_weather(city="Tokyo", unit="fahrenheit")
        assert result["status"] == "success"
        assert result["unit"] == "fahrenheit"
        assert isinstance(result["temperature"], float)

    def test_mocked_city_not_found(self, monkeypatch):
        """When geocoding returns no results the tool must report a not-found error."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        with patch("app.tools.weather.httpx.Client", return_value=self._make_mock_client({}, {})):
            from app.tools.weather import get_weather
            result = get_weather(city="NonExistentCityXYZABC")
        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    def test_mocked_timeout_exception(self, monkeypatch):
        """A TimeoutException from httpx must be caught and returned as a structured error."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        mc = MagicMock()
        mc.get.side_effect = httpx.TimeoutException("timed out")
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        with patch("app.tools.weather.httpx.Client", return_value=mc):
            from app.tools.weather import get_weather
            result = get_weather(city="Berlin")
        assert result["status"] == "error"
        assert "timed out" in result["error"].lower()

    def test_mocked_http_404_status_error(self, monkeypatch):
        """An HTTP 404 response must be caught and returned as a structured error."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        fake_resp = MagicMock()
        fake_resp.status_code = 404
        err = httpx.HTTPStatusError("404", request=MagicMock(), response=fake_resp)
        mc = MagicMock()
        mc.get.side_effect = err
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        with patch("app.tools.weather.httpx.Client", return_value=mc):
            from app.tools.weather import get_weather
            result = get_weather(city="SomeCity")
        assert result["status"] == "error"
        assert "404" in result["error"]

    def test_mocked_network_request_error(self, monkeypatch):
        """A network-level RequestError from httpx must be handled gracefully."""
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        monkeypatch.setenv("WEATHER_API_PROVIDER", "open-meteo")
        mc = MagicMock()
        mc.get.side_effect = httpx.RequestError("connection refused")
        mc.__enter__ = MagicMock(return_value=mc)
        mc.__exit__ = MagicMock(return_value=False)
        with patch("app.tools.weather.httpx.Client", return_value=mc):
            from app.tools.weather import get_weather
            result = get_weather(city="Paris")
        assert result["status"] == "error"
        assert "connection" in result["error"].lower() or "failed" in result["error"].lower()


# ---------------------------------------------------------------------------
# 5. Reminder creation
# ---------------------------------------------------------------------------

class TestReminderCreation:
    """Tests for create_reminder() against an isolated SQLite database."""

    def test_create_reminder_returns_success(self, tmp_db):
        """A valid reminder must return status success."""
        from app.tools.reminders import clear_reminders, create_reminder
        clear_reminders()
        result = create_reminder(title="Doctor appointment", remind_at="tomorrow at 9 AM")
        assert result["status"] == "success"

    def test_create_reminder_has_correct_fields(self, tmp_db):
        """The returned reminder must contain the correct title and remind_at."""
        from app.tools.reminders import clear_reminders, create_reminder
        clear_reminders()
        result = create_reminder(title="Team standup", remind_at="every weekday at 10 AM")
        assert result["reminder"]["title"] == "Team standup"
        assert result["reminder"]["remind_at"] == "every weekday at 10 AM"

    def test_create_reminder_assigns_integer_id(self, tmp_db):
        """The created reminder must receive a positive integer id from SQLite."""
        from app.tools.reminders import clear_reminders, create_reminder
        clear_reminders()
        result = create_reminder(title="Buy groceries", remind_at="in 30 minutes")
        assert isinstance(result["reminder"]["id"], int)
        assert result["reminder"]["id"] >= 1

    def test_create_reminder_message_contains_title(self, tmp_db):
        """The response message must reference the reminder title."""
        from app.tools.reminders import clear_reminders, create_reminder
        clear_reminders()
        result = create_reminder(title="Call mom", remind_at="at 6 PM")
        assert "Call mom" in result["message"]

    def test_multiple_reminders_get_unique_ids(self, tmp_db):
        """Creating multiple reminders must produce unique ids."""
        from app.tools.reminders import clear_reminders, create_reminder
        clear_reminders()
        r1 = create_reminder(title="First", remind_at="in 5 minutes")
        r2 = create_reminder(title="Second", remind_at="in 10 minutes")
        assert r1["reminder"]["id"] != r2["reminder"]["id"]

    def test_created_reminder_persists_in_db(self, tmp_db):
        """After creation a reminder must be retrievable from the database."""
        from app.tools.reminders import clear_reminders, create_reminder, list_reminders
        clear_reminders()
        create_reminder(title="Meeting notes", remind_at="Friday at 3 PM")
        all_reminders = list_reminders()
        assert len(all_reminders) == 1
        assert all_reminders[0]["title"] == "Meeting notes"


# ---------------------------------------------------------------------------
# 6. Reminder input validation
# ---------------------------------------------------------------------------

class TestReminderValidation:
    """Tests for create_reminder() input validation."""

    def test_empty_title_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="", remind_at="tomorrow at 9 AM")
        assert result["status"] == "error"
        assert "title" in result["error"].lower()

    def test_whitespace_title_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="   ", remind_at="tomorrow at 9 AM")
        assert result["status"] == "error"
        assert "title" in result["error"].lower()

    def test_empty_remind_at_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="Study Python", remind_at="")
        assert result["status"] == "error"
        assert "remind_at" in result["error"].lower()

    def test_whitespace_remind_at_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="Study Python", remind_at="   ")
        assert result["status"] == "error"
        assert "remind_at" in result["error"].lower()

    def test_none_title_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title=None, remind_at="at 5 PM")
        assert result["status"] == "error"
        assert "title" in result["error"].lower()

    def test_none_remind_at_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="Valid title", remind_at=None)
        assert result["status"] == "error"
        assert "remind_at" in result["error"].lower()

    def test_both_empty_fields_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="", remind_at="")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# 7. Notes search
# ---------------------------------------------------------------------------

class TestNotesSearch:
    """Tests for search_notes() against a temporary SQLite DB seeded by init_db()."""

    def test_search_notes_finds_seeded_machine_learning_note(self, tmp_db):
        """Searching 'machine learning' must find at least one seeded note."""
        from app.tools.notes import search_notes
        result = search_notes(query="machine learning")
        assert result["status"] == "success"
        assert result["count"] >= 1
        assert any(
            "machine learning" in n["title"].lower() or "machine learning" in n["content"].lower()
            for n in result["notes"]
        )

    def test_search_notes_result_has_required_fields(self, tmp_db):
        """Each note must contain id, title, content, and created_at fields."""
        from app.tools.notes import search_notes
        result = search_notes(query="machine learning")
        assert result["count"] >= 1
        note = result["notes"][0]
        for field in ("id", "title", "content", "created_at"):
            assert field in note

    def test_search_notes_finds_grocery_note(self, tmp_db):
        """Searching 'grocery' must find the seeded weekly grocery list note."""
        from app.tools.notes import search_notes
        result = search_notes(query="grocery")
        assert result["status"] == "success"
        assert result["count"] >= 1

    def test_search_notes_limit_is_respected(self, tmp_db):
        """Setting limit=1 must return at most one note."""
        from app.tools.notes import search_notes
        result = search_notes(query="a", limit=1)
        assert result["status"] == "success"
        assert len(result["notes"]) <= 1

    def test_search_notes_limit_capped_at_20(self, tmp_db):
        """Setting limit=999 must be capped to 20 results maximum."""
        from app.tools.notes import add_note, search_notes
        for i in range(25):
            add_note(title=f"Bulk note {i}", content="captest_unique_kw")
        result = search_notes(query="captest_unique_kw", limit=999)
        assert result["status"] == "success"
        assert len(result["notes"]) <= 20

    def test_search_notes_custom_note_is_found(self, tmp_db):
        """A note inserted via add_note() must be retrievable via search_notes()."""
        from app.tools.notes import add_note, search_notes
        add_note(title="Unit Testing Guide", content="How to write effective pytest tests")
        result = search_notes(query="pytest")
        assert result["status"] == "success"
        assert result["count"] >= 1
        assert any("pytest" in n["content"].lower() for n in result["notes"])

    def test_search_notes_summary_message_is_present(self, tmp_db):
        """The result must include a non-empty human-readable summary and message."""
        from app.tools.notes import search_notes
        result = search_notes(query="machine learning")
        assert result.get("summary")
        assert result.get("message")


# ---------------------------------------------------------------------------
# 8. Empty notes query (no matching results)
# ---------------------------------------------------------------------------

class TestEmptyNotesQuery:
    """Tests for search_notes() when no notes match the query."""

    def test_no_match_returns_success_with_zero_count(self, tmp_db):
        """A query that matches nothing must return status=success with count=0."""
        from app.tools.notes import search_notes
        result = search_notes(query="zxqjvbwmthisquerywillneverexist99999")
        assert result["status"] == "success"
        assert result["count"] == 0

    def test_no_match_returns_empty_notes_list(self, tmp_db):
        """When no notes match the query the notes list must be empty."""
        from app.tools.notes import search_notes
        result = search_notes(query="zxqjvbwmthisquerywillneverexist99999")
        assert result["notes"] == []

    def test_no_match_summary_reports_not_found(self, tmp_db):
        """The summary text must indicate that no matching notes were found."""
        from app.tools.notes import search_notes
        result = search_notes(query="zxqjvbwmthisquerywillneverexist99999")
        assert "No notes found matching" in result["summary"]

    def test_no_match_summary_includes_query_term(self, tmp_db):
        """The not-found summary must echo the original search term."""
        from app.tools.notes import search_notes
        query = "xyzUnmatchableQuery42"
        result = search_notes(query=query)
        assert query in result["summary"]

    def test_empty_db_returns_zero_results(self, tmp_path, monkeypatch):
        """When the notes table is completely empty, any query must return count=0."""
        db_file = str(tmp_path / "empty_notes.db")
        monkeypatch.setenv("DATABASE_PATH", db_file)
        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notes "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
            " content TEXT NOT NULL, created_at TEXT NOT NULL);"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS reminders "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,"
            " remind_at TEXT NOT NULL, created_at TEXT NOT NULL);"
        )
        conn.commit()
        conn.close()
        from app.tools.notes import search_notes
        result = search_notes(query="anything")
        assert result["status"] == "success"
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# 9. Invalid tool arguments
# ---------------------------------------------------------------------------

class TestInvalidToolArguments:
    """Tests for all three tools with invalid argument types or values."""

    # search_notes
    def test_search_notes_empty_query_returns_error(self):
        from app.tools.notes import search_notes
        result = search_notes(query="")
        assert result["status"] == "error"
        assert "query" in result["error"].lower()

    def test_search_notes_whitespace_query_returns_error(self):
        from app.tools.notes import search_notes
        result = search_notes(query="   ")
        assert result["status"] == "error"
        assert "query" in result["error"].lower()

    def test_search_notes_zero_limit_returns_error(self):
        from app.tools.notes import search_notes
        result = search_notes(query="test", limit=0)
        assert result["status"] == "error"
        assert "limit" in result["error"].lower()

    def test_search_notes_negative_limit_returns_error(self):
        from app.tools.notes import search_notes
        result = search_notes(query="test", limit=-5)
        assert result["status"] == "error"
        assert "limit" in result["error"].lower()

    def test_search_notes_string_limit_not_parseable_returns_error(self):
        from app.tools.notes import search_notes
        result = search_notes(query="test", limit="not_a_number")
        assert result["status"] == "error"
        assert "limit" in result["error"].lower()

    # create_reminder
    def test_create_reminder_integer_title_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title=42, remind_at="tomorrow")
        assert result["status"] == "error"
        assert "title" in result["error"].lower()

    def test_create_reminder_list_remind_at_rejected(self):
        from app.tools.reminders import create_reminder
        result = create_reminder(title="Valid title", remind_at=["tomorrow"])
        assert result["status"] == "error"
        assert "remind_at" in result["error"].lower()

    # get_weather
    def test_weather_integer_city_rejected(self, monkeypatch):
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        from app.tools.weather import get_weather
        result = get_weather(city=12345)
        assert result["status"] == "error"

    def test_weather_unsupported_unit_rejected(self, monkeypatch):
        """Any unit value not in SUPPORTED_UNITS must be rejected.

        After the validation fix, unit="" is no longer silently coerced
        to "celsius" -- it is treated as an invalid explicitly-supplied
        value and returns a structured error.
        """
        monkeypatch.setenv("WEATHER_API_KEY", "test-key")
        from app.tools.weather import get_weather
        for bad_unit in ("kelvin", "rankine", "", "CELSIUS_WRONG"):
            result = get_weather(city="London", unit=bad_unit)
            assert result["status"] == "error", (
                f"Expected error for unit={bad_unit!r} but got {result['status']!r}"
            )


# ---------------------------------------------------------------------------
# 10. Database error simulation
# ---------------------------------------------------------------------------

class TestDatabaseErrors:
    """Tests verifying graceful handling of sqlite3.Error exceptions."""

    def test_reminder_creation_handles_db_write_error(self):
        """If insert_reminder raises sqlite3.Error it must surface as a structured error."""
        def mock_insert(**kwargs):
            raise sqlite3.Error("Simulated disk I/O failure")
        with patch("app.tools.reminders.insert_reminder", side_effect=mock_insert):
            from app.tools.reminders import create_reminder
            result = create_reminder(title="Backup job", remind_at="at midnight")
        assert result["status"] == "error"
        assert "database error" in result["error"].lower() or "reminder" in result["error"].lower()

    def test_notes_search_handles_db_read_error(self):
        """If search_notes_db raises sqlite3.Error it must surface as a structured error."""
        def mock_search(**kwargs):
            raise sqlite3.Error("Simulated read failure")
        with patch("app.tools.notes.search_notes_db", side_effect=mock_search):
            from app.tools.notes import search_notes
            result = search_notes(query="machine learning")
        assert result["status"] == "error"
        assert "database error" in result["error"].lower()

    def test_reminder_creation_handles_init_db_error(self):
        """If init_db() raises sqlite3.Error it must produce a structured error."""
        def mock_init(*args, **kwargs):
            raise sqlite3.Error("Simulated init failure")
        with patch("app.tools.reminders.init_db", side_effect=mock_init):
            from app.tools.reminders import create_reminder
            result = create_reminder(title="Backup reminder", remind_at="at 2 AM")
        assert result["status"] == "error"

    def test_notes_search_handles_init_db_error(self):
        """If init_db() raises sqlite3.Error it must produce a structured error."""
        def mock_init(*args, **kwargs):
            raise sqlite3.Error("Simulated init failure on notes")
        with patch("app.tools.notes.init_db", side_effect=mock_init):
            from app.tools.notes import search_notes
            result = search_notes(query="any query")
        assert result["status"] == "error"

    def test_insert_to_directory_path_produces_error(self, tmp_path, monkeypatch):
        """Pointing DATABASE_PATH to a directory causes SQLite to fail gracefully."""
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path))  # tmp_path is a dir, not a file
        from app.tools.reminders import create_reminder
        result = create_reminder(title="Will fail", remind_at="never")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# 11. Database isolation proof
# ---------------------------------------------------------------------------

class TestDatabaseIsolation:
    """Proves that unit tests using tmp_db never touch the production assistant.db."""

    def test_tmp_db_is_separate_file_from_assistant_db(self, tmp_db):
        """The tmp_db fixture must point to a path that is NOT assistant.db."""
        import os
        production_db = os.path.abspath("assistant.db")
        assert os.path.abspath(tmp_db) != production_db, (
            "tmp_db points at the production assistant.db -- isolation is broken!"
        )

    def test_production_db_not_modified_by_reminder_write(self, tmp_db):
        """Writing a reminder via create_reminder must not modify assistant.db.

        We record the mtime of assistant.db before the write, perform the write
        (which goes to tmp_db), then assert the mtime is unchanged.
        """
        import os
        from app.tools.reminders import clear_reminders, create_reminder

        production_db = os.path.abspath("assistant.db")
        # Note the production DB mtime before the test operation.
        before_mtime = os.path.getmtime(production_db) if os.path.exists(production_db) else None

        clear_reminders()
        result = create_reminder(title="Isolation test reminder", remind_at="in 1 hour")
        assert result["status"] == "success"

        after_mtime = os.path.getmtime(production_db) if os.path.exists(production_db) else None
        assert before_mtime == after_mtime, (
            "assistant.db mtime changed during a test that should only write to tmp_db!"
        )

    def test_each_tmp_db_invocation_is_independent(self, tmp_path, monkeypatch):
        """Two separate tmp_path values must never refer to the same directory.

        This test directly exercises the pytest tmp_path guarantee to confirm
        that successive tests cannot cross-contaminate each other's databases.
        """
        import os
        db_file = str(tmp_path / "test_isolation.db")
        monkeypatch.setenv("DATABASE_PATH", db_file)

        from app.database import init_db
        from app.tools.reminders import clear_reminders, create_reminder, list_reminders

        init_db(db_path=db_file)
        clear_reminders()

        create_reminder(title="Only in this test", remind_at="at noon")
        reminders = list_reminders()
        # Exactly one reminder exists because we started from a fresh, empty DB.
        assert len(reminders) == 1
        assert reminders[0]["title"] == "Only in this test"


class TestOpenAILazyInitialization:
    """Tests confirming the application starts up safely without OPENAI_API_KEY."""

    def test_openai_client_lazy_error_on_call(self, monkeypatch):
        """Accessing client methods without OPENAI_API_KEY raises clean RuntimeError."""
        import app.services.openai_client as oac
        monkeypatch.setenv("OPENAI_API_KEY", "")
        oac._client = None

        with pytest.raises(RuntimeError, match="OPENAI_API_KEY is missing"):
            _ = oac.client.audio

    def test_text_pipeline_returns_503_when_openai_key_missing(self, monkeypatch):
        """POST /api/text returns HTTP 503 with helpful message when OPENAI_API_KEY is missing."""
        import app.services.openai_client as oac
        monkeypatch.setenv("OPENAI_API_KEY", "")
        oac._client = None

        from app.main import app
        client = TestClient(app)
        response = client.post("/api/text", json={"text": "Hello"})
        assert response.status_code == 503
        assert "OPENAI_API_KEY is missing" in response.json()["detail"]


class TestCORSConfiguration:
    """Verify CORS headers for Vercel, Render, and local development origins."""

    def test_cors_preflight_for_vercel_origin(self):
        """OPTIONS preflight from a Vercel domain receives allowed origin and methods."""
        from app.main import app
        client = TestClient(app)
        origin = "https://voice-assistant-demo.vercel.app"
        response = client.options(
            "/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

    def test_cors_get_for_vercel_origin(self):
        """GET request with Vercel Origin header receives Access-Control-Allow-Origin."""
        from app.main import app
        client = TestClient(app)
        origin = "https://real-time-voice-frontend.vercel.app"
        response = client.get("/health", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

    def test_cors_for_localhost_origin(self):
        """Localhost origin is permitted for local frontend development."""
        from app.main import app
        client = TestClient(app)
        origin = "http://localhost:3000"
        response = client.get("/health", headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin



