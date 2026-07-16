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

Landlord/owner and contractor/tradie callers reuse the same two-gate model, each with
its OWN verify tool (separate per caller type so the LLM's branch is unambiguous) and a
set of token-scoped reads. The minted token carries the caller type, so a landlord token
can only read landlord endpoints and a contractor token only contractor endpoints:
  - verify_landlord_identity(name, address)      -> POST /api/voice/verify-identity (callerType 'landlord')
  - get_landlord_account_summary / get_landlord_portfolio / get_landlord_maintenance /
    get_landlord_next_inspection / get_landlord_income
                                                  -> POST /api/voice/landlord/*
  - verify_contractor_identity(name, reference)  -> POST /api/voice/verify-identity (callerType 'contractor')
  - get_contractor_jobs / get_contractor_job_status
                                                  -> POST /api/voice/contractor/*
Each verify tool mints its own callerType-scoped token; the read tools thread it back
into their body as `verificationToken` exactly as the tenant reads do, and it is NEVER
spoken to the caller.

Phase 2/3 add WRITE actions plus per-call state:
  - report_maintenance(description, urgent=False, address?)
                                                  -> POST /api/voice/maintenance
  - log_job_update(reference, note, urgent=False) -> POST /api/voice/contractor/job-update
A verified TENANT **or property OWNER** can lodge a repair mid-call via
report_maintenance (the SERVER routes tenant-vs-owner from the token; an owner names the
property via the optional `address`), and a verified CONTRACTOR can log a note/update on
one of their own jobs via log_job_update. Both take no token/property/id from the LLM —
instead every verify tool stashes its minted `verificationToken`, the matched caller
name, the caller type, and the verified property address onto a per-call `CallState`
(attached to the session as `userdata`), and the write tools read that stash to POST the
token + call id + caller name. The same CallState feeds the automatic end-of-call Comm
Hub log hook (see agent.py / call_log.py).

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
from livekit.agents import RunContext, function_tool

from . import config
from .call_state import CallState

logger = logging.getLogger("crossub-voice-agent.tools")

# A phone call cannot hang waiting on HTTP. Keep the whole request tight so the
# tool resolves (to a real answer or a graceful degrade) well within a turn.
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_VERIFY_TENANT_PATH = "/api/voice/verify-tenant"
_END_LEASING_PATH = "/api/voice/end-leasing"

# Maintenance-request lodging (Phase 2/3). A verified TENANT or property OWNER reports a
# repair mid-call; report_maintenance POSTs the STORED verification token + call id (and,
# for a multi-property owner, an `address`) here so the Nest side derives the property
# from the token + address (never a body id) and routes tenant-vs-owner by the token's
# callerType. Fixed contract shared with the Nest voice module — do not rename.
_MAINTENANCE_PATH = "/api/voice/maintenance"

# Identity + token-scoped tenant reads (Phase 1). The verification token minted by
# verify-identity is threaded into every read body as `verificationToken` (fixed
# contract shared with the Nest voice module — do not rename).
_VERIFY_IDENTITY_PATH = "/api/voice/verify-identity"
_ACCOUNT_SUMMARY_PATH = "/api/voice/tenant/account-summary"
_RENT_STATUS_PATH = "/api/voice/tenant/rent"
_NEXT_INSPECTION_PATH = "/api/voice/tenant/next-inspection"
_MAINTENANCE_STATUS_PATH = "/api/voice/tenant/maintenance-status"
_LEASE_DETAILS_PATH = "/api/voice/tenant/lease"

# The caller-type discriminator the shared verify-identity endpoint branches on. The
# tenant path omits it on the wire (defaults to tenant on the Nest side); landlord and
# contractor callers each get a dedicated verify tool that sends its own literal here.
# The same values are also stashed into CallState.caller_type so the log hook can
# attribute the call and report_maintenance stays TENANT-scoped. Fixed contract shared
# with the Nest voice module's CallerType — do not rename.
_CALLER_TYPE_TENANT = "tenant"
_CALLER_TYPE_LANDLORD = "landlord"
_CALLER_TYPE_CONTRACTOR = "contractor"

# Token-scoped LANDLORD/OWNER reads (Phase 1 extension). The landlord verification
# token minted by verify-identity (callerType 'landlord') is threaded into every read
# body as `verificationToken`, exactly like the tenant reads (do not rename).
_LANDLORD_ACCOUNT_SUMMARY_PATH = "/api/voice/landlord/account-summary"
_LANDLORD_PROPERTIES_PATH = "/api/voice/landlord/properties"
_LANDLORD_MAINTENANCE_STATUS_PATH = "/api/voice/landlord/maintenance-status"
_LANDLORD_NEXT_INSPECTION_PATH = "/api/voice/landlord/next-inspection"
_LANDLORD_INCOME_PATH = "/api/voice/landlord/income"

# Token-scoped CONTRACTOR/TRADIE reads (Phase 1 extension). The contractor
# verification token (callerType 'contractor') scopes reads to that contractor's own
# jobs; job-status also carries the caller-supplied work-order/reference number.
_CONTRACTOR_JOBS_PATH = "/api/voice/contractor/jobs"
_CONTRACTOR_JOB_STATUS_PATH = "/api/voice/contractor/job-status"

# Contractor job-update WRITE (Phase 3). A verified CONTRACTOR leaves a note/update on
# ONE of their own jobs; log_job_update POSTs the STORED verification token + call id
# here so the Nest side derives the job from the token + reference (never a body id).
# Fixed contract shared with the Nest voice module — do not rename.
_CONTRACTOR_JOB_UPDATE_PATH = "/api/voice/contractor/job-update"


def _service_configured() -> bool:
    """True only when BOTH the base URL and the machine-auth token are set. Either
    one missing => the tools degrade gracefully instead of firing broken requests."""
    return bool(config.VOICE_API_BASE_URL and config.VOICE_SERVICE_TOKEN)


def _call_state(context: RunContext) -> CallState | None:
    """Return the per-call CallState attached to the session as `userdata`, or None if
    it is missing/unexpected. The verify tools stash the minted token + caller details
    onto it; report_maintenance and the log hook read them back. Defensive — a missing
    or wrongly-typed userdata never crashes a tool."""
    try:
        state = context.userdata
    except Exception:  # userdata unset on the session, etc.
        return None
    return state if isinstance(state, CallState) else None


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
async def verify_tenant(context: RunContext, name: str, address: str) -> dict:
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
    state = _call_state(context)
    if state is not None:
        state.stash_verification(result, _CALLER_TYPE_TENANT)
    logger.info("verify_tenant(name=%r, address=%r) -> %s", name, address, result)
    return result


@function_tool
async def create_end_leasing(
    context: RunContext, property_id: str, move_out_date: str, caller_name: str
) -> dict:
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
    # Note the action so the end-of-call summary can flag the vacate notice. Never
    # blocks the caller — pure best-effort bookkeeping.
    if result.get("created"):
        state = _call_state(context)
        if state is not None:
            task = result.get("taskNumber")
            state.record_action(f"vacate notice {task}" if task else "vacate notice")
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
    """Shared body for every token-scoped read tool (tenant, landlord, and contractor):
    POST the caller's verification token (and optional reference) and return the raw
    JSON dict for the LLM to speak. The token — not any LLM-supplied id — is what scopes
    the read to this caller (and caller type), so it is the only auth material these
    tools send."""
    payload: dict = {"verificationToken": verification_token}
    if reference is not None:
        payload["reference"] = reference
    return await _post(path, payload)


@function_tool
async def verify_identity(context: RunContext, name: str, address: str) -> dict:
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
    state = _call_state(context)
    if state is not None:
        state.stash_verification(result, _CALLER_TYPE_TENANT)
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


@function_tool
async def verify_landlord_identity(
    context: RunContext, name: str, address: str
) -> dict:
    """Verify a caller who says they are the LANDLORD / OWNER of a CROSSUB-managed
    property, BEFORE answering any question about that owner's OWN property, tenancy,
    maintenance, inspection, or income. Call this once you have BOTH the caller's full
    name AND the address of a property they own. On success the backend mints a
    short-lived verification token scoped to THIS owner's own properties only; keep it
    in context and pass it to the landlord read tools. Never reveal the match outcome,
    the reason, or the token to the caller, and never mix it with tenant or contractor
    data.

    Args:
        name: The caller's full name, exactly as they gave it.
        address: The street address of a property they say they own, exactly as given.

    Returns:
        A dict. When the backend is reachable: {"verified": bool,
        "verificationToken"?: str, "matchedName"?: str, "propertyAddress"?: str,
        "reason"?: str}. When verified is true, use the "verificationToken" value as the
        verification_token argument for every landlord read tool — and NEVER speak it.
        When it is unreachable or not configured: {"ok": false, ...} — treat that as
        "cannot verify right now" and tell the caller a team member will follow up.
        NEVER reveal to the caller whether or why verification failed.
    """
    result = await _post(
        _VERIFY_IDENTITY_PATH,
        {"name": name, "address": address, "callerType": _CALLER_TYPE_LANDLORD},
    )
    state = _call_state(context)
    if state is not None:
        state.stash_verification(result, _CALLER_TYPE_LANDLORD)
    logger.info(
        "verify_landlord_identity(name=%r, address=%r) -> %s",
        name,
        address,
        _redact_token(result),
    )
    return result


@function_tool
async def verify_contractor_identity(
    context: RunContext, name: str, reference: str
) -> dict:
    """Verify a caller who says they are the CONTRACTOR / TRADIE assigned to CROSSUB
    work, BEFORE answering any question about their jobs. Call this once you have BOTH
    the caller's full name AND a work-order / reference number for one of their jobs. On
    success the backend mints a short-lived verification token scoped to THIS
    contractor's own jobs only; keep it in context and pass it to the contractor read
    tools. Never reveal the match outcome, the reason, or the token to the caller, and
    never mix it with tenant or landlord data.

    Args:
        name: The caller's full name, exactly as they gave it.
        reference: The work-order / job reference number the caller gave, exactly as
            given.

    Returns:
        A dict. When the backend is reachable: {"verified": bool,
        "verificationToken"?: str, "matchedName"?: str, "reason"?: str}. When verified
        is true, use the "verificationToken" value as the verification_token argument
        for every contractor read tool — and NEVER speak it. When it is unreachable or
        not configured: {"ok": false, ...} — treat that as "cannot verify right now"
        and tell the caller a team member will follow up. NEVER reveal to the caller
        whether or why verification failed.
    """
    result = await _post(
        _VERIFY_IDENTITY_PATH,
        {"name": name, "reference": reference, "callerType": _CALLER_TYPE_CONTRACTOR},
    )
    state = _call_state(context)
    if state is not None:
        state.stash_verification(result, _CALLER_TYPE_CONTRACTOR)
    logger.info(
        "verify_contractor_identity(name=%r, reference=%r) -> %s",
        name,
        reference,
        _redact_token(result),
    )
    return result


@function_tool
async def get_landlord_account_summary(verification_token: str) -> dict:
    """Get a friendly overview of the verified LANDLORD's account (their properties, a
    snapshot of what's active). ONLY call this after verify_landlord_identity returned
    verified:true, passing its verificationToken. Answer only what the caller asked, and
    only about their OWN properties.

    Args:
        verification_token: The verificationToken returned by verify_landlord_identity.
            Never invent it and never read it aloud.

    Returns:
        A dict. When reachable: a free-form owner account-summary object to speak from.
        When unreachable/unconfigured or the token is rejected: {"ok": false, ...} —
        tell the caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(_LANDLORD_ACCOUNT_SUMMARY_PATH, verification_token)
    logger.info("get_landlord_account_summary(token=***) -> %s", result)
    return result


@function_tool
async def get_landlord_portfolio(verification_token: str) -> dict:
    """List the verified LANDLORD's own properties with each one's occupancy status and
    the tenant's NAME (never the tenant's email or phone). ONLY call this after
    verify_landlord_identity returned verified:true, passing its verificationToken.

    Args:
        verification_token: The verificationToken returned by verify_landlord_identity.
            Never invent it and never read it aloud.

    Returns:
        A dict describing the owner's properties (address, occupancy status, tenant
        name). When unreachable/unconfigured or the token is rejected: {"ok": false,
        ...} — tell the caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(_LANDLORD_PROPERTIES_PATH, verification_token)
    logger.info("get_landlord_portfolio(token=***) -> %s", result)
    return result


@function_tool
async def get_landlord_maintenance(
    verification_token: str, reference: str | None = None
) -> dict:
    """Get the status of maintenance requests on the verified LANDLORD's own
    properties. ONLY call this after verify_landlord_identity returned verified:true,
    passing its verificationToken. If the caller mentions a specific reference or job
    number, pass it as `reference` to narrow the result; otherwise omit it to list the
    requests across their properties.

    Args:
        verification_token: The verificationToken returned by verify_landlord_identity.
            Never invent it and never read it aloud.
        reference: Optional maintenance request reference / job number the caller gave,
            to look up one specific request.

    Returns:
        A dict describing the maintenance request(s) on the owner's properties. When
        unreachable/unconfigured or the token is rejected: {"ok": false, ...} — tell the
        caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(
        _LANDLORD_MAINTENANCE_STATUS_PATH, verification_token, reference
    )
    logger.info(
        "get_landlord_maintenance(token=***, reference=%r) -> %s", reference, result
    )
    return result


@function_tool
async def get_landlord_next_inspection(verification_token: str) -> dict:
    """Get the next upcoming inspection across the verified LANDLORD's own properties so
    you can tell them when someone is next attending one of their properties. ONLY call
    this after verify_landlord_identity returned verified:true, passing its
    verificationToken.

    Args:
        verification_token: The verificationToken returned by verify_landlord_identity.
            Never invent it and never read it aloud.

    Returns:
        A dict describing the next inspection, or one indicating there is none. When
        unreachable/unconfigured or the token is rejected: {"ok": false, ...} — tell the
        caller you couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(_LANDLORD_NEXT_INSPECTION_PATH, verification_token)
    logger.info("get_landlord_next_inspection(token=***) -> %s", result)
    return result


@function_tool
async def get_landlord_income(verification_token: str) -> dict:
    """Get the verified LANDLORD's income position for their OWN properties — the
    rent-received status, any arrears amount owed on their properties, and the net
    income to them. ONLY call this after verify_landlord_identity returned verified:true,
    passing its verificationToken. Speak only what the tool returned; never invent or
    round a figure.

    Args:
        verification_token: The verificationToken returned by verify_landlord_identity.
            Never invent it and never read it aloud.

    Returns:
        A dict with the owner's rent-received status, arrears amount, and net income for
        their own properties. When unreachable/unconfigured or the token is rejected:
        {"ok": false, ...} — tell the caller you couldn't retrieve it and a team member
        will follow up.
    """
    result = await _tenant_read(_LANDLORD_INCOME_PATH, verification_token)
    logger.info("get_landlord_income(token=***) -> %s", result)
    return result


@function_tool
async def get_contractor_jobs(verification_token: str) -> dict:
    """List the verified CONTRACTOR's own current jobs (each with its site address,
    status, type, urgency, description, and scheduled date). ONLY call this after
    verify_contractor_identity returned verified:true, passing its verificationToken.
    Never state a price or quote, and never read out tenant name or phone — the site
    address is only for access.

    Args:
        verification_token: The verificationToken returned by
            verify_contractor_identity. Never invent it and never read it aloud.

    Returns:
        A dict listing the contractor's own jobs. When unreachable/unconfigured or the
        token is rejected: {"ok": false, ...} — tell the caller you couldn't retrieve it
        and a team member will follow up.
    """
    result = await _tenant_read(_CONTRACTOR_JOBS_PATH, verification_token)
    logger.info("get_contractor_jobs(token=***) -> %s", result)
    return result


@function_tool
async def get_contractor_job_status(verification_token: str, reference: str) -> dict:
    """Get the status of ONE of the verified CONTRACTOR's jobs by its work-order /
    reference number. ONLY call this after verify_contractor_identity returned
    verified:true, passing its verificationToken plus the caller's reference number.
    Never state a price or quote, and never read out tenant name or phone.

    Args:
        verification_token: The verificationToken returned by
            verify_contractor_identity. Never invent it and never read it aloud.
        reference: The work-order / job reference number the caller gave, to look up
            that specific job.

    Returns:
        A dict describing that job (address, status, type, urgency, description,
        scheduled date), or one indicating it wasn't found. When unreachable/
        unconfigured or the token is rejected: {"ok": false, ...} — tell the caller you
        couldn't retrieve it and a team member will follow up.
    """
    result = await _tenant_read(
        _CONTRACTOR_JOB_STATUS_PATH, verification_token, reference
    )
    logger.info(
        "get_contractor_job_status(token=***, reference=%r) -> %s", reference, result
    )
    return result


@function_tool
async def report_maintenance(
    context: RunContext,
    description: str,
    urgent: bool = False,
    address: str | None = None,
) -> dict:
    """Lodge a maintenance / repair request for the VERIFIED TENANT **or property
    OWNER** on this call — for something broken or faulty at the property (e.g. a
    leaking tap, broken heater, no hot water, a blocked drain). ONLY call this AFTER a
    verify tool returned verified:true, and only once you have confirmed WHAT is broken
    and whether it is urgent. You do NOT pass any token or property id — the caller's
    verified token, call id, and name are supplied for you from the call, and the SERVER
    decides tenant-vs-owner handling from that token.

    For an OWNER who owns multiple properties, pass `address` = the street address they
    name for the repair, so the server can pick the right property. A single-property
    owner (or a tenant) does not need `address`.

    Args:
        description: A clear, self-contained description of the problem in the caller's
            own words (what is broken and where), e.g. "hot water system not working in
            the kitchen". Do not include the caller's name or any reference number.
        urgent: True ONLY for a genuine safety, security, gas, electrical, or flooding
            issue; otherwise False.
        address: OPTIONAL — the street address of the property the repair is for. Only
            needed for an owner who owns more than one property; omit for a tenant or a
            single-property owner.

    Returns:
        A dict. When the backend is reachable: {"created": bool, "orderNumber"?: str,
        "reason"?: 'no_property'|'ambiguous_property'|'wrong_caller_type'|
        'invalid_token'}. 'ambiguous_property' means an owner must name which property —
        ask for the full street address and call again with `address`. Read back the
        orderNumber to the caller ONLY when created:true. When it is unreachable, not
        configured, or the caller has not been verified this call: {"ok": false, ...} —
        treat that as "couldn't log it right now" and say a team member will follow up.
        NEVER tell the caller a request was created unless this returned created:true,
        and never invent or guess a reference number.
    """
    state = _call_state(context)
    if state is None or not state.verification_token:
        # No verified token stashed this call — never POST a maintenance request
        # without one. Signal the LLM to verify the caller first.
        logger.info(
            "report_maintenance called before a verified token was stashed; declining"
        )
        return {"ok": False, "created": False, "reason": "not_verified"}

    payload: dict = {
        "callId": state.call_id,
        "verificationToken": state.verification_token,
        "description": description,
        "urgent": bool(urgent),
    }
    if state.caller_name:
        payload["callerName"] = state.caller_name
    if state.caller_phone:
        payload["callerPhone"] = state.caller_phone
    # Property disambiguation for an owner: prefer an address the LLM supplied for the
    # repair, else fall back to the verified property's address stashed at verify time
    # (a single-property owner / the just-verified property). Tenants: harmless — the
    # server ignores `address` for a tenant token, which routes to their one property.
    resolved_address = address if (address and address.strip()) else state.caller_property_address
    if resolved_address and resolved_address.strip():
        payload["address"] = resolved_address

    result = await _post(_MAINTENANCE_PATH, payload)
    order_number = result.get("orderNumber")
    if result.get("created") and order_number:
        state.record_action(f"maintenance {order_number}")
    # Log the outcome only — the payload (with the token) is never logged.
    logger.info(
        "report_maintenance(description=%r, urgent=%s, address=%r) -> %s",
        description,
        urgent,
        address,
        result,
    )
    return result


@function_tool
async def log_job_update(
    context: RunContext, reference: str, note: str, urgent: bool = False
) -> dict:
    """Log an update / note from the VERIFIED CONTRACTOR on ONE of their own jobs (e.g.
    running late, completed, needs parts, an access issue). You do NOT pass any token —
    the verified caller's token, call id, and name are supplied for you. `reference` is
    the work-order / job number the contractor gives; `note` is a short clear
    description of the update. Returns {logged, orderNumber?, reason?}. This records a
    note for the office to review — it does NOT change the job's official status.

    Args:
        reference: The work-order / job reference number the contractor gave, to find
            which of their jobs the note is for.
        note: A short, clear description of the update in the contractor's own words
            (e.g. "running about an hour late, will arrive by two", "job complete, will
            send the invoice", "need a replacement part, back tomorrow").
        urgent: True ONLY for a genuine safety, security, or access-blocking issue;
            otherwise False.

    Returns:
        A dict. When the backend is reachable: {"logged": bool, "orderNumber"?: str,
        "reason"?: 'no_job'|'wrong_caller_type'|'invalid_token'}. Confirm the note ONLY
        when logged:true. When it is unreachable, not configured, or the caller has not
        been verified as a contractor this call: {"ok": false, ...} — treat that as
        "couldn't log it right now" and say a team member will follow up. NEVER tell the
        contractor the job's official status changed, and never promise payment, a
        schedule, or approval — only that the note was logged for the office.
    """
    state = _call_state(context)
    if state is None or not state.verification_token:
        # No verified token stashed this call — never POST an update without one.
        logger.info(
            "log_job_update called before a verified token was stashed; declining"
        )
        return {"ok": False, "logged": False, "reason": "not_verified"}
    # Defence in depth: only a verified CONTRACTOR may log a job update. The server
    # enforces this from the token too, but refusing here avoids a pointless POST.
    if state.caller_type != _CALLER_TYPE_CONTRACTOR:
        logger.info(
            "log_job_update called for caller_type=%r (not contractor); declining",
            state.caller_type,
        )
        return {"ok": False, "logged": False, "reason": "wrong_caller_type"}

    payload: dict = {
        "callId": state.call_id,
        "verificationToken": state.verification_token,
        "reference": reference,
        "note": note,
        "urgent": bool(urgent),
    }

    result = await _post(_CONTRACTOR_JOB_UPDATE_PATH, payload)
    order_number = result.get("orderNumber")
    if result.get("logged") and order_number:
        state.record_action(f"job update {order_number}")
    # Log the outcome only — the payload (with the token) is never logged.
    logger.info(
        "log_job_update(reference=%r, urgent=%s) -> %s",
        reference,
        urgent,
        result,
    )
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
    verify_landlord_identity,
    verify_contractor_identity,
    get_landlord_account_summary,
    get_landlord_portfolio,
    get_landlord_maintenance,
    get_landlord_next_inspection,
    get_landlord_income,
    get_contractor_jobs,
    get_contractor_job_status,
    report_maintenance,
    log_job_update,
]
