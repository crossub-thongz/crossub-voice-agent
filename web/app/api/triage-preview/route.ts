import { NextResponse } from "next/server";

// Always run per-request (never cached) — each preview is a fresh AI classification.
export const dynamic = "force-dynamic";

/**
 * Server-side proxy to the Nest API's `POST /api/voice/triage-preview`. The shared
 * `x-voice-service-token` is read here (server-only) and never reaches the browser — the same
 * pattern as the LiveKit token route. Point `VOICE_API_BASE_URL` at the API (e.g. the staging
 * URL) and set `VOICE_SERVICE_TOKEN` to the SAME value the API expects.
 */
export async function POST(req: Request) {
  const base = process.env.VOICE_API_BASE_URL;
  const token = process.env.VOICE_SERVICE_TOKEN;

  if (!base || !token) {
    return NextResponse.json(
      {
        error:
          "Missing VOICE_API_BASE_URL / VOICE_SERVICE_TOKEN in .env.local (set them to the API URL + its voice service token).",
      },
      { status: 500 },
    );
  }

  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const url = `${base.replace(/\/$/, "")}/api/voice/triage-preview`;
  try {
    const upstream = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-voice-service-token": token,
      },
      body: JSON.stringify(payload),
    });
    const data = (await upstream.json().catch(() => ({}))) as Record<string, unknown>;
    if (!upstream.ok) {
      const msg =
        (typeof data.message === "string" && data.message) ||
        (typeof data.error === "string" && data.error) ||
        `Triage API responded ${upstream.status}`;
      return NextResponse.json({ error: msg }, { status: upstream.status });
    }
    return NextResponse.json(data);
  } catch (e) {
    return NextResponse.json(
      {
        error:
          "Could not reach the triage API. Is VOICE_API_BASE_URL correct and the API reachable? " +
          (e instanceof Error ? e.message : String(e)),
      },
      { status: 502 },
    );
  }
}
