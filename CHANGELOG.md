# Changelog

## 2026-07-16

### Added
- Phone account reads (Phase 1, Part B): identity verify + five token-scoped Claude function-calling tools — `verify_identity` (name + address → `POST /api/voice/verify-identity`, mints a `verificationToken`) plus `get_account_summary`, `get_rent_status`, `get_next_inspection`, `get_maintenance_status`, and `get_lease_details`, each POSTing `{"verificationToken": ...}` (maintenance also accepts an optional `reference`) to the Nest `voice` tenant read endpoints via the existing graceful-degrade `_post()` helper.
- Move-out by phone: two Claude function-calling tools (`verify_tenant`, `create_end_leasing`) that POST to the Nest `voice` endpoints with the `x-voice-service-token` header, letting the agent verify a tenant then lodge an end-of-lease record mid-call.
- `VOICE_API_BASE_URL` / `VOICE_SERVICE_TOKEN` config (with `.env.example` docs); when either is unset the tools degrade gracefully — the agent tells the caller a team member will follow up instead of crashing.
- `httpx` dependency for the async tool HTTP client.

### Changed
- `SYSTEM_PROMPT` gains an "ACCOUNT QUESTIONS (reads)" policy: for a caller's OWN rent / inspection / maintenance / lease, collect name + address → `verify_identity` → reveal nothing until `verified:true` (never disclose a mismatch reason) → thread the `verificationToken` into every read tool, answer only what was asked in the caller's language, never read another property's data, never invent figures, and never state an arrears/owing amount; the "no account access" limit is narrowed to "until identity is verified." The verification token is never spoken and is redacted from logs. Bilingual EN/中文, the compliance disclosure, and per-language TTS switching are unchanged.
- `SYSTEM_PROMPT` now carries the move-out conversation policy: collect name + address + move-out date, verify, read back and require an explicit "yes", then create — never claim a record was created unless the tool returned `created:true`, and never reveal a verification mismatch reason (anti-fishing). Bilingual EN/中文, the fixed compliance disclosure, and per-language TTS voice switching are unchanged.

## 2026-07-15

### Added
- Render/PaaS deploy support: the worker binds its health server to the platform `$PORT` on `0.0.0.0` when present (via new `HEALTH_PORT`/`HEALTH_HOST` config), so a Render Web Service deploy passes the port scan; unset locally, keeping LiveKit's defaults.

### Changed
- Deployment target for the browser tester (`web/`) is **Render**, not Vercel: it runs as a second Render Web Service (Root Directory `web/`, Node, build `npm install && npm run build`, start `npm start` — `next start` binds Render's `$PORT`), sharing the worker's LiveKit creds + `VOICE_AGENT_NAME=crossub-inbound`.
- Worker deploy on Render (staging, Singapore) requires the Build Command to prefetch the turn-detector model — `uv sync --frozen && uv run crossub-voice-agent download-files` — and a Standard (2 GB) instance; 512 MB OOMs loading VAD + the multilingual turn-detector. Health endpoint returns `OK` when live.
- `docs/cost-estimate.md` — boss-facing running-cost estimate (~1,000 min/mo ≈ AUD $150/mo; pilot ≈ free), free-tier breakdown, go-live blockers (ElevenLabs commercial licence, Twilio trial), and a bilingual EN/中文 summary.
- Browser tester UI under `web/` (Next.js 15 + LiveKit React): Start-call button, live EN + 中文 transcript, audio visualizer, agent-state indicator; a server route mints a browser token and dispatches the agent per call.
- `dev.sh` one-command launcher that runs the agent worker + web tester together (Ctrl+C stops both).
- Per-language TTS voice switching: the agent matches its voice to the caller's language each turn, uses a dedicated Chinese voice via `VOICE_TTS_VOICE_ID_ZH`, and enforces the `zh`/`en` language code on flash/turbo v2.5.

### Fixed
- Chinese no longer sounds accented: the compliance disclosure's 中文 half and every Chinese reply are spoken with the Chinese voice + enforced `zh` language code, instead of the English voice reading Mandarin.
- `config.py` now calls `load_dotenv()` before reading env vars — it was imported before `agent.py` loaded `.env`, so all `.env` overrides (and optional keys) were silently ignored.
- ElevenLabs TTS now receives the API key explicitly from `ELEVENLABS_API_KEY` (the plugin otherwise expects `ELEVEN_API_KEY`), fixing a startup crash in `console` mode.

## 2026-07-13

### Added
- Phase 0 PoC scaffold: LiveKit Agents (Python) worker with Deepgram STT, Claude LLM, ElevenLabs TTS, Silero VAD, and multilingual turn detection.
- Bilingual EN + 中文 CROSSUB persona with canned property FAQ and a fixed, uninterruptible compliance disclosure (AU call-recording consent + AI disclosure).
- Per-turn latency and usage metrics logging for the Phase 0 go/no-go evaluation.
- `uv`-managed project (pyproject.toml, Python 3.12), env template, and README runbook covering local `console` testing and Twilio→LiveKit SIP phone setup.
