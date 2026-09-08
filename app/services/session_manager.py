"""Authenticated WebSocket session manager for Gemini Live voice conversations."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types

from app.database.mongodb import ensure_conversation, save_message
from app.services.auth import decode_access_token
from app.services.gemini_client import (
    GeminiConfigError,
    GeminiConnectionError,
    GeminiError,
    GeminiLiveClient,
    GeminiLiveSession,
    get_gemini_client,
)
from app.services.tool_executor import ToolExecutor, get_default_tool_executor

logger = logging.getLogger("voice_assistant.session_manager")


def sanitize_error_message(raw_msg: str) -> str:
    if not raw_msg:
        return "An unexpected error occurred."
    sanitized = str(raw_msg)
    for env_var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "WEATHER_API_KEY", "MONGODB_URI", "JWT_SECRET"):
        val = os.getenv(env_var, "").strip()
        if val and len(val) >= 8 and val in sanitized:
            sanitized = sanitized.replace(val, "[REDACTED_SECRET]")
    return re.sub(r"AIza[0-9A-Za-z\-_]{35}", "[REDACTED_KEY]", sanitized)


class VoiceSession:
    def __init__(self, session_id: str, websocket: WebSocket):
        self.session_id = session_id
        self.websocket = websocket
        self.is_active = True
        self.user_id: Optional[str] = None
        self.user_email: Optional[str] = None
        self.conversation_id: Optional[str] = None
        self.tool_executor: ToolExecutor = get_default_tool_executor()
        self.gemini_client: Optional[GeminiLiveClient] = None
        self.gemini_session: Optional[GeminiLiveSession] = None
        self._gemini_cm: Optional[Any] = None
        self._receive_task: Optional[asyncio.Task] = None
        self.audio_chunks_received = 0
        self.total_bytes_received = 0
        self.turn_id = 1
        self._executed_call_ids: set[str] = set()
        self._stream_error_sent = False
        self._user_buffer = ""
        self._assistant_buffer = ""
        self._user_saved_for_turn = False

    @property
    def authenticated(self) -> bool:
        return bool(self.user_id and self.conversation_id)

    async def authenticate(self, token: str, conversation_id: Optional[str] = None) -> None:
        payload = decode_access_token(token)
        self.user_id = str(payload["sub"])
        self.user_email = str(payload.get("email", ""))
        conversation = await asyncio.to_thread(ensure_conversation, self.user_id, conversation_id)
        self.conversation_id = conversation["id"]
        self.tool_executor = get_default_tool_executor(user_id=self.user_id)
        await self.send_status(status="authenticated", conversation=conversation)

    async def _persist_user(self, text: str) -> None:
        if not self.authenticated or not text.strip() or self._user_saved_for_turn:
            return
        await asyncio.to_thread(save_message, self.user_id, self.conversation_id, "user", text.strip())
        self._user_saved_for_turn = True

    async def _persist_assistant(self, text: str) -> None:
        if not self.authenticated or not text.strip():
            return
        await asyncio.to_thread(save_message, self.user_id, self.conversation_id, "assistant", text.strip())

    def _reset_turn_buffers(self) -> None:
        self._user_buffer = ""
        self._assistant_buffer = ""
        self._user_saved_for_turn = False

    async def ensure_gemini_connected(self) -> GeminiLiveSession:
        if self.gemini_session is not None:
            return self.gemini_session
        if self.gemini_client is None:
            self.gemini_client = get_gemini_client()
        self._gemini_cm = self.gemini_client.connect()
        self.gemini_session = await self._gemini_cm.__aenter__()
        self._receive_task = asyncio.create_task(self._listen_to_gemini())
        logger.info("Session %s connected to Gemini Live API", self.session_id)
        return self.gemini_session

    async def reset_gemini_session(self, close_task: bool = True) -> None:
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
            except Exception:
                pass
        self._gemini_cm = None
        self.gemini_session = None

    async def _listen_to_gemini(self) -> None:
        try:
            while self.is_active and self.gemini_session is not None:
                async for message in self.gemini_session.receive():
                    if not self.is_active:
                        break
                    await self._process_gemini_message(message)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            clean = sanitize_error_message(str(exc))
            logger.error("Gemini receive loop failed for %s: %s", self.session_id, clean)
            if not self._stream_error_sent:
                self._stream_error_sent = True
                await self.send_status("error", f"Gemini connection error: {clean}")
        finally:
            if self.is_active:
                await self.reset_gemini_session(close_task=False)

    async def _process_gemini_message(self, message: types.LiveServerMessage) -> None:
        sc = getattr(message, "server_content", None)
        if sc is not None:
            if getattr(sc, "interrupted", False) and not getattr(sc, "turn_complete", False):
                self.turn_id += 1
                await self.send_json({"type": "interrupted", "turn_id": self.turn_id, "session_id": self.session_id})
                return

            interim = getattr(sc, "interim_input_transcription", None)
            if interim is not None and getattr(interim, "text", None):
                text = interim.text
                self._user_buffer = text if len(text) >= len(self._user_buffer) else self._user_buffer + text
                await self.send_json({"type": "transcript", "role": "user", "text": text, "is_final": False})

            input_trans = getattr(sc, "input_transcription", None)
            if input_trans is not None and getattr(input_trans, "text", None):
                text = input_trans.text
                self._user_buffer = text if len(text) >= len(self._user_buffer) else self._user_buffer + text
                finished = bool(getattr(input_trans, "finished", False))
                await self.send_json({"type": "transcript", "role": "user", "text": text, "is_final": finished})
                if finished:
                    await self._persist_user(self._user_buffer)

            output_trans = getattr(sc, "output_transcription", None)
            if output_trans is not None and getattr(output_trans, "text", None):
                text = output_trans.text
                self._assistant_buffer += text
                await self.send_json({
                    "type": "transcript", "role": "assistant", "text": text,
                    "is_final": bool(getattr(output_trans, "finished", False)),
                })

            model_turn = getattr(sc, "model_turn", None)
            if model_turn is not None:
                for part in getattr(model_turn, "parts", []) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        if not self._assistant_buffer:
                            self._assistant_buffer += part_text
                        await self.send_json({"type": "text", "role": "assistant", "text": part_text, "turn_id": self.turn_id})
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None and inline_data.data:
                        await self.send_json({
                            "type": "audio",
                            "data": base64.b64encode(inline_data.data).decode("utf-8"),
                            "mime_type": inline_data.mime_type or "audio/pcm;rate=24000",
                            "turn_id": self.turn_id,
                        })

            if getattr(sc, "turn_complete", False):
                if self._user_buffer:
                    await self._persist_user(self._user_buffer)
                if self._assistant_buffer:
                    await self._persist_assistant(self._assistant_buffer)
                await self.send_json({"type": "turn_complete", "turn_id": self.turn_id, "session_id": self.session_id})
                self.turn_id += 1
                self._reset_turn_buffers()

        tool_call = getattr(message, "tool_call", None)
        if tool_call is not None:
            calls = getattr(tool_call, "function_calls", []) or []
            unexecuted: List[Any] = []
            call_list: List[Dict[str, Any]] = []
            for fc in calls:
                call_id = getattr(fc, "id", "")
                if call_id and call_id in self._executed_call_ids:
                    continue
                if call_id:
                    self._executed_call_ids.add(call_id)
                unexecuted.append(fc)
                call_list.append({"name": getattr(fc, "name", ""), "id": call_id, "args": getattr(fc, "args", {}) or {}})
            if unexecuted:
                await self.send_json({"type": "tool_call", "function_calls": call_list, "handled": True})
                responses = await self.tool_executor.execute_calls(unexecuted)
                for response in responses:
                    raw = getattr(response, "response", {}) or {}
                    result = raw.get("result", {}) if isinstance(raw, dict) else {}
                    await self.send_json({
                        "type": "tool_result", "name": getattr(response, "name", ""),
                        "call_id": getattr(response, "id", ""), "result": result,
                    })
                if self.gemini_session is not None and responses:
                    await self.gemini_session.send_tool_response(responses)

        if getattr(message, "go_away", None) is not None:
            await self.send_status("session_ended", "Session ended by Gemini.")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        session = await self.ensure_gemini_connected()
        try:
            await session.send_audio_chunk(pcm_bytes, mime_type="audio/pcm;rate=16000")
        except Exception:
            await self.reset_gemini_session()
            session = await self.ensure_gemini_connected()
            await session.send_audio_chunk(pcm_bytes, mime_type="audio/pcm;rate=16000")
        self.audio_chunks_received += 1
        self.total_bytes_received += len(pcm_bytes)

    async def send_json(self, payload: Dict[str, Any]) -> bool:
        if not self.is_active:
            return False
        try:
            await self.websocket.send_json(payload)
            return True
        except Exception:
            self.is_active = False
            return False

    async def send_status(self, status: str, message: Optional[str] = None, **extra: Any) -> bool:
        payload: Dict[str, Any] = {"type": "status", "status": status, "session_id": self.session_id}
        if message is not None:
            payload["message"] = message
        payload.update(extra)
        return await self.send_json(payload)

    async def close(self) -> None:
        self.is_active = False
        await self.reset_gemini_session()


class SessionManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, VoiceSession] = {}

    def create_session(self, websocket: WebSocket) -> VoiceSession:
        session_id = uuid.uuid4().hex[:12]
        session = VoiceSession(session_id, websocket)
        self._sessions[session_id] = session
        return session

    async def remove_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.close()


session_manager = SessionManager()


def validate_message(raw_text: str) -> Dict[str, Any]:
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty message received.")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Malformed JSON.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise ValueError("Invalid message payload.")
    return data


async def handle_voice_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    session = session_manager.create_session(websocket)
    try:
        await session.send_status("connected")
        while session.is_active:
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                break

            bytes_payload = data.get("bytes")
            if bytes_payload is not None:
                if not session.authenticated:
                    await session.send_status("error", "Please login before using voice.")
                    continue
                try:
                    await session.send_audio(bytes_payload)
                    session._stream_error_sent = False
                    if session.audio_chunks_received == 1:
                        await session.send_status("streaming")
                except Exception as exc:
                    clean = sanitize_error_message(str(exc))
                    if not session._stream_error_sent:
                        session._stream_error_sent = True
                        await session.send_status("error", f"Audio streaming error: {clean}")
                continue

            raw = data.get("text")
            if raw is None:
                continue
            try:
                message = validate_message(raw)
            except ValueError as exc:
                await session.send_status("error", str(exc))
                continue

            action = message.get("type", "").lower()
            if action == "ping":
                await session.send_status("pong")
                continue
            if action == "auth":
                try:
                    await session.authenticate(message.get("token", ""), message.get("conversation_id"))
                except Exception as exc:
                    await session.send_status("auth_error", sanitize_error_message(str(exc)))
                continue
            if not session.authenticated:
                await session.send_status("auth_required", "Please login to continue.")
                continue

            if action == "init":
                try:
                    client = get_gemini_client()
                    client.config.validate()
                    session.gemini_client = client
                    await session.send_status("ready", config=client.config.get_public_summary())
                except Exception as exc:
                    await session.send_status("error", sanitize_error_message(str(exc)))
            elif action == "start_audio":
                session._reset_turn_buffers()
                await session.send_status("streaming")
            elif action == "stop_audio":
                if session.gemini_session is not None:
                    await session.gemini_session.end_audio_stream()
                await session.send_status("stopped", chunks=session.audio_chunks_received, bytes=session.total_bytes_received)
            elif action == "audio":
                try:
                    await session.send_audio(base64.b64decode(message.get("data", "")))
                except Exception as exc:
                    await session.send_status("error", sanitize_error_message(str(exc)))
            elif action == "text":
                prompt = message.get("text", "").strip()
                if not prompt:
                    await session.send_status("error", "Empty text received.")
                    continue
                session._reset_turn_buffers()
                session._user_buffer = prompt
                await session._persist_user(prompt)
                try:
                    gemini = await session.ensure_gemini_connected()
                    await gemini.send_text(prompt)
                except Exception as exc:
                    await session.send_status("error", sanitize_error_message(str(exc)))
            elif action == "interrupt":
                session.turn_id += 1
                await session.send_json({"type": "interrupted", "turn_id": session.turn_id, "session_id": session.session_id})
            else:
                await session.send_status("error", f"Unsupported message type: '{action}'.")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Unexpected WebSocket error in %s: %s", session.session_id, exc)
    finally:
        await session_manager.remove_session(session.session_id)
