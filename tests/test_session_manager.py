"""Tests for /ws/voice WebSocket lifecycle, session manager, and Gemini Live event streaming."""

import asyncio
import base64
import pytest
from fastapi.testclient import TestClient
from google.genai import types

import json

from app.main import app
from app.services.session_manager import VoiceSession, session_manager, validate_message


def test_validate_message_valid():
    """Verify valid JSON messages parse correctly."""
    valid_payload = '{"type": "ping"}'
    parsed = validate_message(valid_payload)
    assert parsed == {"type": "ping"}


def test_validate_message_malformed_json():
    """Verify malformed JSON raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        validate_message("invalid json content")
    assert "Malformed JSON" in str(exc_info.value)


def test_validate_message_missing_type():
    """Verify JSON without 'type' raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        validate_message('{"data": 123}')
    assert "missing or invalid 'type' field" in str(exc_info.value)


def test_websocket_connection_and_disconnect():
    """Test WebSocket connection establishment, initial status, and clean disconnect."""
    client = TestClient(app)
    initial_sessions = session_manager.active_session_count

    with client.websocket_connect("/ws/voice") as ws:
        assert session_manager.active_session_count == initial_sessions + 1

        data = ws.receive_json()
        assert data["type"] == "status"
        assert data["status"] == "connected"
        assert "session_id" in data

    assert session_manager.active_session_count == initial_sessions


def test_websocket_ping_pong():
    """Test ping message yields pong status."""
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        ws.send_json({"type": "ping"})
        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["status"] == "pong"


def test_websocket_start_and_stop_audio_lifecycle():
    """Test start_audio and stop_audio control messages."""
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        ws.send_json({"type": "start_audio"})
        start_res = ws.receive_json()
        assert start_res["type"] == "status"
        assert start_res["status"] == "streaming"

        ws.send_json({"type": "stop_audio"})
        stop_res = ws.receive_json()
        assert stop_res["type"] == "status"
        assert stop_res["status"] == "stopped"
        assert stop_res["chunks"] == 0
        assert stop_res["bytes"] == 0


def test_websocket_binary_audio_missing_gemini_key(monkeypatch):
    """Test streaming binary audio when GEMINI_API_KEY is missing yields structured error."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        fake_pcm = b"\x00\x01" * 320
        ws.send_bytes(fake_pcm)

        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["status"] == "error"
        assert "GEMINI_API_KEY is missing" in response["message"]


def test_websocket_base64_audio_missing_gemini_key(monkeypatch):
    """Test streaming base64 JSON audio chunk when GEMINI_API_KEY is missing yields structured error."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        fake_pcm = b"\x00\x02" * 160
        b64_data = base64.b64encode(fake_pcm).decode("utf-8")
        ws.send_json({"type": "audio", "data": b64_data})

        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["status"] == "error"
        assert "GEMINI_API_KEY is missing" in response["message"]


def test_websocket_invalid_message_handling():
    """Test sending invalid non-JSON string returns structured error status."""
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        ws.send_text("this is not a json payload")
        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["status"] == "error"
        assert "Malformed JSON" in response["message"]


def test_websocket_unsupported_message_type():
    """Test sending unknown message type returns structured error status."""
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        ws.send_json({"type": "unknown_action"})
        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["status"] == "error"
        assert "Unsupported message type" in response["message"]


def test_websocket_text_missing_gemini_key(monkeypatch):
    """Test sending text prompt when GEMINI_API_KEY is missing yields structured error."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as ws:
        _ = ws.receive_json()

        ws.send_json({"type": "text", "text": "Explain quantum computing."})
        response = ws.receive_json()
        assert response["type"] == "status"
        assert response["status"] == "error"
        assert "GEMINI_API_KEY is missing" in response["message"]


def test_process_gemini_streamed_text_and_audio_events():
    """Test _process_gemini_message dispatches text, audio, and completion events."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    mock_ws = MockWebSocket()
    session = VoiceSession(session_id="test_sess", websocket=mock_ws)

    # 1. Create a Gemini Live message with text, audio, and turn_complete
    pcm_audio = b"\x10\x20\x30\x40"
    gemini_msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                parts=[
                    types.Part(text="Hello from Gemini!"),
                    types.Part(inline_data=types.Blob(data=pcm_audio, mime_type="audio/pcm;rate=24000")),
                ]
            ),
            turn_complete=True,
        )
    )

    asyncio.run(session._process_gemini_message(gemini_msg))

    assert len(mock_ws.sent_messages) == 3
    # First: text event
    assert mock_ws.sent_messages[0] == {"type": "text", "role": "assistant", "text": "Hello from Gemini!", "turn_id": 1}
    # Second: audio event with base64 data
    assert mock_ws.sent_messages[1]["type"] == "audio"
    assert mock_ws.sent_messages[1]["data"] == base64.b64encode(pcm_audio).decode("utf-8")
    assert mock_ws.sent_messages[1]["mime_type"] == "audio/pcm;rate=24000"
    assert mock_ws.sent_messages[1]["turn_id"] == 1
    # Third: turn complete event
    assert mock_ws.sent_messages[2] == {"type": "turn_complete", "session_id": "test_sess", "turn_id": 1}


def test_process_gemini_transcription_events():
    """Test _process_gemini_message dispatches interim, final, and output transcription events."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    mock_ws = MockWebSocket()
    session = VoiceSession(session_id="test_sess", websocket=mock_ws)

    # 1. Test interim (partial) input transcription
    interim_msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            interim_input_transcription=types.Transcription(text="What is the wea", finished=False)
        )
    )
    asyncio.run(session._process_gemini_message(interim_msg))

    assert len(mock_ws.sent_messages) == 1
    assert mock_ws.sent_messages[0] == {
        "type": "transcript",
        "role": "user",
        "text": "What is the wea",
        "is_final": False,
    }

    # 2. Test final input transcription
    mock_ws.sent_messages.clear()
    final_msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            input_transcription=types.Transcription(text="What is the weather today?", finished=True)
        )
    )
    asyncio.run(session._process_gemini_message(final_msg))

    assert len(mock_ws.sent_messages) == 1
    assert mock_ws.sent_messages[0] == {
        "type": "transcript",
        "role": "user",
        "text": "What is the weather today?",
        "is_final": True,
    }

    # 3. Test assistant output transcription
    mock_ws.sent_messages.clear()
    output_msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(
            output_transcription=types.Transcription(text="The weather today is sunny.", finished=False)
        )
    )
    asyncio.run(session._process_gemini_message(output_msg))

    assert len(mock_ws.sent_messages) == 1
    assert mock_ws.sent_messages[0] == {
        "type": "transcript",
        "role": "assistant",
        "text": "The weather today is sunny.",
        "is_final": False,
    }


def test_process_gemini_tool_call_event():
    """Test _process_gemini_message handles tool calls without executing them."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    mock_ws = MockWebSocket()
    session = VoiceSession(session_id="test_sess", websocket=mock_ws)

    fc = types.FunctionCall(name="query_database", id="call_abc", args={"query": "SELECT *"})
    gemini_msg = types.LiveServerMessage(
        tool_call=types.LiveServerToolCall(
            function_calls=[fc]
        )
    )

    asyncio.run(session._process_gemini_message(gemini_msg))

    assert len(mock_ws.sent_messages) == 2
    # 1. First: tool_call notification
    tool_event = mock_ws.sent_messages[0]
    assert tool_event["type"] == "tool_call"
    assert tool_event["handled"] is True
    assert len(tool_event["function_calls"]) == 1
    assert tool_event["function_calls"][0]["name"] == "query_database"

    # 2. Second: tool_result notification
    result_event = mock_ws.sent_messages[1]
    assert result_event["type"] == "tool_result"
    assert result_event["name"] == "query_database"
    assert result_event["call_id"] == "call_abc"


# ==============================================================================
# Barge-in / Interruption Tests (Step 11)
# ==============================================================================

def test_process_gemini_server_interrupted_event():
    """Verify Gemini server-side interruption increments turn_id and dispatches interrupted event."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    mock_ws = MockWebSocket()
    session = VoiceSession(session_id="test_sess", websocket=mock_ws)
    assert session.turn_id == 1

    # Simulate Gemini emitting server_content.interrupted = True
    gemini_msg = types.LiveServerMessage(
        server_content=types.LiveServerContent(interrupted=True)
    )
    asyncio.run(session._process_gemini_message(gemini_msg))

    assert session.turn_id == 2
    assert len(mock_ws.sent_messages) == 1
    assert mock_ws.sent_messages[0] == {
        "type": "interrupted",
        "turn_id": 2,
        "session_id": "test_sess",
    }


def test_client_interruption_websocket_action():
    """Verify client sending interrupt action over WebSocket increments turn_id and returns interrupted event."""
    client = TestClient(app)
    with client.websocket_connect("/ws/voice") as websocket:
        # 1. Connected handshake
        msg = websocket.receive_json()
        assert msg["type"] == "status"
        assert msg["status"] == "connected"

        # 2. Send interrupt signal
        websocket.send_text(json.dumps({"type": "interrupt"}))
        resp = websocket.receive_json()
        assert resp["type"] == "interrupted"
        assert resp["turn_id"] == 2


def test_rapid_multiple_interruptions():
    """Verify rapid successive interruptions advance turn_id cleanly without race conditions."""
    class MockWebSocket:
        def __init__(self):
            self.sent_messages = []

        async def send_json(self, payload):
            self.sent_messages.append(payload)

    mock_ws = MockWebSocket()
    session = VoiceSession(session_id="test_sess", websocket=mock_ws)

    for i in range(1, 4):
        asyncio.run(session.handle_interrupt())
        assert session.turn_id == i + 1

    assert session.turn_id == 4
    interrupted_events = [m for m in mock_ws.sent_messages if m["type"] == "interrupted"]
    assert len(interrupted_events) == 3
    assert [m["turn_id"] for m in interrupted_events] == [2, 3, 4]

