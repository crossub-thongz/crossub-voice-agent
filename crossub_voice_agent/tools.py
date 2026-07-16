"""Claude function-calling tools that let the voice agent take real actions in
CROSSUB by POSTing to the Nest `voice` module endpoints.

Two tools back the phone move-out flow:
  - verify_tenant(name, address)                 -> POST /api/voice/verify-tenant
  - create_end_leasing(property_id, move_out_date, caller_name)
                                                  -> POST /api/voice/end-leasing

The identity + account-reads flow adds an identity check plus five token-scoped
reads (the caller asks about their OWN rent / inspection / maintenance / lease):
  - verify_identity(name, address)               -> POST /api/voice/verify-identity
  - get_account_summary(verification_token)      -> POST /api/voice/tenant/account-summary
  - get_rent_status(verification_token)          -> POST /api/voice/tenant/rent
  - get_next_inspection(verification_token)      -> POST /api/voice/tenant/next-inspection
  - get_maintenance_status(verification_token, reference?)
                                                  -> POST /api/voice/tenant/maintenance-status
  - get_lease_details(verification_token)        -> POST /api/voice/tenant/lease
On a successful verify_identity the Nest side mints a short-lived HMAC verification
token (returned as `verificationToken`) that scopes every later read to this
caller's property only. The token is threaded back into each read call's body as
`verificationToken` and is NEVER spoken to the caller.

Every call is authenticated to the Nest side with the `x-voice-service-token`
header (shared machine secret, see config.VOICE_SERVICE_TOKEN).

Defensive by design: a live phone call must never crash because the backend is
slow, down, or unconfigured. Missing config, a non-2xx response, a bad body, or a
network error all return a STRUCTURED dict (`{"ok": false, ...}`) the LLM can act
on — no tool ever raises. The JSON field names below are a FIXED contract shared
with the Nest `voice` module; do not rename them.
"""

from __future__ import annotations

import logging

import httpx
from livekit.agents import function_tool

from . import config

logger = logging.getLogger("crossub-voice-agent.tools")

# A phone call cannot hang waiting on HTTP. Keep the whole request tight so the
# tool resolves (to a real answer or a graceful degrade) well within a turn.
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_VERIFY_TENANT_PATH = "/api/voice/verify-tenant"
_END_LEASING_PATH = "/api/voice/end-leasing"

# Identity + token-scoped tenant reads (Phase 1). The verification token minted by
# verify-identity is threaded into every read body as `verificationToken` (fixed
# contract shared with the Nest voice module — do not rename).
_VERIFY_IDENTITY_PATH = "/api/voice/verify-identity"
_ACCOUNT_SUMMARY_PATH = "/api/voice/tenant/account-summary"
_RENT_STATUS_PATH = "/api/voice/tenant/rent"
_NEXT_INSPECTION_PATH = "/api/voice/tenant/next-inspection"
_MAINTENANCE_STATUS_PATH = "/api/voice/tenant/maintenance-status"
_LEASE_DETAILS_PATH = "/api/voice/tenant/lease"


def _service_configured() -> bool:
    """True only when BOTH the base URL and the machine-auth token are set. Either
    one missing => the tools degrade gracefully instead of firing broken requests."""
    return bool(config.VOICE_API_BASE_URL and config.VOICE_SERVICE_TOKEN)


async def _post(path: str, payload: dict) -> dict:
    """POST `payload` to the Nest voice API at `path` with machine-auth.

    Returns the parsed JSON body on a 2xx response, otherwise a structured
    `{"ok": false, "reason": ...}` dict. Never raises — the caller (and ultimately
    the LLM) always gets a dict it can reason about.
    """
    if not _service_configured():
        logger.warning(
            "Voice API not configured (VOICE_API_BASE_URL / VOICE_SERVICE_TOKEN); "
            "tool %s degraded to graceful-fallback",
            path,
        )
        return {"ok": False, "reason": "service_unconfigured"}

    url = f"{config.VOICE_API_BASE_URL.rstrip('/')}{path}"
    headers = {"x-voice-service-token": config.VOICE_SERVICE_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        # Timeout, connection refused, DNS failure, etc. — degrade, don't crash.
        logger.error("Voice API request to %s failed: %s", path, exc)
        return {"ok": False, "reason": "network_error"}

    # Accept any 2xx (NestJS returns 201 for POST by default) as success.
    if not resp.is_success:
        logger.error("Voice API %s returned HTTP %s", path, resp.status_code)
        return {"ok": False, "reason": f"http_{resp.status_code}"}

    try:
        data = resp.json()
    except ValueError:
        logger.error("Voice API %s returned a non-JSON body", path)
        return {"ok": False, "reason": "bad_response"}

    if not isinstance(data, dict):
        logger.error("Voice API %s returned a non-object JSON body: %r", path, data)
        return {"ok": False, "reason": "bad_response"}

    # Mark reachable responses so the LLM can distinguish a real answer (ok:true)
    # from a graceful degrade (ok:false) regardless of the domain fields.
    data.setdefault("ok", True)
    return data


@function_tool
async def verify_tenant(name: str, address: str) -> dict:
    """Verify that a caller is the tenant of a CROSSUB-managed property before taking
    any account action for them. Call this once you have BOTH the caller's full name
    AND their property address. Never reveal the internal match outcome or reason to
    the caller.

    Args:
        name: The caller's full name, exactly as they gave it.
        address: The property's street address, exactly as the caller gave it.

    Returns:
        A dict. When the backend is reachable: {"verified": bool, "propertyId"?: str,
        "matchedTenantName"?: str, "reason"?: str}. When it is unreachable or not
        configured: {"ok": false, ...} — treat that as "cannot verify right now" and
        tell the caller a team member will follow up.
    """
    result = await _post(_VERIFY_TENANT_PATH, {"name": name, "address": address})
    logger.info("verify_tenant(name=%r, address=%r) -> %s", name, address, result)
    return result


@function_tool
async def create_end_leasing(property_id: str, move_out_date: str, caller_name: str) -> dict:
    """Lodge an end-of-lease (move-out / vacate) record for a verified tenant. ONLY
    call this AFTER verify_tenant returned verified:true AND the caller has explicitly
    said "yes" to your read-back of their name, address, and move-out date. This does
    not finalize anything — a CROSSUB officer processes the case afterwards.

    Args:
        property_id: The propertyId returned by verify_tenant. Never invent it.
        move_out_date: The move-out date in ISO format, YYYY-MM-DD.
        caller_name: The caller's name (the matched tenant name from verify_tenant).

    Returns:
        A dict. When the backend is reachable: {"created": bool, "taskNumber"?: str,
        "reason"?: str}. When it is unreachable or not configured: {"ok": false, ...}.
        NEVER tell the caller a record was created unless this returned created:true.
    """
    result = await _post(
        _END_LEASING_PATH,
        {
            "propertyId": property_id,
            "moveOutDate": move_out_date,
            "callerName": caller_name,
        },
    )
    logger.info(
        "create_end_leasing(property_id=%r, move_out_date=%r, caller_name=%r) -> %s",
        property_id,
        move_out_date,
        caller_name,
        result,
    )
    return result


def _redact_token(result: dict) -> dict:
    """Return a copy of `result` with any minted verification token masked. The
    prompt already forbids speaking it aloud; this keeps the security credential out
    of the log sinks too."""
    if "verificationToken" in result:
        return {**result, "verificationToken": "***"}
    return result


async def _tenant_read(
    path: str, verification_token: str, reference: str | None = None
) -> dict:
    """Shared body for the token-scoped tenant read tools: POST the caller's
    verification token (and optional reference) and return the raw JSON dict for the
    LLM to speak. The token — not any LLM-supplied id — is what scopes the read to
    this caller's property, so it is the only auth material these tools send."""
    payload: dict = {"verificationToken": verification_token}
    if reference is not None:
        payload["reference"] = reference
    return await _post(path, payload)


@function_tool
async def verify_identity(name: str, address: str) -> dict:
    """Verify a caller's identity by full name + property address BEFORE answering any
    question about their OWN account — rent, upcoming inspection, maintenance, or
    lease. Call this once you have BOTH the caller's full name AND their property
    address. On success the backend mints a short-lived verification token that
    scopes every later read tool to this caller's property only; keep it in context
    and pass it to the read tools. Never reveal the match outcome, the reason, or the
    token to the caller.

    Args:
        name: The caller's full name, exactly as they gave it.
        address: The property's street address, exactly as the caller gave it.

    Returns:
        A dict. When the backend is reachable: {"verified": bool,
        "verificationToken"?: str, "matchedName"?: str, "propertyAddress"?: str,
        "reason"?: str}. When verified is true, use the "verificationToken" value as
        the verification_token argument for every read tool — and NEVER speak it.
        When it is unreachable or not configured: {"ok": false, ...} — treat that as
        "cannot verify right now" and tell the caller a team member will follow up.
        NEVER reveal to the caller whether or why verification failed.
    """
    result = await _post(_VERIFY_IDENTITY_PATH, {"name": name, "address": address})
    logger.info(
        "verify_identity(name=%r, address=%r) -> %s", name, address, _redact_token(result)
    )
    return result


@function_tool
async def get_account_summary(verification_token: str) -> dict:
    """Get a friendly overview of the verified caller's tenancy (property, key dates,
    a snapshot of what's active). ONLY call this after verify_identity returned
    verified:true, passing its verificationToken. Answer only what the caller asked.

    Args:
        verification_token: The verificationToken returned by verify_identity. Never
            invent it and never read it aloud.

    Returns:
        A dict. When reachable: a free-form account-summary object to speak from.
        When unreachable/unconfigured or the token is rejected: {"ok": false, ...} —
        tell the caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(_ACCOUNT_SUMMARY_PATH, verification_token)
    logger.info("get_account_summary(token=***) -> %s", result)
    return result


@function_tool
async def get_rent_status(verification_token: str) -> dict:
    """Get the verified caller's rent status — the weekly rent amount and the date
    rent is paid up to. ONLY call this after verify_identity returned verified:true,
    passing its verificationToken. This does NOT include any arrears or amount owing
    figure — that is intentionally unavailable, so never state or guess one.

    Args:
        verification_token: The verificationToken returned by verify_identity. Never
            invent it and never read it aloud.

    Returns:
        A dict. When reachable: {"weeklyRent"?, "rentPaidUntil"?, "currency"?, ...}
        (no arrears). When unreachable/unconfigured or the token is rejected:
        {"ok": false, ...} — tell the caller you couldn't retrieve it and a team
        member will follow up.
    """
    result = await _tenant_read(_RENT_STATUS_PATH, verification_token)
    logger.info("get_rent_status(token=***) -> %s", result)
    return result


@function_tool
async def get_next_inspection(verification_token: str) -> dict:
    """Get the verified caller's next upcoming inspection (including routine
    inspections) so you can tell them when someone is next coming to the property.
    ONLY call this after verify_identity returned verified:true, passing its
    verificationToken.

    Args:
        verification_token: The verificationToken returned by verify_identity. Never
            invent it and never read it aloud.

    Returns:
        A dict describing the next inspection, or one indicating there is none. When
        unreachable/unconfigured or the token is rejected: {"ok": false, ...} — tell
        the caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(_NEXT_INSPECTION_PATH, verification_token)
    logger.info("get_next_inspection(token=***) -> %s", result)
    return result


@function_tool
async def get_maintenance_status(
    verification_token: str, reference: str | None = None
) -> dict:
    """Get the status of the verified caller's maintenance requests for their
    property. ONLY call this after verify_identity returned verified:true, passing its
    verificationToken. If the caller mentions a specific reference or job number, pass
    it as `reference` to narrow the result; otherwise omit it to list their requests.

    Args:
        verification_token: The verificationToken returned by verify_identity. Never
            invent it and never read it aloud.
        reference: Optional maintenance request reference / job number the caller
            gave, to look up one specific request.

    Returns:
        A dict describing the caller's maintenance request(s). When
        unreachable/unconfigured or the token is rejected: {"ok": false, ...} — tell
        the caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(_MAINTENANCE_STATUS_PATH, verification_token, reference)
    logger.info("get_maintenance_status(token=***, reference=%r) -> %s", reference, result)
    return result


@function_tool
async def get_lease_details(verification_token: str) -> dict:
    """Get the verified caller's lease / tenancy details (e.g. term dates and key
    lease facts) so you can answer questions about their own tenancy. ONLY call this
    after verify_identity returned verified:true, passing its verificationToken.

    Args:
        verification_token: The verificationToken returned by verify_identity. Never
            invent it and never read it aloud.

    Returns:
        A dict describing the lease / tenancy. When unreachable/unconfigured or the
        token is rejected: {"ok": false, ...} — tell the caller you couldn't retrieve
        it and a team member will follow up.
    """
    result = await _tenant_read(_LEASE_DETAILS_PATH, verification_token)
    logger.info("get_lease_details(token=***) -> %s", result)
    return result


# Registered on the Agent (see agent.CrossubAssistant). Exposed as one list so the
# agent wires up "all voice action tools" without knowing their individual names.
ALL_TOOLS = [
    verify_tenant,
    create_end_leasing,
    verify_identity,
    get_account_summary,
    get_rent_status,
    get_next_inspection,
    get_maintenance_status,
    get_lease_details,
]
