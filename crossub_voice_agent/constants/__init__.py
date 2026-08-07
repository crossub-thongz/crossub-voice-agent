"""Shared enums and constants for the voice agent.

Anything compared against a literal belongs here rather than as a bare string in
the middle of a branch, so the set of legal values is discoverable in one place
and a typo is an ImportError instead of a silently-wrong comparison.

Leaf package — imports nothing else from `crossub_voice_agent` — so `config.py`
can depend on it without creating a cycle.
"""

from __future__ import annotations

from .voice_mode import DEFAULT_VOICE_MODE, VoiceMode

__all__ = ["DEFAULT_VOICE_MODE", "VoiceMode"]
