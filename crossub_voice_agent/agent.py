"""CROSSUB phone AI voice agent — LiveKit Agents worker (Phase 0 PoC).

Real-time pipeline: caller audio -> Deepgram STT -> Claude -> ElevenLabs TTS,
with Silero VAD + a multilingual turn-detector for natural turn-taking / barge-in.

Run modes (see README):
  uv run crossub-voice-agent console   # talk to it locally with your mic (no phone/LiveKit room)
  uv run crossub-voice-agent dev        # connect to LiveKit; for SIP inbound testing
  uv run crossub-voice-agent download-files  # prefetch VAD + turn-detector model weights
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    cli,
    metrics,
)
from livekit.plugins import anthropic, deepgram, elevenlabs, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from . import config
from . import prompts

load_dotenv()

logger = logging.getLogger("crossub-voice-agent")


class CrossubAssistant(Agent):
    """The CROSSUB persona (Phase 0: canned FAQ, no backend tools yet)."""

    def __init__(self) -> None:
        super().__init__(instructions=prompts.SYSTEM_PROMPT)


def _build_tts() -> elevenlabs.TTS:
    kwargs: dict = {"model": config.TTS_MODEL}
    if config.TTS_API_KEY:
        kwargs["api_key"] = config.TTS_API_KEY
    if config.TTS_VOICE_ID:
        kwargs["voice_id"] = config.TTS_VOICE_ID
    return elevenlabs.TTS(**kwargs)


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

    session: AgentSession = AgentSession(
        stt=deepgram.STT(model=config.STT_MODEL, language=config.STT_LANGUAGE),
        llm=anthropic.LLM(model=config.LLM_MODEL),
        tts=_build_tts(),
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
    )

    # Collect latency + token metrics — the Phase 0 go/no-go signal (latency & cost).
    usage = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics(ev: MetricsCollectedEvent) -> None:
        metrics.log_metrics(ev.metrics)
        usage.collect(ev.metrics)

    async def _log_usage_summary() -> None:
        logger.info("Call usage summary: %s", usage.get_summary())

    ctx.add_shutdown_callback(_log_usage_summary)

    await session.start(
        room=ctx.room,
        agent=CrossubAssistant(),
        room_input_options=_room_input_options(),
    )

    # Fixed-wording compliance disclosure (uninterruptible), then a natural greeting.
    await session.say(prompts.DISCLOSURE, allow_interruptions=False)
    await session.generate_reply(instructions=prompts.GREETING_INSTRUCTIONS)


def main() -> None:
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=config.AGENT_NAME))


if __name__ == "__main__":
    main()
