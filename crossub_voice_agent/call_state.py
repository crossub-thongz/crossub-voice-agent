"""Per-call mutable state shared between the voice agent's tools and the
automatic end-of-call Comm Hub log hook.

One `CallState` lives for the lifetime of one phone call (one `AgentSession`). It is
attached to the session as `userdata`, so any `@function_tool` can read/update it via
`context.userdata`, and the shutdown hook can read the accumulated transcript + the
stashed verification details when it lands the call in the Comm Hub.

Design notes:
- The verification token is kept here so `report_maintenance` and the log-call hook
  can use it, but it is a security credential: every log statement redacts it and it
  is NEVER spoken to the caller.
- Leaf module — imports nothing from this package — so both `tools.py` and
  `call_log.py` can depend on it without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Transcript speaker labels, shared by the live per-turn accumulator (agent.py) and
# the session.history builder (call_log.py) so both render identical "Caller:" /
# "Agent:" lines. Centralized here — no raw label strings elsewhere.
TRANSCRIPT_CALLER_LABEL = "Caller"
TRANSCRIPT_AGENT_LABEL = "Agent"

# Outcome recorded on the log-call POST when the caller took no lodging action.
OUTCOME_GENERAL_ENQUIRY = "general_enquiry"


@dataclass
class CallState:
    """Everything the maintenance tool + the end-of-call log hook need, gathered as
    the call progresses. `call_id` is stable for the whole call (the LiveKit room
    name); the rest is filled in by the verify tools and the transcript accumulator."""

    call_id: str
    caller_phone: str | None = None
    caller_name: str | None = None
    caller_type: str | None = None
    verification_token: str | None = None
    # The verified caller's own property address (from verify-identity's
    # `propertyAddress`). Harmless for a tenant; used as the DEFAULT lodge property
    # for a landlord who owns a single property so report_maintenance need not
    # re-supply an address.
    caller_property_address: str | None = None
    language: str | None = None
    # Append-only fallback transcript accumulator ("Caller:"/"Agent:" lines). The
    # shutdown hook prefers the framework's session.history and only falls back here.
    transcript_lines: list[str] = field(default_factory=list)
    # Human-readable actions taken this call (e.g. "maintenance MR-00123",
    # "vacate notice") — feeds the summary hint + the log-call `outcome` field.
    actions: list[str] = field(default_factory=list)

    def stash_verification(self, result: dict, caller_type: str) -> None:
        """After a verify tool call, remember the minted token + the matched caller
        name + the caller type so `report_maintenance` and the log hook can use them.
        No-op unless the backend actually verified (verified:true with a token), so a
        failed/unreachable verify never overwrites a good earlier one."""
        if not isinstance(result, dict) or not result.get("verified"):
            return
        token = result.get("verificationToken")
        if isinstance(token, str) and token:
            self.verification_token = token
        self.caller_type = caller_type
        # verify_identity/landlord return `matchedName`; verify_tenant returns
        # `matchedTenantName`. Prefer either without ever guessing.
        name = result.get("matchedName") or result.get("matchedTenantName")
        if isinstance(name, str) and name:
            self.caller_name = name
        # The verified property's address, used as the landlord lodge's default
        # property (harmless for a tenant, whose verify result may omit it).
        address = result.get("propertyAddress")
        if isinstance(address, str) and address:
            self.caller_property_address = address

    def record_action(self, action: str) -> None:
        """Note a lodging action for the summary hint + the log-call outcome."""
        if action:
            self.actions.append(action)

    def append_turn(self, speaker: str, text: str) -> None:
        """Accumulate one spoken turn into the fallback transcript."""
        text = (text or "").strip()
        if text:
            self.transcript_lines.append(f"{speaker}: {text}")

    @property
    def outcome(self) -> str:
        """A short outcome string for the log-call POST (e.g. the actions taken, or a
        general-enquiry marker when nothing was lodged)."""
        return "; ".join(self.actions) if self.actions else OUTCOME_GENERAL_ENQUIRY
