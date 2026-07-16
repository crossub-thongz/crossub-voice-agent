"""Automatic end-of-call Comm Hub landing.

A session-shutdown hook (NOT an LLM-chosen tool) that lands every finished phone call
in the CROSSUB Comm Hub as a `CommChannel.VOICE` conversation carrying the full
bilingual transcript + a short AI summary, attributed to the caller's property/person
when they verified.

Flow at call end:
  1. Build the full transcript — prefer the framework's `session.history`; fall back to
     the per-turn accumulator on `CallState`.
  2. Make ONE best-effort Claude summarization call (2-3 sentence English, officer-
     facing) — reuses the `ANTHROPIC_API_KEY` already in the env for the LLM.
  3. POST `/api/voice/log-call` via the existing `tools._post()` helper with the stashed
     verification token (for attribution), caller name/phone/type, language, transcript,
     summary, duration, and outcome.

Strictly best-effort: any failure here must NEVER crash the call or the worker
shutdown. A failed/empty summary POSTs `summary=""` (the Nest side falls back to a
transcript-derived preview). The verification token is never written to the logs.
"""

from __future__ import annotations

import logging

from livekit.agents import AgentSession

from . import config
from . import tools
from .call_state import (
    TRANSCRIPT_AGENT_LABEL,
    TRANSCRIPT_CALLER_LABEL,
    CallState,
)

logger = logging.getLogger("crossub-voice-agent.call_log")

# Fixed contract shared with the Nest voice module — do not rename.
_LOG_CALL_PATH = "/api/voice/log-call"

# One tight summarization call at end-of-call. Reuses the same Claude model as the
# live LLM (config.LLM_MODEL) and the ANTHROPIC_API_KEY already in the environment —
# no new config keys.
_SUMMARY_MAX_TOKENS = 220
_SUMMARY_SYSTEM = (
    "You summarize a recorded phone call for an Australian property-management officer "
    "who will read it in their Comm Hub inbox. Reply with 2 to 3 plain English "
    "sentences of prose only — no lists, no markdown, no preamble. State who called and "
    "why, and explicitly note any action taken during the call (for example a "
    "maintenance request reference like MR-00123, or a move-out / vacate notice). If "
    "nothing was actioned, say the caller made a general enquiry. Never invent details "
    "that are not in the transcript."
)


def _transcript_from_history(session: AgentSession) -> str:
    """Render the caller<->agent transcript from the framework's own conversation
    history (session.history). Only spoken message turns are included; tool calls,
    tool outputs, and system/developer items are skipped."""
    lines: list[str] = []
    for item in session.history.items:
        if getattr(item, "type", None) != "message":
            continue
        role = getattr(item, "role", None)
        if role not in ("user", "assistant"):
            continue
        text = (getattr(item, "text_content", None) or "").strip()
        if not text:
            continue
        label = TRANSCRIPT_CALLER_LABEL if role == "user" else TRANSCRIPT_AGENT_LABEL
        lines.append(f"{label}: {text}")
    return "\n".join(lines)


def build_transcript(session: AgentSession, state: CallState) -> str:
    """Full transcript for the call: prefer session.history, fall back to the per-turn
    accumulator on CallState if history is unavailable/empty. Never raises."""
    try:
        transcript = _transcript_from_history(session)
    except Exception as exc:  # session torn down, unexpected item shape, etc.
        logger.warning("Could not read session.history for transcript: %s", exc)
        transcript = ""
    if transcript.strip():
        return transcript
    return "\n".join(state.transcript_lines)


async def summarize_call(transcript: str, state: CallState) -> str:
    """One best-effort Claude call → a 2-3 sentence officer-facing English summary.
    Returns "" on ANY failure (missing key, network, bad response) so the caller can
    still be logged with a transcript-derived preview on the Nest side."""
    if not transcript.strip():
        return ""

    # Import lazily so a missing/optional Anthropic SDK never breaks module import or
    # the rest of the shutdown hook.
    try:
        import anthropic
    except Exception as exc:  # pragma: no cover - dependency always present in practice
        logger.warning("Summary skipped — anthropic SDK unavailable: %s", exc)
        return ""

    hint = ""
    if state.actions:
        hint = f"\nActions the agent recorded during the call: {state.outcome}."
    user_content = (
        f"Caller type: {state.caller_type or 'unknown / unverified'}."
        f"{hint}\n\nTranscript:\n{transcript}"
    )

    client = None
    try:
        client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the env
        message = await client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=_SUMMARY_MAX_TOKENS,
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:  # auth, network, rate limit, bad response — all degrade
        logger.warning("Call summary generation failed (posting summary=''): %s", exc)
        return ""
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass

    parts = [
        block.text
        for block in getattr(message, "content", [])
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip()


def build_log_call_payload(
    state: CallState,
    transcript: str,
    summary: str,
    duration_seconds: float | None,
) -> dict:
    """Assemble the `/api/voice/log-call` body from the call state. `callId` +
    `transcript` are required; the verification token (attribution) and the caller
    fields are included only when known. Pure function so it can be asserted in tests."""
    payload: dict = {
        "callId": state.call_id,
        "transcript": transcript,
        "summary": summary or "",
        "outcome": state.outcome,
    }
    if state.verification_token:
        payload["verificationToken"] = state.verification_token
    if state.caller_name:
        payload["callerName"] = state.caller_name
    if state.caller_phone:
        payload["callerPhone"] = state.caller_phone
    if state.language:
        payload["language"] = state.language
    if duration_seconds is not None:
        payload["durationSeconds"] = int(duration_seconds)
    return payload


async def log_call(
    session: AgentSession,
    state: CallState,
    duration_seconds: float | None = None,
) -> None:
    """The end-of-call hook body: build the transcript, summarize (best-effort), and
    POST it to the Comm Hub. Wrapped so it can NEVER crash the shutdown — any error is
    swallowed after logging."""
    try:
        transcript = build_transcript(session, state)
        if not transcript.strip():
            logger.info(
                "Call %s produced no transcript; skipping Comm Hub log", state.call_id
            )
            return

        summary = await summarize_call(transcript, state)
        payload = build_log_call_payload(state, transcript, summary, duration_seconds)
        result = await tools._post(_LOG_CALL_PATH, payload)
        # Never log the token/payload — only the coarse outcome + sizes.
        logger.info(
            "log-call for %s -> logged=%s (transcript_chars=%d, summary_chars=%d, "
            "attributed=%s)",
            state.call_id,
            result.get("logged"),
            len(transcript),
            len(summary),
            bool(state.verification_token),
        )
    except Exception as exc:  # absolutely never crash shutdown
        logger.error("Best-effort call log failed for %s: %s", state.call_id, exc)
