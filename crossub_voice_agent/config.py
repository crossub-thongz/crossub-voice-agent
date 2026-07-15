"""Central configuration for the CROSSUB voice agent.

All tunables live here (no magic strings scattered through the code). Values are
read from the environment (loaded from `.env` by `agent.py`), with sensible
defaults so the agent runs out of the box once provider keys are set.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# This module is imported before agent.py's own load_dotenv() runs, so we must
# load the .env here — otherwise every os.getenv() below reads an empty
# environment and silently falls back to defaults (and optional keys become None).
load_dotenv()

# Values treated as boolean-true in env vars.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


# --- Speech-to-text (Deepgram) ---
STT_MODEL = os.getenv("VOICE_STT_MODEL", "nova-3")
# "multi" = attempt EN/中文 code-switching in one stream. Per-call "en"/"zh"
# selection is the fallback if code-switching quality is poor (see README).
STT_LANGUAGE = os.getenv("VOICE_STT_LANGUAGE", "multi")

# --- LLM (Anthropic Claude) ---
LLM_MODEL = os.getenv("VOICE_LLM_MODEL", "claude-haiku-4-5")

# --- Text-to-speech (ElevenLabs) ---
TTS_MODEL = os.getenv("VOICE_TTS_MODEL", "eleven_flash_v2_5")
# Optional multilingual voice id; None => plugin default voice.
TTS_VOICE_ID = os.getenv("VOICE_TTS_VOICE_ID") or None
# The ElevenLabs plugin defaults to reading ELEVEN_API_KEY; we standardize on the
# clearer ELEVENLABS_API_KEY and pass it explicitly (accepting either name).
TTS_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY") or None

# --- Worker ---
# When set, the worker only runs on explicit dispatch (used for SIP inbound
# routing). `console` mode ignores this and always runs locally.
AGENT_NAME = os.getenv("VOICE_AGENT_NAME", "crossub-inbound")

# LiveKit Cloud enhanced noise cancellation tuned for phone audio.
# Requires `uv sync --extra telephony` and a LiveKit Cloud project.
USE_TELEPHONY_NOISE_CANCELLATION = _flag("VOICE_TELEPHONY_NOISE_CANCELLATION", False)
