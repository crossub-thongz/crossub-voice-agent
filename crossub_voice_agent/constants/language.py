"""The two languages the agent speaks.

Used for per-turn TTS switching (voice + model + enforced language code) and for
the language stamped on the Comm Hub call record. Compare against these rather
than bare "en"/"zh" literals.
"""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    """A caller turn's detected language. `StrEnum`, so these compare equal to
    the plain "en"/"zh" strings the LiveKit/ElevenLabs APIs and the Nest
    log-call contract already expect — no conversion at the boundaries."""

    EN = "en"
    ZH = "zh"


#: Spoken when a turn carries no language signal at all (digits or punctuation
#: only) and there is no previous turn to inherit from.
DEFAULT_LANGUAGE = Language.EN
