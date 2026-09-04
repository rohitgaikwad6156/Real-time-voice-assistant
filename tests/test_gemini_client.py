"""Unit tests for Gemini Live client abstraction and error handling."""

import asyncio
import pytest
from google.genai import types

from app.services.gemini_client import (
    GeminiConfigError,
    GeminiConnectionError,
    GeminiLiveClient,
    GeminiLiveConfig,
    GeminiLiveSession,
    SUPPORTED_VOICES,
    get_gemini_client,
    get_gemini_config,
)


def test_missing_api_key_raises_error():
    """Verify missing API key raises GeminiConfigError."""
    config = GeminiLiveConfig(api_key="")
    with pytest.raises(GeminiConfigError) as exc_info:
        config.validate()
    assert "GEMINI_API_KEY is missing" in str(exc_info.value)


def test_empty_model_raises_error():
    """Verify empty model string raises GeminiConfigError."""
    config = GeminiLiveConfig(api_key="test_key", model="")
    with pytest.raises(GeminiConfigError) as exc_info:
        config.validate()
    assert "Model name must not be empty" in str(exc_info.value)


def test_invalid_voice_raises_error():
    """Verify unsupported voice name raises GeminiConfigError."""
    config = GeminiLiveConfig(api_key="test_key", voice_name="NonExistentVoice")
    with pytest.raises(GeminiConfigError) as exc_info:
        config.validate()
    assert "is not supported" in str(exc_info.value)


def test_valid_configuration_and_types():
    """Verify valid configuration constructs LiveConnectConfig correctly."""
    config = GeminiLiveConfig(
        api_key="test_key_12345",
        model="gemini-2.0-flash-exp",
        voice_name="Puck",
        response_modalities=["AUDIO"],
    )
    config.validate()
    live_config = config.to_live_connect_config()

    assert isinstance(live_config, types.LiveConnectConfig)
    assert live_config.response_modalities == [types.Modality.AUDIO]
    assert live_config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"
    assert isinstance(live_config.input_audio_transcription, types.AudioTranscriptionConfig)
    assert live_config.input_audio_transcription.language_codes == ["en-US", "en-IN", "en"]
    assert isinstance(live_config.output_audio_transcription, types.AudioTranscriptionConfig)
    assert live_config.output_audio_transcription.language_codes == ["en-US", "en-IN", "en"]


def test_custom_language_codes():
    """Verify custom language_codes can be configured."""
    config = GeminiLiveConfig(
        api_key="test_key_12345",
        language_codes=["en-GB", "en"],
    )
    live_config = config.to_live_connect_config()
    assert live_config.input_audio_transcription.language_codes == ["en-GB", "en"]
    assert live_config.output_audio_transcription.language_codes == ["en-GB", "en"]


def test_public_summary_never_exposes_api_key():
    """Verify public summary does not expose the API key secret."""
    secret_key = "super_secret_gemini_api_key_value"
    config = GeminiLiveConfig(api_key=secret_key)
    summary = config.get_public_summary()

    assert "api_key" not in summary
    assert secret_key not in str(summary)
    assert summary["api_key_configured"] is True
    assert summary["model"] == "gemini-2.5-flash-native-audio-latest"
    assert summary["voice_name"] == "Puck"


def test_connect_with_missing_key_raises_config_error():
    """Verify client.connect() raises GeminiConfigError when key is missing."""
    async def _test():
        client = GeminiLiveClient(api_key="")
        with pytest.raises(GeminiConfigError) as exc_info:
            async with client.connect():
                pass
        assert "GEMINI_API_KEY is missing" in str(exc_info.value)

    asyncio.run(_test())


def test_connect_with_invalid_key_raises_connection_error():
    """Verify client.connect() catches APIError and raises GeminiConnectionError on auth failure."""
    async def _test():
        client = GeminiLiveClient(api_key="invalid_test_key_for_testing")
        with pytest.raises(GeminiConnectionError) as exc_info:
            async with client.connect():
                pass
        assert "Gemini Live API connection failed" in str(exc_info.value)

    asyncio.run(_test())
