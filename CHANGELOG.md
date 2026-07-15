# Changelog

## 2026-07-15

### Added
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
