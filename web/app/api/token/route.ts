import { NextResponse } from "next/server";
import { AccessToken, AgentDispatchClient } from "livekit-server-sdk";

// Always run per-request (never cached) — each call mints a fresh room + token.
export const dynamic = "force-dynamic";

const AGENT_NAME = process.env.VOICE_AGENT_NAME || "crossub-inbound";

function rand(len: number): string {
  // Browser-free random id for room/participant names.
  return Array.from({ length: len }, () =>
    "abcdefghijklmnopqrstuvwxyz0123456789"[Math.floor(Math.random() * 36)],
  ).join("");
}

export async function POST() {
  const url = process.env.LIVEKIT_URL;
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;

  if (!url || !apiKey || !apiSecret) {
    return NextResponse.json(
      { error: "Missing LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET in .env.local" },
      { status: 500 },
    );
  }

  const roomName = `crossub-test-${rand(8)}`;
  const identity = `tester-${rand(6)}`;

  // 1) Join token for the browser participant.
  const at = new AccessToken(apiKey, apiSecret, { identity, ttl: "15m" });
  at.addGrant({ room: roomName, roomJoin: true, canPublish: true, canSubscribe: true });
  const token = await at.toJwt();

  // 2) Dispatch our named agent worker into that room.
  //    (Server SDK clients want an http(s) host, not the wss URL.)
  const host = url.replace(/^wss:/, "https:").replace(/^ws:/, "http:");
  try {
    const dispatch = new AgentDispatchClient(host, apiKey, apiSecret);
    await dispatch.createDispatch(roomName, AGENT_NAME);
  } catch (e) {
    return NextResponse.json(
      {
        error:
          `Could not dispatch agent "${AGENT_NAME}". Is the worker running (uv run crossub-voice-agent dev)? ` +
          (e instanceof Error ? e.message : String(e)),
      },
      { status: 502 },
    );
  }

  return NextResponse.json({ serverUrl: url, token, roomName });
}
