"""Reliability and failure mode test suite (Step 14).

Tests coverage:
- Error message sanitization & secret masking
- Zero note search results
- Duplicate tool call deduplication (ToolExecutor & VoiceSession)
- WebSocket malformed JSON, empty payload, unsupported action handling
- Clean session shutdown and resource reclamation
"""

import json
import os
import pytest
from fastapi.testclient import TestClient
from google.genai import types

from app.main import app
from app.services.session_manager import (
    SessionManager,
    VoiceSession,
    sanitize_error_message,
    session_manager,
)
from app.services.tool_executor import ToolExecutor
from app.tools.notes import search_notes
from app.tools.registry import ToolRegistry, ToolDefinition


# ==============================================================================
# 1. Error Sanitization Tests (No Secrets in Logs or Messages)
# ==============================================================================

def test_sanitize_error_message_masks_api_keys(monkeypatch):
    """Verify secrets and API keys are redacted from user-facing error messages."""
    fake_gemini_key = "AIzaSyD-TESTINGKEY1234567890abcdefghij"
    fake_weather_key = "1234567890abcdef1234567890abcdef"
    monkeypatch.setenv("GEMINI_API_KEY", fake_gemini_key)
    monkeypatch.setenv("WEATHER_API_KEY", fake_weather_key)

    raw_error = f"API failed with key {fake_gemini_key} and weather auth {fake_weather_key}"
    sanitized = sanitize_error_message(raw_error)

    assert fake_gemini_key not in sanitized
    assert fake_weather_key not in sanitized
    assert "[REDACTED" in sanitized


def test_sanitize_error_message_preserves_safe_text():
    """Verify normal descriptive error messages are preserved unchanged."""
    safe_msg = "Parameter 'city' is required and must be a non-empty string."
    assert sanitize_error_message(safe_msg) == safe_msg


# ==============================================================================
# 2. Tool Reliability (No Matching Notes & Error Handling)
# ==============================================================================

def test_search_notes_returns_clean_zero_results_message():
    """Verify searching for non-existent notes returns clear feedback."""
    res = search_notes("nonexistent_random_query_xyz_9999")
    assert res["status"] == "success"
    assert res["count"] == 0
    assert "No notes found matching" in res["summary"]
    assert "nonexistent_random_query_xyz_9999" in res["summary"]


# ==============================================================================
# 3. Tool Deduplication Protection
# ==============================================================================

@pytest.mark.anyio
async def test_tool_executor_deduplicates_identical_call_ids():
    """Verify ToolExecutor skips executing duplicate calls sharing identical call_id."""
    call_counts = {"count": 0}

    def dummy_tool(arg: str = "val"):
        call_counts["count"] += 1
        return {"status": "success", "arg": arg}

    custom_registry = ToolRegistry()
    custom_registry.register(
        ToolDefinition(
            name="dummy_tool",
            description="Test tool",
            parameters={"type": "object", "properties": {"arg": {"type": "string"}}},
            func=dummy_tool,
        )
    )

    executor = ToolExecutor(registry=custom_registry)

    # 3 calls with same call_id "call_unique_1"
    calls = [
        types.FunctionCall(name="dummy_tool", id="call_unique_1", args={"arg": "first"}),
        types.FunctionCall(name="dummy_tool", id="call_unique_1", args={"arg": "second"}),
        types.FunctionCall(name="dummy_tool", id="call_different_2", args={"arg": "third"}),
    ]

    responses = await executor.execute_calls(calls)
    assert len(responses) == 2  # Only unique call_ids executed
    assert call_counts["count"] == 2


@pytest.mark.anyio
async def test_voice_session_prevents_duplicate_tool_calls_across_messages():
    """Verify VoiceSession ignores already executed call_ids across message stream."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    mock_ws = MockWebSocket()
    session = VoiceSession(session_id="test_dup_sess", websocket=mock_ws)

    fc = types.FunctionCall(name="get_weather", id="call_fixed_123", args={"city": "Pune"})
    msg1 = types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[fc]))
    msg2 = types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[fc]))

    await session._process_gemini_message(msg1)
    first_count = len(mock_ws.sent_messages)
    assert first_count > 0

    # Second dispatch with same call_id should be ignored
    await session._process_gemini_message(msg2)
    assert len(mock_ws.sent_messages) == first_count


# ==============================================================================
# 4. WebSocket Fault Tolerance
# ==============================================================================

def test_websocket_handles_malformed_json_gracefully():
    """Verify WebSocket sends error response on invalid JSON and does not disconnect."""
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as websocket:
        init_resp = websocket.receive_json()
        assert init_resp["status"] == "connected"

        # Send raw invalid JSON string
        websocket.send_text("THIS IS NOT JSON {{{}}")
        error_resp = websocket.receive_json()
        assert error_resp["type"] == "status"
        assert error_resp["status"] == "error"
        assert "Malformed JSON" in error_resp["message"]

        # WebSocket connection remains open and functional
        websocket.send_text(json.dumps({"type": "ping"}))
        pong_resp = websocket.receive_json()
        assert pong_resp["type"] == "status"
        assert pong_resp["status"] == "pong"


def test_websocket_handles_empty_text_gracefully():
    """Verify sending empty text returns error response without crashing."""
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as websocket:
        websocket.receive_json()  # Handshake

        websocket.send_text(json.dumps({"type": "text", "text": "   "}))
        resp = websocket.receive_json()
        assert resp["type"] == "status"
        assert resp["status"] == "error"
        assert "Empty text" in resp["message"]


def test_websocket_handles_unsupported_action_gracefully():
    """Verify unsupported action returns descriptive error."""
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as websocket:
        websocket.receive_json()

        websocket.send_text(json.dumps({"type": "non_existent_action_xyz"}))
        resp = websocket.receive_json()
        assert resp["type"] == "status"
        assert resp["status"] == "error"
        assert "Unsupported message type" in resp["message"]


# ==============================================================================
# 5. Clean Session Lifecycle & Teardown
# ==============================================================================

@pytest.mark.anyio
async def test_session_manager_clean_teardown():
    """Verify session closing cancels background tasks and marks session inactive."""
    class MockWebSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, p):
            self.sent.append(p)

    manager = SessionManager()
    session = manager.create_session(MockWebSocket())
    sess_id = session.session_id

    assert manager.active_session_count == 1
    assert session.is_active is True

    removed = await manager.remove_session(sess_id)
    assert removed is session
    assert session.is_active is False
    assert manager.active_session_count == 0
