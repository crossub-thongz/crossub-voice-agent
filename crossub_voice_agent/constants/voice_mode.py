"""How the agent behaves on an answered call.

CROSSUB publishes a single Calilio number and it is tenant-facing, so every
inbound call is a tenant enquiry. The business decision (Aug 2026) is that those
enquiries are handled on the **email** channel, where `support@crossub.com.au`
already runs AI triage AND AI auto-reply end to end
(`support-email-triage.service.ts` in crossub_web). The phone's job is therefore
to get the enquiry onto that channel, not to answer it.

`DIVERT` is the default. `FULL` is the earlier self-service assistant (verify the
caller, read their rent/lease/inspection data, lodge move-outs and repairs) kept
intact behind this switch so the pivot is reversible without a revert.
"""

from __future__ import annotations

from enum import StrEnum


class VoiceMode(StrEnum):
    """The two answer behaviours. Compare against these, never a bare string."""

    #: Hear the gist, point the caller at the email channel, take no action.
    #: The agent is given NO tools in this mode, so it cannot read or write
    #: anything even if the model were talked into trying.
    DIVERT = "divert"

    #: The full self-service assistant: identity verification + role-scoped
    #: reads + move-out / maintenance / contractor-note writes.
    FULL = "full"


#: Applied when `VOICE_MODE` is unset or not a recognised value. Defaults to the
#: safer behaviour: an agent with no tools cannot leak or write anything.
DEFAULT_VOICE_MODE = VoiceMode.DIVERT
