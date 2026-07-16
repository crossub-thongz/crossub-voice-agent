"""Claude function-calling tools that let the voice agent take real actions in
CROSSUB by POSTing to the Nest `voice` module endpoints.

Two tools back the phone move-out flow:
  - verify_tenant(name, address)                 -> POST /api/voice/verify-tenant
  - create_end_leasing(property_id, move_out_date, caller_name)
                                                  -> POST /api/voice/end-leasing

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


# Registered on the Agent (see agent.CrossubAssistant). Exposed as one list so the
# agent wires up "all voice action tools" without knowing their individual names.
ALL_TOOLS = [verify_tenant, create_end_leasing]
