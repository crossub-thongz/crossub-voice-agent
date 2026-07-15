# CROSSUB Voice Tester

A minimal web UI to test the CROSSUB phone AI voice agent from the browser — Start-call
button, live bilingual (EN + 中文) transcript, audio visualizer, and agent-state indicator.
Talk to it with your mic instead of the terminal `console`, and share a link with testers.

It connects to the same LiveKit project as the agent and **dispatches the running agent
worker** into a fresh room per call.

## Prerequisites

- The voice agent worker running in **`dev`** mode (not `console`):
  ```bash
  cd ~/Desktop/crossub/crossub-voice-agent
  uv run crossub-voice-agent dev
  ```
- The agent's `VOICE_AGENT_NAME` (default `crossub-inbound`) must match this app's.

## Setup

```bash
cd ~/Desktop/crossub/crossub-voice-agent/web
npm install
cp .env.local.example .env.local     # then paste your LiveKit URL + key + secret
npm run dev                          # http://localhost:3000
```

`.env.local` needs the **same** LiveKit credentials as the agent:

```
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
VOICE_AGENT_NAME=crossub-inbound
```

These stay server-side (used only in `app/api/token/route.ts`) — never shipped to the browser.

## Use

1. Start the agent worker (`uv run crossub-voice-agent dev`).
2. Start this app (`npm run dev`), open http://localhost:3000.
3. Click **Start call**, allow mic access, and talk. Watch the transcript build in real time.

## Deploy (share with testers / boss)

Deploy to Vercel and add the four env vars in the project settings. The shared URL lets
anyone try the agent — just keep the agent worker running (locally via `dev`, or later as a
deployed worker). The token route dispatches the agent per call.

## How it works

- `app/api/token/route.ts` — server route: mints a LiveKit join token for the browser and
  calls `AgentDispatchClient.createDispatch()` to bring the named agent into the room.
- `app/page.tsx` — `LiveKitRoom` + `useVoiceAssistant` (audio + agent state) + `BarVisualizer`,
  with a `RoomEvent.TranscriptionReceived` listener that renders the live transcript.
