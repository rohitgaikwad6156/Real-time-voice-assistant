"""WebSocket session manager for real-time voice assistant connections.

Maintains client session lifecycles, handles incoming WebSocket messages,
coordinates bidirectional streaming with Google Gemini Live API, and
dispatches streamed text, audio, tool call, and completion events to the client.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types

from app.services.gemini_client import (
    GeminiConfigError,
    GeminiConnectionError,
    GeminiError,
    GeminiLiveClient,
    GeminiLiveSession,
    get_gemini_client,
)
from app.services.tool_executor import ToolExecutor, get_default_tool_executor

import os
import re

logger = logging.getLogger("voice_assistant.session_manager")


def sanitize_error_message(raw_msg: str) -> str:
    """Remove API keys and credentials from log messages and client errors."""
    if not raw_msg:
        return "An unexpected error occurred."
    sanitized = str(raw_msg)
    for env_var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "WEATHER_API_KEY"):
        val = os.getenv(env_var, "").strip()
        if val and len(val) >= 8 and val in sanitized:
            sanitized = sanitized.replace(val, "[REDACTED_SECRET]")

    # Redact standard Google API key pattern (AIza...)
    sanitized = re.sub(r"AIza[0-9A-Za-z\-_]{35}", "[REDACTED_KEY]", sanitized)
    return sanitized


class VoiceSession:
    """Represents an active client WebSocket session with Gemini Live connectivity."""

    def __init__(
        self,
        session_id: str,
        websocket: WebSocket,
        tool_executor: Optional[ToolExecutor] = None,
    ):
        self.session_id = session_id
        self.websocket = websocket
        self.tool_executor = tool_executor or get_default_tool_executor()
        self.is_active = True
        self.gemini_client: Optional[GeminiLiveClient] = None
        self.gemini_session: Optional[GeminiLiveSession] = None
        self._gemini_cm: Optional[Any] = None
        self._receive_task: Optional[asyncio.Task] = None
        self.audio_chunks_received: int = 0
        self.total_bytes_received: int = 0
        self.turn_id: int = 1
        self._executed_call_ids: set[str] = set()
        self._stream_error_sent: bool = False

    async def handle_interrupt(self) -> None:
        """Handle user barge-in interruption."""
        self.turn_id += 1
        logger.info("Session %s interrupted. Advanced to turn_id=%d", self.session_id, self.turn_id)
        await self.send_json({
            "type": "interrupted",
            "turn_id": self.turn_id,
            "session_id": self.session_id,
        })

    async def ensure_gemini_connected(self) -> GeminiLiveSession:
        """Lazily connect to Gemini Live API and launch downstream message listener.

        Raises:
            GeminiConfigError: If GEMINI_API_KEY is not configured.
            GeminiConnectionError: If connection to Gemini Live fails.
        """
        if self.gemini_session is not None:
            return self.gemini_session

        if self.gemini_client is None:
            self.gemini_client = get_gemini_client()

        self._gemini_cm = self.gemini_client.connect()
        self.gemini_session = await self._gemini_cm.__aenter__()
        logger.info("Session %s connected to Gemini Live API.", self.session_id)

        # Launch background task to continuously listen for Gemini response events
        self._receive_task = asyncio.create_task(self._listen_to_gemini())
        return self.gemini_session

    async def reset_gemini_session(self, close_task: bool = True) -> None:
        """Reset and clean up an existing Gemini Live session so it can be reconnected."""
        if close_task and self._receive_task is not None and self._receive_task != asyncio.current_task():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except (asyncio.CancelledError, Exception):
                pass
        self._receive_task = None

        if self._gemini_cm is not None:
            try:
                await self._gemini_cm.__aexit__(None, None, None)
            except Exception as exc:
                logger.debug("Error closing Gemini session for %s: %s", self.session_id, exc)
            self._gemini_cm = None

        self.gemini_session = None

    async def _listen_to_gemini(self) -> None:
        """Continuously receive events from Gemini Live API and forward to the client."""
        logger.info("Session %s started Gemini receive loop.", self.session_id)
        try:
            if self.gemini_session is None:
                return

            async for message in self.gemini_session.receive():
                if not self.is_active:
                    break
                await self._process_gemini_message(message)

        except asyncio.CancelledError:
            logger.debug("Gemini receive loop cancelled for %s", self.session_id)
        except (GeminiError, Exception) as exc:
            clean_err = sanitize_error_message(str(exc))
            logger.error("Error in Gemini receive loop for %s: %s", self.session_id, clean_err)
            if not self._stream_error_sent:
                self._stream_error_sent = True
                await self.send_status(status="error", message=f"Gemini connection error: {clean_err}")
        finally:
            logger.info("Session %s exited Gemini receive loop.", self.session_id)
            if self.is_active:
                await self.reset_gemini_session(close_task=False)

    async def _process_gemini_message(self, message: types.LiveServerMessage) -> None:
        """Process an incoming server message from Gemini Live API."""
        # 1. Process server content (text, audio, transcriptions, completion, interruption)
        if getattr(message, "server_content", None) is not None:
            sc = message.server_content

            # Check for server-side interruption from Gemini Live API
            if getattr(sc, "interrupted", False):
                self.turn_id += 1
                logger.info(
                    "Session %s: Gemini Live server reported model interruption. New turn_id=%d",
                    self.session_id,
                    self.turn_id,
                )
                await self.send_json({
                    "type": "interrupted",
                    "turn_id": self.turn_id,
                    "session_id": self.session_id,
                })
                return

            # 1. Interim (Low-latency streaming) User Input Transcription
            interim_trans = getattr(sc, "interim_input_transcription", None)
            if interim_trans is not None and getattr(interim_trans, "text", None):
                await self.send_json({
                    "type": "transcript",
                    "role": "user",
                    "text": interim_trans.text,
                    "is_final": False,
                })

            # 2. Final/Turn User Input Transcription
            input_trans = getattr(sc, "input_transcription", None)
            if input_trans is not None and getattr(input_trans, "text", None):
                is_finished = getattr(input_trans, "finished", True)
                await self.send_json({
                    "type": "transcript",
                    "role": "user",
                    "text": input_trans.text,
                    "is_final": is_finished,
                })

            # 3. Output Audio Transcription (Assistant Speech)
            output_trans = getattr(sc, "output_transcription", None)
            if output_trans is not None and getattr(output_trans, "text", None):
                await self.send_json({
                    "type": "transcript",
                    "role": "assistant",
                    "text": output_trans.text,
                    "is_final": getattr(output_trans, "finished", False),
                })

            # 4. Model response parts (streamed text and audio)
            if getattr(sc, "model_turn", None) is not None:
                parts = getattr(sc.model_turn, "parts", []) or []
                for part in parts:
                    # Streamed Text Delta
                    part_text = getattr(part, "text", None)
                    if part_text:
                        await self.send_json({
                            "type": "text",
                            "role": "assistant",
                            "text": part_text,
                            "turn_id": self.turn_id,
                        })

                    # Streamed Audio Chunk (PCM 24 kHz)
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None and inline_data.data:
                        b64_audio = base64.b64encode(inline_data.data).decode("utf-8")
                        mime_type = inline_data.mime_type or "audio/pcm;rate=24000"
                        await self.send_json({
                            "type": "audio",
                            "data": b64_audio,
                            "mime_type": mime_type,
                            "turn_id": self.turn_id,
                        })

            # 5. Turn complete event
            if getattr(sc, "turn_complete", False):
                await self.send_json({
                    "type": "turn_complete",
                    "turn_id": self.turn_id,
                    "session_id": self.session_id,
                })

        # 2. Process tool call events (Execute tools & return responses to Gemini)
        if getattr(message, "tool_call", None) is not None:
            tc = message.tool_call
            function_calls = getattr(tc, "function_calls", []) or []
            if function_calls:
                unexecuted_calls = []
                call_list: List[Dict[str, Any]] = []
                for fc in function_calls:
                    c_id = getattr(fc, "id", "")
                    if c_id and c_id in self._executed_call_ids:
                        logger.warning("Session %s skipping already executed tool call '%s'", self.session_id, c_id)
                        continue
                    if c_id:
                        self._executed_call_ids.add(c_id)
                    unexecuted_calls.append(fc)
                    call_list.append({
                        "name": getattr(fc, "name", ""),
                        "id": c_id,
                        "args": getattr(fc, "args", {}) or {},
                    })

                if not unexecuted_calls:
                    return

                logger.info("Session %s executing tool calls: %s", self.session_id, [c["name"] for c in call_list])
                # Notify frontend of tool invocation
                await self.send_json({
                    "type": "tool_call",
                    "function_calls": call_list,
                    "handled": True,
                })

                # Execute tools via ToolExecutor
                tool_responses = await self.tool_executor.execute_calls(unexecuted_calls)

                # Dispatch tool results to client
                for resp in tool_responses:
                    raw_resp = getattr(resp, "response", {}) or {}
                    res_data = raw_resp.get("result", {}) if isinstance(raw_resp, dict) else {}
                    await self.send_json({
                        "type": "tool_result",
                        "name": getattr(resp, "name", ""),
                        "call_id": getattr(resp, "id", ""),
                        "result": res_data,
                    })

                # Return tool responses to Gemini Live so it continues conversation
                if self.gemini_session is not None and tool_responses:
                    try:
                        await self.gemini_session.send_tool_response(tool_responses)
                        logger.info("Session %s returned %d tool responses to Gemini.", self.session_id, len(tool_responses))
                    except Exception as exc:
                        logger.error("Session %s error returning tool responses: %s", self.session_id, exc)

        # 3. Process server termination / go away event
        if getattr(message, "go_away", None) is not None:
            logger.warning("Session %s received go_away signal from Gemini Live.", self.session_id)
            await self.send_status(status="session_ended", message="Session ended by Gemini.")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Forward a raw PCM 16-bit audio chunk to Gemini Live API."""
        try:
            session = await self.ensure_gemini_connected()
            await session.send_audio_chunk(pcm_bytes, mime_type="audio/pcm;rate=16000")
        except (GeminiConnectionError, Exception) as exc:
            logger.warning("Session %s audio send failed (%s). Reconnecting Gemini session...", self.session_id, exc)
            await self.reset_gemini_session(close_task=True)
            session = await self.ensure_gemini_connected()
            await session.send_audio_chunk(pcm_bytes, mime_type="audio/pcm;rate=16000")

        self.audio_chunks_received += 1
        self.total_bytes_received += len(pcm_bytes)

    async def send_json(self, payload: Dict[str, Any]) -> bool:
        """Send a JSON payload to the connected WebSocket client.

        Returns True if sent successfully, False otherwise.
        """
        if not self.is_active:
            return False
        try:
            await self.websocket.send_json(payload)
            return True
        except Exception as exc:
            logger.warning("Failed to send message to session %s: %s", self.session_id, exc)
            self.is_active = False
            return False

    async def send_status(
        self,
        status: str,
        message: Optional[str] = None,
        **extra: Any,
    ) -> bool:
        """Send a structured status message to the client."""
        payload: Dict[str, Any] = {
            "type": "status",
            "status": status,
            "session_id": self.session_id,
        }
        if message is not None:
            payload["message"] = message
        payload.update(extra)
        return await self.send_json(payload)

    async def close(self) -> None:
        """Clean up background tasks, close Gemini Live session, and mark inactive."""
        self.is_active = False
        await self.reset_gemini_session(close_task=True)


class SessionManager:
    """Manages active WebSocket sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, VoiceSession] = {}

    @property
    def active_session_count(self) -> int:
        """Return the number of currently active sessions."""
        return len(self._sessions)

    def create_session(self, websocket: WebSocket) -> VoiceSession:
        """Create and register a new client session."""
        session_id = uuid.uuid4().hex[:12]
        session = VoiceSession(session_id=session_id, websocket=websocket)
        self._sessions[session_id] = session
        logger.info("Session %s created. Total active sessions: %d", session_id, len(self._sessions))
        return session

    def get_session(self, session_id: str) -> Optional[VoiceSession]:
        """Retrieve a session by its ID."""
        return self._sessions.get(session_id)

    async def remove_session(self, session_id: str) -> Optional[VoiceSession]:
        """Remove and clean up a session."""
        session = self._sessions.pop(session_id, None)
        if session:
            await session.close()
            logger.info("Session %s removed. Remaining active sessions: %d", session_id, len(self._sessions))
        return session


# Global session manager instance
session_manager = SessionManager()


def validate_message(raw_text: str) -> Dict[str, Any]:
    """Validate and parse an incoming WebSocket text payload.

    Args:
        raw_text: Raw string from client.

    Returns:
        Parsed JSON dictionary.

    Raises:
        ValueError: If message is not valid JSON or lacks the required 'type' field.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty message received.")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as err:
        raise ValueError(f"Malformed JSON: {err.msg}") from err

    if not isinstance(data, dict):
        raise ValueError("Invalid payload: message must be a JSON object.")

    if "type" not in data or not isinstance(data["type"], str) or not data["type"].strip():
        raise ValueError("Invalid payload: missing or invalid 'type' field.")

    return data


async def handle_voice_websocket(websocket: WebSocket) -> None:
    """Handle the complete lifecycle of a client WebSocket on /ws/voice."""
    await websocket.accept()

    session = session_manager.create_session(websocket)

    try:
        # Notify client of successful connection
        await session.send_status(status="connected")

        # Process incoming client messages
        while session.is_active:
            message_data = await websocket.receive()
            msg_type = message_data.get("type")

            if msg_type == "websocket.disconnect":
                break

            # --------------------------------------------------------------
            # Case 1: Binary PCM Audio Chunk (Direct from Web Audio API)
            # --------------------------------------------------------------
            bytes_payload = message_data.get("bytes")
            if bytes_payload is not None:
                try:
                    await session.send_audio(bytes_payload)
                    session._stream_error_sent = False
                    if session.audio_chunks_received == 1:
                        await session.send_status(status="streaming")
                except GeminiConfigError as cfg_err:
                    clean_err = sanitize_error_message(str(cfg_err))
                    logger.error("Session %s Gemini config error: %s", session.session_id, clean_err)
                    if not session._stream_error_sent:
                        session._stream_error_sent = True
                        await session.send_status(status="error", message=clean_err)
                except GeminiConnectionError as conn_err:
                    clean_err = sanitize_error_message(str(conn_err))
                    logger.error("Session %s Gemini connection error: %s", session.session_id, clean_err)
                    if not session._stream_error_sent:
                        session._stream_error_sent = True
                        await session.send_status(status="error", message=clean_err)
                except Exception as exc:
                    clean_err = sanitize_error_message(str(exc))
                    logger.exception("Session %s audio streaming error: %s", session.session_id, clean_err)
                    if not session._stream_error_sent:
                        session._stream_error_sent = True
                        await session.send_status(status="error", message=f"Audio streaming error: {clean_err}")
                continue

            # --------------------------------------------------------------
            # Case 2: Text / JSON Message
            # --------------------------------------------------------------
            raw_text = message_data.get("text")
            if raw_text is not None:
                try:
                    message = validate_message(raw_text)
                except ValueError as val_err:
                    logger.warning("Session %s message validation failed: %s", session.session_id, val_err)
                    await session.send_status(status="error", message=str(val_err))
                    continue

                action = message.get("type", "").lower()

                if action == "ping":
                    await session.send_status(status="pong")

                elif action == "init":
                    try:
                        client = get_gemini_client()
                        client.config.validate()
                        session.gemini_client = client
                        summary = client.config.get_public_summary()
                        await session.send_status(status="ready", config=summary)
                    except GeminiConfigError as cfg_err:
                        clean_err = sanitize_error_message(str(cfg_err))
                        logger.error("Session %s Gemini config error: %s", session.session_id, clean_err)
                        await session.send_status(status="error", message=clean_err)
                    except GeminiConnectionError as conn_err:
                        clean_err = sanitize_error_message(str(conn_err))
                        logger.error("Session %s Gemini connection error: %s", session.session_id, clean_err)
                        await session.send_status(status="error", message=clean_err)
                    except Exception as exc:
                        clean_err = sanitize_error_message(str(exc))
                        logger.exception("Session %s unexpected init error: %s", session.session_id, clean_err)
                        await session.send_status(status="error", message=f"Initialization failure: {clean_err}")

                elif action == "start_audio":
                    logger.info("Session %s starting audio stream.", session.session_id)
                    session._stream_error_sent = False
                    await session.send_status(status="streaming")

                elif action == "stop_audio":
                    logger.info(
                        "Session %s stopped audio stream. Chunks: %d, Bytes: %d",
                        session.session_id,
                        session.audio_chunks_received,
                        session.total_bytes_received,
                    )
                    await session.send_status(
                        status="stopped",
                        chunks=session.audio_chunks_received,
                        bytes=session.total_bytes_received,
                    )

                elif action == "audio":
                    b64_data = message.get("data", "")
                    try:
                        pcm_bytes = base64.b64decode(b64_data)
                        await session.send_audio(pcm_bytes)
                        session._stream_error_sent = False
                        if session.audio_chunks_received == 1:
                            await session.send_status(status="streaming")
                    except GeminiConfigError as cfg_err:
                        clean_err = sanitize_error_message(str(cfg_err))
                        logger.error("Session %s Gemini config error: %s", session.session_id, clean_err)
                        if not session._stream_error_sent:
                            session._stream_error_sent = True
                            await session.send_status(status="error", message=clean_err)
                    except GeminiConnectionError as conn_err:
                        clean_err = sanitize_error_message(str(conn_err))
                        logger.error("Session %s Gemini connection error: %s", session.session_id, clean_err)
                        if not session._stream_error_sent:
                            session._stream_error_sent = True
                            await session.send_status(status="error", message=clean_err)
                    except Exception as exc:
                        clean_err = sanitize_error_message(str(exc))
                        logger.error("Session %s base64 audio error: %s", session.session_id, clean_err)
                        if not session._stream_error_sent:
                            session._stream_error_sent = True
                            await session.send_status(status="error", message=f"Invalid audio chunk: {clean_err}")

                elif action == "text":
                    user_prompt = message.get("text", "").strip()
                    if not user_prompt:
                        await session.send_status(status="error", message="Empty text received.")
                    else:
                        try:
                            gemini_session = await session.ensure_gemini_connected()
                            await session.send_json({
                                "type": "transcript",
                                "role": "user",
                                "text": user_prompt,
                                "is_final": True,
                            })
                            try:
                                await gemini_session.send_text(user_prompt)
                            except (GeminiConnectionError, Exception) as send_err:
                                logger.warning("Session %s send_text failed (%s). Reconnecting...", session.session_id, send_err)
                                await session.reset_gemini_session()
                                gemini_session = await session.ensure_gemini_connected()
                                await gemini_session.send_text(user_prompt)
                        except GeminiConfigError as cfg_err:
                            clean_err = sanitize_error_message(str(cfg_err))
                            logger.error("Session %s Gemini config error: %s", session.session_id, clean_err)
                            await session.send_status(status="error", message=clean_err)
                        except GeminiConnectionError as conn_err:
                            clean_err = sanitize_error_message(str(conn_err))
                            logger.error("Session %s Gemini connection error: %s", session.session_id, clean_err)
                            await session.send_status(status="error", message=clean_err)
                        except Exception as exc:
                            clean_err = sanitize_error_message(str(exc))
                            logger.exception("Session %s text prompt error: %s", session.session_id, clean_err)
                            await session.send_status(status="error", message=f"Text processing error: {clean_err}")

                elif action == "interrupt":
                    logger.info("Session %s received client interruption signal.", session.session_id)
                    await session.handle_interrupt()

                else:
                    await session.send_status(
                        status="error",
                        message=(
                            f"Unsupported message type: '{action}'. "
                            f"Supported: 'ping', 'init', 'start_audio', 'stop_audio', 'audio', 'text', 'interrupt'."
                        ),
                    )

    except WebSocketDisconnect:
        logger.info("Session %s disconnected normally.", session.session_id)
    except Exception as exc:
        logger.exception("Unexpected error in session %s: %s", session.session_id, exc)
        await session.send_status(status="error", message="Internal server error.")
    finally:
        await session_manager.remove_session(session.session_id)
