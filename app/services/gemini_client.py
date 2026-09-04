"""Gemini Live API client service for real-time voice streaming.

Provides a clean client and session abstraction over the official Google GenAI SDK
(google-genai) for bidirectional audio/text communication.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# Ensure environment variables are loaded
load_dotenv()

# Valid voices supported by Gemini Live API
SUPPORTED_VOICES = ("Puck", "Charon", "Kore", "Fenrir", "Aoede")

# Default configurations
DEFAULT_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"
DEFAULT_VOICE_NAME = "Puck"
DEFAULT_MODALITIES = ["AUDIO"]
DEFAULT_SYSTEM_INSTRUCTION = (
    "You are a helpful, conversational, and natural AI real-time voice assistant. "
    "Always transcribe, understand, and process speech in English using the Latin/English script. "
    "Never transliterate English speech into non-Latin scripts (such as Telugu, Devanagari, Tamil, etc.). "
    "Always formulate responses in English unless the user explicitly asks to speak in a different language. "
    "Keep responses concise, clear, and easy to follow when spoken aloud. "
    "Avoid markdown formatting or tables."
)
DEFAULT_LANGUAGE_CODES = ["en-US", "en-IN", "en"]


# ==============================================================================
# Custom Exceptions
# ==============================================================================

class GeminiError(Exception):
    """Base exception for all Gemini service errors."""
    pass


class GeminiConfigError(GeminiError):
    """Raised when Gemini configuration or API key is missing or invalid."""
    pass


class GeminiConnectionError(GeminiError):
    """Raised when connection to Gemini Live API fails or terminates unexpectedly."""
    pass


# ==============================================================================
# Configuration Abstraction
# ==============================================================================

@dataclass
class GeminiLiveConfig:
    """Configuration settings for Gemini Live API sessions."""

    api_key: Optional[str] = None
    model: str = field(
        default_factory=lambda: os.getenv("GEMINI_LIVE_MODEL") or os.getenv("GEMINI_MODEL") or DEFAULT_LIVE_MODEL
    )
    voice_name: str = field(
        default_factory=lambda: os.getenv("GEMINI_VOICE_NAME") or os.getenv("GEMINI_VOICE") or DEFAULT_VOICE_NAME
    )
    response_modalities: List[str] = field(
        default_factory=lambda: list(DEFAULT_MODALITIES)
    )
    system_instruction: str = field(
        default_factory=lambda: os.getenv("GEMINI_SYSTEM_PROMPT", DEFAULT_SYSTEM_INSTRUCTION)
    )
    language_codes: List[str] = field(
        default_factory=lambda: [
            c.strip()
            for c in os.getenv("GEMINI_LANGUAGE_CODES", "en-US,en-IN,en").split(",")
            if c.strip()
        ]
    )
    tools: Optional[List[types.Tool]] = None

    def __post_init__(self) -> None:
        """Resolve API key from environment if not explicitly provided."""
        if self.api_key is None:
            self.api_key = os.getenv("GEMINI_API_KEY", "").strip()

    def validate(self) -> None:
        """Validate configuration settings.

        Raises:
            GeminiConfigError: If API key is missing or configuration parameters are invalid.
        """
        if not self.api_key:
            raise GeminiConfigError(
                "GEMINI_API_KEY is missing. Please set GEMINI_API_KEY in your .env file or environment."
            )

        if not self.model or not self.model.strip():
            raise GeminiConfigError("Invalid configuration: Model name must not be empty.")

        if self.voice_name not in SUPPORTED_VOICES:
            raise GeminiConfigError(
                f"Invalid configuration: Voice '{self.voice_name}' is not supported. "
                f"Supported voices: {', '.join(SUPPORTED_VOICES)}."
            )

        if not self.response_modalities:
            raise GeminiConfigError(
                "Invalid configuration: At least one response modality (e.g. 'AUDIO') must be specified."
            )

    def to_live_connect_config(self) -> types.LiveConnectConfig:
        """Convert this configuration into a google-genai types.LiveConnectConfig object."""
        self.validate()

        modalities = [
            types.Modality.AUDIO if m.upper() == "AUDIO" else types.Modality.TEXT
            for m in self.response_modalities
        ]

        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=self.voice_name
                )
            )
        )

        system_instruction = types.Content(
            parts=[types.Part.from_text(text=self.system_instruction)]
        )

        live_tools = self.tools
        if live_tools is None:
            from app.tools.registry import get_default_registry
            live_tools = get_default_registry().get_gemini_tools()

        thinking_config = None
        try:
            thinking_config = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

        return types.LiveConnectConfig(
            response_modalities=modalities,
            speech_config=speech_config,
            system_instruction=system_instruction,
            tools=live_tools,
            thinking_config=thinking_config,
            input_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=self.language_codes,
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(
                language_codes=self.language_codes,
            ),
        )

    def get_public_summary(self) -> dict:
        """Return safe, non-sensitive configuration metadata for status checks.

        Never exposes the actual API key string.
        """
        return {
            "model": self.model,
            "voice_name": self.voice_name,
            "language_codes": self.language_codes,
            "response_modalities": self.response_modalities,
            "api_key_configured": bool(self.api_key and self.api_key.strip()),
        }


# ==============================================================================
# Live Session Wrapper Abstraction
# ==============================================================================

class GeminiLiveSession:
    """Wrapper around google.genai.live.AsyncSession.

    Provides clean helper methods for real-time audio/text exchange used by
    the WebSocket voice pipeline in subsequent steps.
    """

    def __init__(self, raw_session: genai.live.AsyncSession):
        self._session = raw_session

    @property
    def raw_session(self) -> genai.live.AsyncSession:
        """Direct access to the underlying SDK AsyncSession."""
        return self._session

    async def send_audio_chunk(
        self, pcm_bytes: bytes, mime_type: str = "audio/pcm;rate=16000"
    ) -> None:
        """Stream a raw PCM audio chunk to Gemini Live.

        Args:
            pcm_bytes: Raw PCM 16-bit little-endian audio bytes.
            mime_type: MIME type of the audio stream (default: audio/pcm;rate=16000).
        """
        try:
            # Note: Google GenAI Live API requires media= for streaming audio VAD (mediaChunks)
            await self._session.send_realtime_input(
                media=types.Blob(data=pcm_bytes, mime_type=mime_type)
            )
        except Exception as exc:
            raise GeminiConnectionError(
                f"Failed to stream audio chunk to Gemini Live: {exc}"
            ) from exc

    async def end_audio_stream(self) -> None:
        """Signal to Gemini Live that the user has stopped sending audio chunks.

        Allows Gemini's turn detection to immediately close the speech segment
        and generate the response without waiting for client-side timeout.
        """
        try:
            await self._session.send_realtime_input(audio_stream_end=True)
        except Exception as exc:
            # Non-fatal warning if already ended or interrupted
            pass

    async def send_text(self, text: str, end_of_turn: bool = True) -> None:
        """Send a text turn to Gemini Live.

        Args:
            text: Text prompt string.
            end_of_turn: True if user turn is complete and model should respond.
        """
        try:
            await self._session.send_client_content(
                turns=[types.Content(role="user", parts=[types.Part.from_text(text=text)])],
                turn_complete=end_of_turn,
            )
        except Exception as exc:
            raise GeminiConnectionError(
                f"Failed to send text to Gemini Live: {exc}"
            ) from exc

    async def send_tool_response(
        self,
        function_responses: Any,
    ) -> None:
        """Send tool execution response(s) back to Gemini Live.

        Args:
            function_responses: Single FunctionResponse or sequence of FunctionResponse objects.
        """
        try:
            await self._session.send_tool_response(
                function_responses=function_responses
            )
        except Exception as exc:
            raise GeminiConnectionError(
                f"Failed to send tool response to Gemini Live: {exc}"
            ) from exc

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        """Asynchronously iterate over messages received from Gemini Live."""
        try:
            async for message in self._session.receive():
                yield message
        except Exception as exc:
            raise GeminiConnectionError(
                f"Error receiving stream from Gemini Live: {exc}"
            ) from exc

    async def close(self) -> None:
        """Gracefully close the live session."""
        try:
            await self._session.close()
        except Exception:
            pass


# ==============================================================================
# Client Abstraction
# ==============================================================================

class GeminiLiveClient:
    """Manages Gemini Live API connectivity and session lifecycle."""

    def __init__(
        self,
        config: Optional[GeminiLiveConfig] = None,
        api_key: Optional[str] = None,
    ):
        if config is not None:
            self.config = config
            if api_key is not None:
                self.config.api_key = api_key
        else:
            self.config = GeminiLiveConfig(api_key=api_key)

        self._client: Optional[genai.Client] = None

    def _get_or_create_client(self) -> genai.Client:
        """Initialize the official Google GenAI Client.

        Raises:
            GeminiConfigError: If GEMINI_API_KEY is not configured.
        """
        self.config.validate()

        if self._client is None:
            self._client = genai.Client(api_key=self.config.api_key)

        return self._client

    @asynccontextmanager
    async def connect(
        self, custom_config: Optional[types.LiveConnectConfig] = None
    ) -> AsyncIterator[GeminiLiveSession]:
        """Establish a real-time bidirectional WebSocket session with Gemini Live API.

        Yields:
            GeminiLiveSession: Wrapped session providing audio streaming and response reception.

        Raises:
            GeminiConfigError: If API key or configuration is invalid.
            GeminiConnectionError: If connection or handshake fails.
        """
        client = self._get_or_create_client()
        connect_config = custom_config or self.config.to_live_connect_config()

        try:
            async with client.aio.live.connect(
                model=self.config.model, config=connect_config
            ) as raw_session:
                yield GeminiLiveSession(raw_session)
        except (errors.APIError, errors.ClientError, errors.ServerError) as api_err:
            raise GeminiConnectionError(
                f"Gemini Live API connection failed: {api_err}"
            ) from api_err
        except GeminiError:
            raise
        except Exception as err:
            raise GeminiConnectionError(
                f"Failed to connect to Gemini Live session: {err}"
            ) from err


# ==============================================================================
# Service Helpers
# ==============================================================================

def get_gemini_config() -> GeminiLiveConfig:
    """Retrieve the current Gemini configuration."""
    return GeminiLiveConfig()


def get_gemini_client(
    config: Optional[GeminiLiveConfig] = None,
    api_key: Optional[str] = None,
) -> GeminiLiveClient:
    """Factory helper to create a GeminiLiveClient instance."""
    return GeminiLiveClient(config=config, api_key=api_key)
