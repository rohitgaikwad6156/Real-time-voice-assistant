import os
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

LLM_MODEL = os.getenv("OPENAI_LLM_MODEL", "gpt-5.6")
TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")

_client: Optional[OpenAI] = None


def get_openai_client() -> OpenAI:
    """Lazily instantiate and return OpenAI client when needed."""
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY is missing. Configure OPENAI_API_KEY to use legacy OpenAI endpoints.")
        _client = OpenAI(api_key=key)
    return _client


class _LazyOpenAIProxy:
    """Proxy object that defers OpenAI client instantiation until first attribute access."""

    def __getattr__(self, name: str):
        return getattr(get_openai_client(), name)


client = _LazyOpenAIProxy()

