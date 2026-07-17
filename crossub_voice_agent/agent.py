"""CROSSUB phone AI voice agent — LiveKit Agents worker (Phase 0 PoC).

Real-time pipeline: caller audio -> Deepgram STT -> Claude -> ElevenLabs TTS,
with Silero VAD + a multilingual turn-detector for natural turn-taking / barge-in.

Run modes (see README):
  uv run crossub-voice-agent console   # talk to it locally with your mic (no phone/LiveKit room)
  uv run crossub-voice-agent dev        # connect to LiveKit; for SIP inbound testing
  uv run crossub-voice-agent download-files  # prefetch VAD + turn-detector model weights
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    ConversationItemAddedEvent,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
    metrics,
)
from livekit.plugins import anthropic, deepgram, elevenlabs, silero
from livekit.plugins.elevenlabs.tts import DEFAULT_VOICE_ID
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from . import call_log
from . import config
from . import prompts
from . import tools
from .call_state import (
    TRANSCRIPT_AGENT_LABEL,
    TRANSCRIPT_CALLER_LABEL,
    CallState,
)

load_dotenv()

logger = logging.getLogger("crossub-voice-agent")

# Concrete voice ids per language. Resolve to explicit ids (never None) so every
# switch sets the voice both ways — otherwise, after a Chinese turn, an English
# turn would keep speaking English with the Chinese voice.
EN_VOICE_ID = config.TTS_VOICE_ID or DEFAULT_VOICE_ID
ZH_VOICE_ID = config.TTS_VOICE_ID_ZH or EN_VOICE_ID

# Detect the language of a turn: any CJK ideograph => Chinese; else Latin letters
# => English; else None (digits/punctuation only — keep the current voice).
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str | None:
    if _CJK_RE.search(text):
        return "zh"
    if _LATIN_RE.search(text):
        return "en"
    return None


def apply_tts_language(tts: elevenlabs.TTS, lang: str) -> None:
    """Point the ElevenLabs TTS at the right voice (and, on flash/turbo v2.5, the
    right language_code) for `lang` ('en' or 'zh'). Switching per turn keeps the
    good English voice for English and a dedicated voice for 中文, instead of one
    English-native voice reading Mandarin with an accent."""
    voice_id = ZH_VOICE_ID if lang == "zh" else EN_VOICE_ID
    if config.TTS_LANGUAGE_ENFORCED:
        tts.update_options(voice_id=voice_id, language=lang)
    else:
        tts.update_options(voice_id=voice_id)


class CrossubAssistant(Agent):
    """The CROSSUB persona, with the voice action tools (verify + token-scoped reads,
    move-out via create_end_leasing, and repair lodging via report_maintenance) that
    POST to the Nest voice API."""

    def __init__(self, tts: elevenlabs.TTS) -> None:
        # Register the Claude function-calling tools (verify/read, create_end_leasing,
        # report_maintenance) so the LLM can take real CROSSUB actions mid-call.
        super().__init__(instructions=prompts.SYSTEM_PROMPT, tools=tools.ALL_TOOLS)
        self._tts_ref = tts
        self._spoken_language: str | None = None

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Before the LLM replies, match the TTS voice to the caller's language so
        the reply is spoken naturally in the same language they used."""
        lang = detect_language(new_message.text_content or "")
        if lang and lang != self._spoken_language:
            self._spoken_language = lang
            apply_tts_language(self._tts_ref, lang)
            logger.info("Matched TTS voice/language to caller: %s", lang)


def _build_tts() -> elevenlabs.TTS:
    kwargs: dict = {"model": config.TTS_MODEL, "voice_id": EN_VOICE_ID}
    if config.TTS_API_KEY:
        kwargs["api_key"] = config.TTS_API_KEY
    return elevenlabs.TTS(**kwargs)


# The attribute LiveKit sets on an inbound SIP participant carrying the caller's
# number (ANI). Treat it as a hint for who is calling, never as authentication —
# caller ID is trivially spoofed, so the verify tools still ask for name + address.
_SIP_PHONE_ATTR = "sip.phoneNumber"


def _extract_caller_phone(participant) -> str | None:
    """Best-effort caller phone number from the inbound SIP participant's attributes.
    Returns None for non-SIP sessions (browser tester / console) or if unavailable —
    the phone number is optional everywhere it's used."""
    try:
        attrs = getattr(participant, "attributes", None) or {}
        return attrs.get(_SIP_PHONE_ATTR) or None
    except Exception:
        return None


async def _wait_for_caller(ctx: JobContext):
    """Wait for the caller to join so their attributes are readable.

    Accepts any participant kind on purpose: a phone caller joins as SIP, but the
    web tester joins as a standard participant and dispatches this agent *before*
    the browser connects — waiting for SIP alone would hang the tester forever.
    Returns None if nobody arrives in time; the call then proceeds without a phone
    number rather than stalling the worker."""
    try:
        return await asyncio.wait_for(
            ctx.wait_for_participant(), timeout=config.PARTICIPANT_WAIT_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        logger.warning(
            "No participant joined within %ss; continuing without a caller phone number.",
            config.PARTICIPANT_WAIT_TIMEOUT_S,
        )
    except Exception:
        logger.exception("Waiting for the caller failed; continuing without a phone number.")
    return None


def _room_input_options() -> RoomInputOptions:
    """Room input, optionally with LiveKit Cloud telephony noise cancellation."""
    if config.USE_TELEPHONY_NOISE_CANCELLATION:
        try:
            from livekit.plugins import noise_cancellation

            return RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony())
        except ImportError:
            logger.warning(
                "VOICE_TELEPHONY_NOISE_CANCELLATION is on but the plugin is missing. "
                "Install it with `uv sync --extra telephony`. Continuing without it."
            )
    return RoomInputOptions()


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    call_started_at = time.monotonic()

    # connect() returns before anyone is in the room, so the caller must be awaited
    # before their attributes exist to read.
    caller = await _wait_for_caller(ctx)

    # Per-call state, attached to the session as `userdata`. The verify tools stash the
    # minted token + caller name/type here; report_maintenance and the end-of-call log
    # hook read it. call_id is the LiveKit room name (stable for the whole call).
    call_state = CallState(
        call_id=ctx.room.name or f"session-{id(ctx.room):x}",
        caller_phone=_extract_caller_phone(caller) if caller else None,
    )

    tts = _build_tts()
    session: AgentSession = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, language=config.STT_LANGUAGE),
        llm=anthropic.LLM(model=config.LLM_MODEL),
        tts=tts,
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        userdata=call_state,
    )

    # Collect latency + token metrics — the Phase 0 go/no-go signal (latency & cost).
    usage = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)
        usage.collect(ev.metrics)

    # Fallback transcript accumulator: append each spoken user/agent turn to CallState.
    # The end-of-call hook prefers session.history but falls back to this. Also tracks
    # the call's language for the Comm Hub record.
    @session.on("conversation_item_added")
    def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if getattr(item, "type", None) != "message":
            return
        role = getattr(item, "role", None)
        text = (getattr(item, "text_content", None) or "").strip()
        if not text:
            return
        if role == "user":
            call_state.append_turn(TRANSCRIPT_CALLER_LABEL, text)
            lang = detect_language(text)
            if lang:
                call_state.language = lang
        elif role == "assistant":
            call_state.append_turn(TRANSCRIPT_AGENT_LABEL, text)

    async def _log_usage_summary() -> None:
        logger.info("Call usage summary: %s", usage.get_summary())

    # Automatic end-of-call Comm Hub landing (best-effort; never crashes shutdown).
    async def _land_call_in_comm_hub() -> None:
        await call_log.log_call(
            session, call_state, duration_seconds=time.monotonic() - call_started_at
        )

    ctx.add_shutdown_callback(_log_usage_summary)
    ctx.add_shutdown_callback(_land_call_in_comm_hub)

    await session.start(
        room=ctx.room,
        agent=CrossubAssistant(tts),
        room_input_options=_room_input_options(),
    )

    # Fixed-wording compliance disclosure (uninterruptible). Speak each half in its
    # own-language voice so the 中文 isn't read by the English voice with an accent.
    apply_tts_language(tts, "en")
    await session.say(prompts.DISCLOSURE_EN, allow_interruptions=False)
    apply_tts_language(tts, "zh")
    await session.say(prompts.DISCLOSURE_ZH, allow_interruptions=False)
    # Back to the English voice for the greeting; the caller's first turn then sets
    # the language for the rest of the call (see on_user_turn_completed).
    apply_tts_language(tts, "en")
    await session.generate_reply(instructions=prompts.GREETING_INSTRUCTIONS)


def main() -> None:
    opts: dict = {"entrypoint_fnc": entrypoint, "agent_name": config.AGENT_NAME}
    # On a PaaS web service (Render/Railway), bind the health server to the
    # platform-provided $PORT/host so the deploy's port scan succeeds. Unset
    # locally, keeping LiveKit's own defaults.
    if config.HEALTH_PORT is not None:
        opts["port"] = config.HEALTH_PORT
    if config.HEALTH_HOST:
        opts["host"] = config.HEALTH_HOST
    cli.run_app(WorkerOptions(**opts))


if __name__ == "__main__":
    main()
