# CROSSUB Voice Agent — Phase 0 PoC

A real-time phone AI agent for CROSSUB, built on **LiveKit Agents (Python)**. It answers a
call and talks naturally in **English and Chinese (中文)** using:

- **Deepgram** — speech-to-text
- **Claude (Anthropic)** — the brain (same model family as CROSSUB's email AI)
- **ElevenLabs** — text-to-speech
- **Silero VAD + multilingual turn-detector** — natural turn-taking & barge-in

This is **Phase 0**: a de-risking proof-of-concept with a **canned property FAQ** and **no
CROSSUB backend integration yet**. Its only job is to answer one question: *does natural,
low-latency, bilingual phone AI actually work well and at acceptable cost?* Backend tools
(look up a tenancy, log a maintenance request, escalate emergencies) come in Phase 1, wired
to the new `/api/voice/*` module in the `crossub_web` API.

> This is a **separate repo/service** from `crossub_web` on purpose — it's a long-running
> Python worker, not part of the NestJS monolith.

---

## 1. Prerequisites

Create accounts and grab API keys (free tiers are fine for the PoC):

| Service | What you need | Where |
|---|---|---|
| **LiveKit Cloud** | Project URL + API key/secret | https://cloud.livekit.io |
| **Deepgram** | API key | https://console.deepgram.com |
| **ElevenLabs** | API key (+ optionally a multilingual voice id) | https://elevenlabs.io |
| **Anthropic** | API key | https://console.anthropic.com |
| **Twilio** | Elastic SIP Trunk + an AU phone number *(only for the phone test, step 5)* | https://console.twilio.com |

`uv` is already installed. Python 3.12 is pinned via `.python-version` (uv fetches it for you).

## 2. Install

```bash
cd ~/Desktop/crossub/crossub-voice-agent
uv sync                      # create the venv + install deps
uv run crossub-voice-agent download-files   # prefetch VAD + turn-detector weights (one-time)
```

## 3. Configure

```bash
cp .env.example .env
# then edit .env and paste in your keys
```

## 4. Test locally first (no phone needed) — `console` mode

The fastest way to feel the latency and hear the bilingual voice, straight from your mic:

```bash
uv run crossub-voice-agent console
```

Speak to it in English, then in 中文. Check:

- **Latency** — does it respond quickly (target < ~800 ms after you stop talking)?
- **Bilingual quality** — is the Chinese STT accurate and the Chinese TTS natural?
- **Barge-in** — can you interrupt it mid-sentence and it stops and listens?

The terminal logs per-turn latency metrics (end-of-utterance, time-to-first-token,
time-to-first-byte) and a usage summary on exit — that's your latency + cost evidence.

### Bilingual voice quality (中文)

The agent **matches its voice to the caller's language every turn** — English replies use the
English voice, Chinese replies switch to a Chinese voice — and enforces the `zh`/`en` language
code (on `eleven_flash_v2_5` / `eleven_turbo_v2_5`) so Mandarin is pronounced correctly. The
opening compliance disclosure is likewise spoken with the English voice for its English half and
the Chinese voice for its 中文 half.

For **native-sounding** Mandarin (not the English voice speaking Chinese), set a dedicated
Chinese voice in `.env`:

```
VOICE_TTS_VOICE_ID_ZH=<a Chinese voice id>
```

Get one from the **ElevenLabs Voice Library** (elevenlabs.io/app/voice-library → filter
*Language = Chinese* → pick a conversational voice → *Add to my voices* → copy its Voice ID).
Left blank, Chinese is still spoken by the English voice but with the enforced `zh` language
code — better than before, but a dedicated voice is the real fix. For maximum 中文 quality at
some latency cost, A/B `VOICE_TTS_MODEL=eleven_multilingual_v2`.

## 5. Test over a real phone number — SIP

Once `console` mode feels good, wire up telephony:

1. **Run the worker** (it registers with LiveKit and waits for dispatch):
   ```bash
   uv run crossub-voice-agent dev
   ```
2. **LiveKit SIP inbound trunk + dispatch rule** — in LiveKit Cloud (or via the `lk` CLI),
   create an inbound SIP trunk and a dispatch rule that routes incoming calls into a room and
   dispatches the agent named `crossub-inbound` (the `VOICE_AGENT_NAME`).
   Docs: https://docs.livekit.io/sip/
3. **Twilio Elastic SIP Trunk** — point your AU number's Origination URI at your LiveKit SIP
   endpoint so inbound calls flow Twilio → LiveKit → this agent.
   Docs: https://docs.livekit.io/sip/quickstarts/configuring-twilio-trunk/
4. **Dial the number** and talk. For phone audio, consider enabling
   `VOICE_TELEPHONY_NOISE_CANCELLATION=true` (needs `uv sync --extra telephony`).

## Phase 0 success criteria (the go/no-go gate)

Proceed to Phase 1 only if:

- [ ] Turn latency is consistently low enough to feel conversational.
- [ ] English **and** Chinese are both understood and spoken naturally.
- [ ] Interruptions (barge-in) work.
- [ ] Per-minute cost (from the usage logs + provider dashboards) is acceptable to the boss.

## ⚠️ Known Phase-0 risk to validate: Chinese STT

The biggest unknown is **simultaneous EN + 中文** in a single stream. `VOICE_STT_LANGUAGE=multi`
attempts code-switching, but Deepgram's `multi` model may not cover Mandarin well. If Chinese
recognition is poor, test the fallbacks:

- Set `VOICE_STT_LANGUAGE=zh` (Chinese-only) and `=en` (English-only) to compare per-language quality.
- If both are individually good but code-switching isn't, the Phase 1 design can add a quick
  IVR ("press 1 for English, 2 for 中文") to pick the STT language per call.

Document what you find — it directly shapes the Phase 1 STT decision.

## Layout

```
crossub_voice_agent/   # the Python voice agent (LiveKit worker)
  agent.py     # LiveKit worker: entrypoint, STT/LLM/TTS pipeline, metrics
  config.py    # all tunables (env-driven, no magic strings)
  prompts.py   # system persona + the fixed bilingual compliance disclosure
web/           # Next.js browser tester (see below)
```

## Browser tester (`web/`)

A visual way to test the agent from a browser — Start-call button, live EN + 中文 transcript,
audio visualizer — instead of the terminal `console`. It runs against the same LiveKit project
and dispatches this agent per call. Uses **npm** (not pnpm).

Run both at once, one terminal (Ctrl+C stops both):

```bash
./dev.sh           # starts the agent + tester; open http://localhost:3000
```

Or manually in two terminals:

```bash
# Terminal 1 — the agent (dev mode connects to LiveKit for the browser):
uv run crossub-voice-agent dev

# Terminal 2 — the tester UI:
cd web
npm install        # first time only
npm run dev        # http://localhost:3000
```

The tester reads the same LiveKit creds from `web/.env.local` (already populated). Deployable to
Vercel (set the project root to `web/`) to share a link with testers / the boss. See `web/README.md`.

## What's next (Phase 1, in the crossub_web repo)

Add Claude **tools** to `agent.py` that call the new `POST /api/voice/*` endpoints
(authenticated with `x-voice-service-token`) to resolve the caller by phone, answer
account-specific questions, log maintenance requests, and escalate emergencies — reusing
CROSSUB's existing services and Comm Hub. See the approved plan for the full seam map.
