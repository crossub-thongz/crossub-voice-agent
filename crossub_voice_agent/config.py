"""Central configuration for the CROSSUB voice agent.

All tunables live here (no magic strings scattered through the code). Values are
read from the environment (loaded from `.env` by `agent.py`), with sensible
defaults so the agent runs out of the box once provider keys are set.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .constants import DEFAULT_VOICE_MODE, VoiceMode

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


def _seconds(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _voice_mode() -> VoiceMode:
    """Parse VOICE_MODE, falling back to the safe default for anything
    unrecognised — a typo must never silently arm the full self-service agent."""
    raw = (os.getenv("VOICE_MODE") or "").strip().lower()
    for mode in VoiceMode:
        if raw == mode.value:
            return mode
    return DEFAULT_VOICE_MODE


# --- Answer behaviour ---
# `divert` (default) = hear the enquiry, point the caller at the email channel,
# take no action and expose NO tools. `full` = the earlier self-service assistant.
# See constants/voice_mode.py for why divert is the business default.
VOICE_MODE = _voice_mode()

# The intake address the agent reads out in divert mode. This must be a mailbox
# the support sweep actually polls — `support@crossub.com.au` is the one that
# already runs AI triage + auto-reply, and leasing@/maintenance@/inspection@
# route into it (see mailbox-routing.constants.ts in crossub_web).
INTAKE_EMAIL = os.getenv("VOICE_INTAKE_EMAIL", "support@crossub.com.au").strip()

# Optional SMS intake number, spoken as a second option ("or text us on ...").
# BLANK BY DEFAULT AND IT MUST STAY THAT WAY UNTIL SMS ACTUALLY EXISTS: as of
# Aug 2026 crossub_web has no SMS provider at all (no Twilio, no sendSms), so
# nothing can receive a text. Setting this before the integration lands would
# send tenants into a black hole. Set it only once inbound SMS is delivered
# somewhere the team reads.
INTAKE_SMS_NUMBER = (os.getenv("VOICE_INTAKE_SMS_NUMBER") or "").strip() or None


# --- Speech-to-text (Deepgram) ---
STT_MODEL = os.getenv("VOICE_STT_MODEL", "nova-3")
# "multi" = attempt EN/中文 code-switching in one stream. Per-call "en"/"zh"
# selection is the fallback if code-switching quality is poor (see README).
STT_LANGUAGE = os.getenv("VOICE_STT_LANGUAGE", "multi")

# --- LLM (Anthropic Claude) ---
LLM_MODEL = os.getenv("VOICE_LLM_MODEL", "claude-haiku-4-5")

# --- Text-to-speech (ElevenLabs) ---
# The model is switched PER TURN alongside the voice (see agent.apply_tts_language),
# because the two languages want opposite trade-offs on a free-tier account:
#   English — flash_v2_5, ~310ms to first byte, and it accepts an enforced language code.
#   中文    — multilingual_v2 handles Mandarin noticeably better, but measured ~1310ms
#             to first byte (Aug 2026, 3 runs). Paying that on every ENGLISH turn too
#             would push a normal reply past this repo's <800ms conversational target,
#             so it is scoped to the turns that actually need it.
# The real fix for accented Mandarin is a native Chinese voice, which needs a paid
# ElevenLabs plan — free-tier keys are refused library voices with 402/paid_plan_required.
TTS_MODEL = os.getenv("VOICE_TTS_MODEL", "eleven_flash_v2_5")
TTS_MODEL_ZH = os.getenv("VOICE_TTS_MODEL_ZH", "eleven_multilingual_v2")
# English / default voice id (None => the ElevenLabs plugin default voice).
TTS_VOICE_ID = os.getenv("VOICE_TTS_VOICE_ID") or None
# Dedicated Chinese (中文) voice id. When the caller speaks Chinese the agent
# switches to this voice so Mandarin is spoken by a native-sounding voice instead
# of the English voice reading it with an accent. None => reuse TTS_VOICE_ID (still
# improved by the enforced zh language code below on flash/turbo v2.5).
TTS_VOICE_ID_ZH = os.getenv("VOICE_TTS_VOICE_ID_ZH") or None
# Only these models accept an explicit language_code (enforced pronunciation).
# multilingual_v2 auto-detects and rejects language_code, so we must not send it.
_LANGUAGE_ENFORCING_MODELS = frozenset(
    {"eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_v3"}
)


def language_enforced(model: str) -> bool:
    """Whether `language_code` may be sent for this TTS model. Per model, not a
    module-level flag: with per-language models in play, English can be on
    flash (enforced) while 中文 is on multilingual_v2 (which rejects it) in the
    very same call."""
    return model in _LANGUAGE_ENFORCING_MODELS
# The ElevenLabs plugin defaults to reading ELEVEN_API_KEY; we standardize on the
# clearer ELEVENLABS_API_KEY and pass it explicitly (accepting either name).
TTS_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY") or None

# --- CROSSUB Nest API (voice action endpoints) ---
# Base URL of the crossub_web Nest API the action tools call to verify tenants and
# create end-leasing records — e.g. "http://localhost:3010" locally or the Render
# staging URL. None if unset => the tools degrade gracefully (the agent tells the
# caller a team member will follow up) instead of firing broken requests.
VOICE_API_BASE_URL = os.getenv("VOICE_API_BASE_URL") or None
# Shared machine-auth secret sent as the `x-voice-service-token` header on every
# tool request; must match the Nest side's VOICE_SERVICE_TOKEN. None if unset =>
# tools degrade gracefully (never crash the call).
VOICE_SERVICE_TOKEN = os.getenv("VOICE_SERVICE_TOKEN") or None

# --- Worker ---
# When set, the worker only runs on explicit dispatch (used for SIP inbound
# routing). `console` mode ignores this and always runs locally.
AGENT_NAME = os.getenv("VOICE_AGENT_NAME", "crossub-inbound")

# LiveKit Cloud enhanced noise cancellation tuned for phone audio.
# Requires `uv sync --extra telephony` and a LiveKit Cloud project.
USE_TELEPHONY_NOISE_CANCELLATION = _flag("VOICE_TELEPHONY_NOISE_CANCELLATION", False)

# How long to wait for the caller to join the room before starting the session.
# The room is still empty the instant connect() returns, so the caller's SIP
# attributes (their phone number) only exist once they've actually joined. This is
# best-effort: on timeout the call proceeds normally, just without a phone number.
PARTICIPANT_WAIT_TIMEOUT_S = _seconds("VOICE_PARTICIPANT_WAIT_TIMEOUT_S", 10.0)

# --- Health-check HTTP server binding (for PaaS that require an open port) ---
# The LiveKit worker exposes a health endpoint. Platforms like Render/Railway run
# this as a web service and expect it to bind the platform-provided $PORT on all
# interfaces, or the deploy fails ("no open ports detected"). Locally these are
# unset and the worker keeps LiveKit's own default (8081 in prod). When $PORT is
# present we also default the host to 0.0.0.0 so the platform's port scan reaches it.
_health_port_raw = os.getenv("PORT")
HEALTH_PORT = int(_health_port_raw) if _health_port_raw and _health_port_raw.isdigit() else None
HEALTH_HOST = os.getenv("HOST") or ("0.0.0.0" if HEALTH_PORT is not None else None)
