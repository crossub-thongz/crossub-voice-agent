# CROSSUB Phone AI — Running Cost Estimate

_Last updated: 2026-07-15 · Owner: Tony (IT)_

## Purpose

A plain-language estimate of what the CROSSUB phone AI agent costs to run, so the cost is
clear **before** we commit to a production phone line. The headline scenario is **~1,000 call
minutes per month**. These are **estimates from provider list prices**, not quotes — the real
figure is measurable from the agent's own usage logs once we run a batch of test calls.

> **TL;DR** — The **pilot is essentially free** (signup credits cover it). A **live line at
> ~1,000 min/month costs roughly AUD $150/month**, and about **half of that is the voice
> (ElevenLabs)**. Going live also requires paying for two things regardless of usage: ElevenLabs
> (its free tier bans commercial use) and Twilio (its trial can't take real calls).

---

## The stack we pay for

The phone AI is a pipeline of five services. Each bills separately:

| # | Service | Job in the call |
|---|---------|-----------------|
| 1 | **Twilio** | The phone number + carrying the phone call (PSTN → internet) |
| 2 | **LiveKit Cloud** | Real-time media plumbing (connects caller ↔ AI, handles turn-taking) |
| 3 | **Deepgram** | Speech-to-text (hears the caller) |
| 4 | **Anthropic Claude** (Haiku 4.5) | The "brain" (understands + decides what to say) |
| 5 | **ElevenLabs** | Text-to-speech (the AI's voice) |

---

## Cost at ~1,000 minutes / month

Assumptions: the AI is speaking ~40–45% of each call (this drives ElevenLabs, which bills per
character spoken); Twilio carries the phone line; LiveKit runs on its free **Build** plan.
USD list prices converted at **~1.55 AUD/USD**.

| Service | List cost @1,000 min | Free-credit effect | Steady-state (AUD/mo) |
|---------|----------------------|--------------------|------------------------|
| **Deepgram** (speech-to-text) | ~$7.70 USD | $200 signup credit → free for **~26 months** | **~A$0 now** → ~A$12 later |
| **ElevenLabs** (voice) | ~$40–90 USD | free tier unusable here (10 min/mo + non-commercial) | **A$62–140** 🔴 biggest |
| **Claude Haiku** (brain) | ~$7 USD | $5 signup credit ≈ first month free | **~A$11** |
| **LiveKit Cloud** (media) | $0 on Build | 1,000 agent-min free = exactly the cap | **A$0** |
| **Twilio** (phone line) | ~$15 USD | $15 trial ≈ first month free | **~A$23** |
| **TOTAL** | | | **≈ AUD $110–190 / month** |

**Per-minute equivalent:** roughly **AUD $0.11–0.19 / minute**, all-in.

---

## The two numbers that matter

### 1. Pilot / testing phase: **≈ AUD $0**
The free signup credits and free tiers cover almost everything while we prototype and demo:
Deepgram ($200), Anthropic ($5), Twilio ($15 trial), and LiveKit (Build plan, no card). The
only thing that can cost anything during testing is ElevenLabs, and only if we push a lot of
voice minutes (its free tier is ~10 minutes/month) — realistically **A$0–30/month** while testing.

### 2. Live at ~1,000 min/month, steady state: **≈ AUD $150/month** (range A$110–190)
- **~Half is ElevenLabs** (the voice) — the single biggest line.
- Deepgram's **$200 credit keeps speech-to-text free for ~2 years**, so the **first ~2 years run
  cheaper (~A$90–160/month)** than the steady-state figure.
- The brain (Claude) and telephony (Twilio) are minor.

---

## Important caveats (read before sign-off)

1. **Free credits *delay* cost — they don't lower the monthly bill.**
   Deepgram's $200, Anthropic's $5 and Twilio's $15 are **one-time**. Only LiveKit's Build plan
   is recurring-free. So the monthly bill above is what you pay once the signup credits are used.

2. **1,000 min sits *exactly* on LiveKit's free ceiling.**
   The Build plan includes 1,000 agent-minutes/month. One busy month above that pushes us onto
   the **Ship plan (~A$78/month)** — worth budgeting for once call volume is real.

3. **Two hard blockers before real tenants can call (not about usage — about licensing):**
   - 🚫 **ElevenLabs free tier bans commercial use** (and requires attribution). A real CROSSUB
     business line needs at least the **~US$5/month Starter** plan, or a different voice vendor.
   - 🚫 **Twilio must leave trial** — the trial only accepts *verified* test numbers, not calls
     from the public. This is mandatory for go-live.

4. **The estimate is voice-heavy and vendor-dependent.** ElevenLabs' per-character price drops
   steeply on higher-volume plans; a cheaper voice vendor (e.g. Cartesia) could lower the biggest
   line. We should A/B this before committing.

---

## How we get the *real* number

The agent already logs a **per-call usage summary** (speech seconds, brain tokens, characters
spoken). After **~5–10 real test calls of typical length**, those logs convert into an **exact
measured AUD/minute for CROSSUB's actual call mix** — far more reliable than this table. We should
do that during the pilot and update this document.

---

## One-line summary for the boss (EN / 中文)

**EN:** Testing the phone AI is basically free right now (covered by signup credits). A real
production line at about 1,000 minutes/month costs roughly **AUD $150/month**, mostly the AI
voice. Before real tenants can call, we'll need to pay for the ElevenLabs voice (its free version
isn't allowed for business use) and upgrade Twilio (its free trial can't take public calls).

**中文：** 目前测试这个电话 AI 基本上是免费的（有注册赠送的额度覆盖）。如果正式上线，按每月约 1,000
分钟通话计算，费用大约是 **每月 150 澳元**，其中大部分是 AI 语音的费用。在真正让租客拨打之前，我们
需要为 ElevenLabs 语音付费（免费版不允许商用），并升级 Twilio（免费试用无法接听公众来电）。

---

## Sources & notes

- LiveKit Cloud pricing (Build plan: 1,000 agent-min + 1 free US number, no card) — livekit.com/pricing
- Deepgram free tier ($200 signup credit, no card, expires 1 year) — deepgram.com/pricing
- Anthropic Claude API (~$5 new-account credit; no permanent free API tier) — claude.com/pricing
- ElevenLabs free plan (10,000 credits/mo ≈ 10–15 min; non-commercial + attribution) — elevenlabs.io/pricing
- Twilio trial ($15 credit, verified numbers only until upgraded) — twilio.com/pricing

_All figures are list-price estimates as of the "last updated" date and will shift with usage
mix, vendor plans, and the USD→AUD exchange rate. Treat as a planning estimate, not a quote._
