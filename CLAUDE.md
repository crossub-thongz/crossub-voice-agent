# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A standalone **Python LiveKit Agents worker** that answers CROSSUB's phone line as a bilingual (English + 中文) AI assistant. It is a **sidecar to the `crossub_web` NestJS monolith** (`/Users/chanthaithong/Desktop/crossub/crossub_web`), deliberately kept out of that repo because it is a long-running audio worker, not an HTTP service.

Real-time pipeline per turn: caller audio → **Deepgram** STT → **Claude** (`anthropic.LLM`) → **ElevenLabs** TTS, with **Silero VAD** + a multilingual turn-detector for barge-in.

**Read "Answer modes" next — it decides how much of the rest of this file is live.** Since Aug 2026 the default behaviour is `divert`: the line hands tenant enquiries to email rather than answering them, and the agent runs with **no tools at all**. The full self-service assistant below still exists and still works, but only under `VOICE_MODE=full`.

In `full` mode the agent takes real actions in CROSSUB by calling `POST /api/voice/*` on the Nest API (see "The Nest seam" below). It is not self-contained — most behavior worth changing lives at that boundary or in the system prompt.

## Commands

`uv` only — never `pip`. Python 3.12 is pinned via `.python-version`.

```bash
uv sync                                       # create venv + install
uv sync --extra telephony                     # + LiveKit Cloud noise cancellation (BVCTelephony)
uv run crossub-voice-agent download-files     # prefetch VAD + turn-detector weights (one-time)

uv run crossub-voice-agent console            # talk via your mic; no LiveKit room, no browser
uv run crossub-voice-agent dev                # register with LiveKit; needed for SIP + the web tester
./dev.sh                                      # agent (dev) + web tester together; Ctrl+C stops both
```

`console` vs `dev` matters: `console` runs the session locally and ignores `agent_name`, so the browser tester and SIP dispatch **cannot** reach it. Anything involving a room needs `dev`.

The browser tester lives in `web/` and uses **npm**, not pnpm (unlike `crossub_web`):

```bash
cd web && npm install && npm run dev          # http://localhost:3000
```

**There is no test runner and no linter/formatter configured** (no pytest, ruff, black, or mypy). Don't claim tests or lint pass. Verification is manual: `console` mode for latency/voice, the `web/` tester for a full room + tool round-trip, and the worker logs (per-turn latency metrics + an end-of-call usage summary).

## Module layout and dependency order

All Python lives in `crossub_voice_agent/` (~1,800 lines, 6 modules). The import graph is deliberate:

- **`constants/`** — leaf package, imports nothing local. The `VoiceMode` enum. Anything compared against a literal goes here; `config.py` imports it, so it must stay dependency-free.
- **`call_state.py`** — leaf, imports nothing local. `CallState` dataclass + transcript/outcome labels. Both `tools.py` and `call_log.py` depend on it, so keep it dependency-free to avoid a cycle.
- **`config.py`** — the *only* place env vars are read. Calls `load_dotenv()` itself because it is imported before `agent.py`'s own call (a past bug: every `.env` override was silently ignored).
- **`prompts.py`** — `SYSTEM_PROMPT` (the whole conversation policy, ~230 lines), plus the fixed-wording bilingual compliance disclosure and greeting instructions. Kept free of logic so wording can be reviewed without touching code.
- **`tools.py`** — the 19 Claude function-calling tools, exported as `ALL_TOOLS`.
- **`call_log.py`** — the end-of-call Comm Hub landing hook (not an LLM tool).
- **`agent.py`** — `entrypoint()` (per-call wiring) and `main()` (worker options).

## Per-call lifecycle (`agent.entrypoint`)

1. `ctx.connect()`, then **await the caller** (`_wait_for_caller`) — the room is empty the instant `connect()` returns, so SIP attributes (`sip.phoneNumber`, the caller's ANI) don't exist yet. It accepts *any* participant kind on purpose: the web tester dispatches the agent before the browser joins, so waiting for SIP alone would hang it. Bounded by `VOICE_PARTICIPANT_WAIT_TIMEOUT_S` (10s); on timeout the call proceeds without a phone number.
2. Resolve the `AgentProfile` for the configured mode (logged: mode, tool count, intake address), then build `CallState` (`call_id` = LiveKit room name, `default_outcome` from the profile) and attach it as the session's `userdata`.
3. Register two shutdown callbacks: the usage summary log and `call_log.log_call`.
4. `session.start()`, then speak the **uninterruptible** disclosure — English half with the English voice, 中文 half with the Chinese voice — then an LLM-generated greeting.

Caller ID is treated as a *hint*, never authentication (trivially spoofed); the verify tools still demand name + address.

## Answer modes

`VOICE_MODE` (read once in `config.py`, parsed into the `VoiceMode` enum) picks one of two behaviours. `agent.build_agent_profile()` resolves it into a single `AgentProfile` — instructions, tool list, greeting, default outcome — so the two can never be half-applied.

| | `divert` (default) | `full` |
|---|---|---|
| Prompt | `prompts.build_divert_system_prompt(...)` | `prompts.SYSTEM_PROMPT` |
| Disclosure | `DIVERT_DISCLOSURE_EN/_ZH` — ends "our team follows up by email" | `DISCLOSURE_EN/_ZH` — ends "you can ask to speak with a person" |
| Tools | **none** | all 19 (`tools.ALL_TOOLS`) |
| Nest calls during the call | none | verify + role-scoped reads + writes |
| Log-call outcome when nothing lodged | `diverted_to_email` | `general_enquiry` |

**Why divert exists.** CROSSUB publishes one Calilio number, it is tenant-facing, and the business handles tenant enquiries by email — `support@crossub.com.au` already runs AI triage *and* AI auto-reply end to end (`support-email-triage.service.ts` in `crossub_web`, ~3,800 lines, `sendReply(...)` gated on `replyEnabled`). The phone's job is to get the enquiry onto that channel, not to answer it.

**Two things not to undo:**

- **Divert passes an empty tool list, not a prompt instruction.** Removing the capability beats telling the model not to use it — with nothing registered the agent cannot read an account or write a record under any amount of caller pressure. If you find yourself adding "just one safe tool" to divert mode, that guarantee is what you are spending.
- **An unrecognised `VOICE_MODE` falls back to `divert`.** A typo must never arm an agent that can read tenant data. Keep the fallback pointing at the mode with fewer capabilities.
- **The disclosure is per mode and must stay truthful.** Divert has no live transfer, so its disclosure cannot offer a person; `full` takes a callback number, so it cannot promise an email. Both keep the AI disclosure + AU recording consent verbatim — that part is not yours to reword. Note it states how the team follows up rather than promising to email *this* caller: an unrecognised number has no address to reply to, since `VoiceCallerLinkService` only resolves an email for a human-confirmed, still-active caller link.

The agent must never claim to have logged, lodged, or created anything in divert mode — nothing on the call creates a record; the caller's email is what starts the job. The call itself still lands in the Comm Hub via the unchanged `call_log` shutdown hook, so an officer sees who rang and why even if the tenant never writes in.

**SMS is wired but deliberately dark.** `VOICE_INTAKE_SMS_NUMBER` adds a "you can also text us" line to the prompt, and is blank by default because `crossub_web` has **no SMS provider at all** — no Twilio, no `sendSms`, nothing in `package.json`. Do not set it until inbound SMS actually lands somewhere the team reads, or callers are sent to a black hole.

## Two mechanisms worth knowing before editing

**Per-language TTS switching.** `detect_language()` classifies each caller turn by CJK-vs-Latin regex; `Agent.on_user_turn_completed` then calls `apply_tts_language()` to `update_options(voice_id=..., language=...)` before the reply is generated. Voice ids resolve to *explicit* ids (`EN_VOICE_ID` / `ZH_VOICE_ID`, never `None`) so switching works in both directions — leaving one as `None` would strand the Chinese voice on later English turns. `language=` is only sent for models in `config._LANGUAGE_ENFORCING_MODELS` (flash/turbo v2.5, v3); `eleven_multilingual_v2` rejects it, so `TTS_LANGUAGE_ENFORCED` gates it.

**`CallState` as the token stash.** The LLM never receives or passes a call id, property id, or (for write tools) a verification token. Instead each `verify_*` tool stashes the minted `verificationToken` + matched name + caller type + verified property address onto `CallState`, and the write tools (`report_maintenance`, `log_job_update`) read it back via the injected `RunContext`. `stash_verification()` no-ops unless the backend returned `verified: true`, so a failed or unreachable verify can never clobber a good earlier one. Write tools return `{"ok": false, "reason": "not_verified"}` rather than POSTing without a token.

## The Nest seam (`crossub_web`)

Every tool POSTs to `crossub_web`'s `apps/api/src/modules/voice/` module, authenticated with a shared machine secret in the `x-voice-service-token` header (`VoiceServiceGuard` on the controller). Both sides must agree on `VOICE_SERVICE_TOKEN`. Paths are declared as `_*_PATH` constants at the top of `tools.py`.

Two-gate model, three caller types, each with its **own** verify tool so the LLM's branch is unambiguous:

| Caller | Verify | Reads |
|---|---|---|
| Tenant | `verify_identity` (name + address) | `tenant/{account-summary,rent,next-inspection,maintenance-status,lease}` |
| Landlord/owner | `verify_landlord_identity` (`callerType: 'landlord'`) | `landlord/{account-summary,properties,maintenance-status,next-inspection,income}` |
| Contractor | `verify_contractor_identity` (name + work-order ref) | `contractor/{jobs,job-status}` |

Verify mints a short-lived HMAC token carrying the caller type; the token — never an LLM-supplied id — is what scopes each read, so a landlord token cannot read tenant endpoints. Writes: `create_end_leasing` (tenant move-out), `report_maintenance` (tenant *or* owner; the **server** routes by token type, and a multi-property owner disambiguates with `address`), `log_job_update` (contractor note only, never a status change). `verify_tenant` + `create_end_leasing` are the older move-out pair and coexist with `verify_identity`.

**The JSON field names are a fixed cross-repo contract** (`verificationToken`, `propertyId`, `moveOutDate`, `callerName`, `orderNumber`, `taskNumber`, `reference`, …). Renaming one here silently breaks the Nest DTOs, and vice versa — change both repos together.

## Invariants to preserve

- **No tool ever raises.** `_post()` returns a structured `{"ok": false, "reason": ...}` for unconfigured service, network error, non-2xx, non-JSON, or non-object body. A live phone call must not die because the backend is slow or down; the LLM is prompted to say "a team member will follow up" on `ok: false`. If `VOICE_API_BASE_URL` or `VOICE_SERVICE_TOKEN` is unset, *all* tools degrade this way — that's the intended local-dev mode, not a bug.
- **The verification token is never spoken and never logged.** Use `_redact_token()` on verify results and log outcomes only (`token=***`), never payloads.
- **Anti-fishing.** The prompt forbids revealing *why* a verification failed — no "that name doesn't match". Keep new failure paths equally opaque.
- **Never confirm an action that didn't happen.** Only read back a reference when the tool returned `created: true` / `logged: true`.
- **The shutdown hook can never crash shutdown.** `call_log.log_call` wraps everything; a failed Claude summary POSTs `summary: ""` and lets the Nest side derive a preview.
- **Role data never mixes.** A contractor hears no price and no tenant name/phone; a tenant hears no arrears figure (deliberately unavailable); a landlord hears tenant *names* but never their email/phone.

## Adding a tool (`full` mode only)

Tools are unreachable in `divert` mode by design — adding one changes nothing until `VOICE_MODE=full`.


1. Add the path as a `_*_PATH` constant (no inline strings) and use `_post()` / `_tenant_read()`.
2. Write the docstring **for the LLM** — existing ones state the preconditions ("ONLY call this after … returned verified:true"), the exact return shape, and what must never be spoken. This docstring *is* the tool schema.
3. Take `context: RunContext` as the first param only if you need `CallState` (it's hidden from the LLM schema).
4. Register in `ALL_TOOLS`.
5. Add the matching conversation policy to `SYSTEM_PROMPT` — a tool with no prompt policy won't be used correctly.
6. Implement or confirm the Nest endpoint + DTO in `crossub_web`.

## Config and deployment quirks

All tunables are `VOICE_*` env vars documented in `.env.example` and read once in `config.py`. Notable:

- `VOICE_STT_LANGUAGE=multi` attempts EN/中文 code-switching in one Deepgram stream. **This is the known Phase-0 risk** — if Mandarin recognition is poor, `en`/`zh` per call is the fallback (the docs float a "press 1 for English" IVR). `docs/async-messaging-triage.md` records that real-time STT proved unreliable enough on a phone line to motivate the *text* triage path as an alternative.
- `VOICE_TTS_VOICE_ID_ZH` blank means Chinese is spoken by the English voice (accented) — set a dedicated ElevenLabs Chinese voice for native-sounding Mandarin.
- `ELEVENLABS_API_KEY` is passed explicitly because the plugin otherwise reads `ELEVEN_API_KEY`.
- `PORT`/`HOST` → `HEALTH_PORT`/`HEALTH_HOST`: when a PaaS sets `$PORT`, the worker binds its health server to it on `0.0.0.0` so the deploy's port scan passes. Unset locally, keeping LiveKit's defaults.

Deploy target is **Render** (staging, Singapore), not Vercel — two web services. The worker's build command must be `uv sync --frozen && uv run crossub-voice-agent download-files` (the turn-detector weights must be prefetched) on a **Standard 2 GB** instance; 512 MB OOMs loading VAD + the multilingual turn-detector. The tester deploys with root directory `web/`.

## The `web/` tester

Next.js 15 + React 19, two tabs, both proxying secrets server-side so nothing reaches the browser:

- `app/page.tsx` + `app/api/token/route.ts` — mints a LiveKit join token and calls `AgentDispatchClient.createDispatch()` to pull the named worker into a fresh room. `VOICE_AGENT_NAME` here **must match** the agent's, or dispatch silently targets nothing.
- `app/messaging/page.tsx` + `app/api/triage-preview/route.ts` — the async-messaging (text) triage tester, proxying `POST /api/voice/triage-preview`. Dry-run only; it never writes a record.

Its env lives in `web/.env.local` (separate from the agent's `.env`), documented in `web/.env.local.example`.

## Docs

`docs/cost-estimate.md` (boss-facing running-cost estimate, ~AUD $150/mo at 1,000 min) and `docs/async-messaging-triage.md` (bilingual guide to the text triage path) are written for non-engineers — keep them plain-language and bilingual if you edit them. `README.md` carries the Phase 0 go/no-go criteria.

Per the global convention: append a dated entry to `CHANGELOG.md` at the repo root (newest first) after any behavior change. The existing entries are unusually detailed about *why* — match that.
