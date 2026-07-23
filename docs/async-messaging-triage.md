# CROSSUB AI Triage — Async Messaging (Text) · Guide

_How to use and test the text / async-messaging version of the CROSSUB AI triage._
_如何使用和测试 CROSSUB 文字（异步消息）版 AI 智能分派。_

Last updated: 2026-07-23

---

## 1. What this is · 这是什么

**English.** Instead of talking to a live phone robot, a tenant, owner, or contractor simply
**types a message** (in the app, or later by voicemail/email). CROSSUB's AI reads it, decides
**which team should handle it** (Leasing, Maintenance, Inspection, Accounting, Tribunal, or
General), judges **how urgent** it is, writes a **short summary** for the officer, and — for a
genuine repair from someone with a property on file — can **create the maintenance job
automatically**. It works in **English and 中文**.

**中文。** 租客、业主或维修师傅不需要和实时电话机器人对话，只要**打字发一条消息**（在 App 里，
以后也可以通过语音留言/邮件）。CROSSUB 的 AI 会读懂它，判断**应该由哪个团队处理**（租赁、维修、
验房、财务、仲裁，或综合），判断**紧急程度**，为同事写一段**简短摘要**；如果是有房产在档的人报修，
还能**自动创建维修工单**。**中英文都支持。**

> Why text instead of the real-time phone AI? Real-time speech-to-text was unreliable on a phone
> line (it once heard "end lease" as "end list"). Reading a **typed** message is 100% accurate —
> no speech recognition in the loop. 为什么用文字而不是实时电话 AI？电话线上的实时语音转文字不可靠
> （曾把 "end lease" 听成 "end list"）。读**打字**消息是 100% 准确的——中间没有语音识别。

---

## 2. The tester · 测试工具

There is one tester site with **two tabs** at the top:

| Tab | What it does |
|---|---|
| **Voice tester** | Talk to the real-time phone AI in the browser (the earlier demo). |
| **Text / messaging** | Type a message and watch the AI triage it. **This guide is about this tab.** |

Staging tester URL: **https://crossub-staging-voice-tester.onrender.com** → click **“Text / messaging →”**.

> **Nothing is saved.** The text tester runs in **preview (dry-run) mode**: it shows you exactly
> what the AI *would* do, but it does **not** create any real record, message, or job. You can run
> it as many times as you like with zero side effects. **不会保存任何东西**——文字测试是**预览（演练）
> 模式**，只展示 AI *会*怎么做，但**不会**创建任何真实记录、消息或工单，可以随便试。

---

## 3. How to use it · 使用步骤

1. Open the tester and click the **Text / messaging** tab.
2. (Optional) Click one of the **example** chips — including two **中文** examples — to fill the form instantly.
3. Choose **who is sending**: Tenant / Owner / Contractor.
4. (Optional) type a **name** and a **subject**.
5. Keep **“Property on file”** ticked to simulate a known sender (untick it to see what happens for an unknown sender).
6. Type (or edit) the **message**.
7. Click **Run AI triage**.
8. Read the **result card**:
   - **Department** badge — which team it was routed to.
   - **Urgency** badge — LOW / MEDIUM / HIGH / CRITICAL.
   - **AI summary** — the one-line summary an officer would see.
   - **Action** — either **“Would create a maintenance order”** (with the order type and the
     extracted issues), or a line explaining why no automatic action was taken.
   - A footer confirming it was a **dry run · no records were written**.

步骤（中文）：打开测试工具 → 点 **Text / messaging** 标签 → （可选）点一个**示例**（含两个中文示例）→
选择**发送人身份**（租客/业主/师傅）→ （可选）填姓名和主题 → 勾选 **Property on file** 模拟在档用户 →
输入消息 → 点 **Run AI triage** → 查看结果卡片（部门、紧急度、AI 摘要、是否会自动建工单）。

---

## 4. What to try (a good 5-minute demo) · 建议演示

| Try this | You should see |
|---|---|
| A repair, e.g. *“the kitchen tap is leaking”* (Tenant, property on file) | **Maintenance**, high urgency, **would create a maintenance order** |
| A 中文 repair, e.g. *“热水器坏了，两天没有热水”* | **Maintenance** — proves it works in Chinese too |
| A rent question, e.g. *“has my rent been received?”* | **Accounting**, **no order** (it's a question, not a repair) |
| A move-out, e.g. *“我想退租”* | **Leasing** — routed to the right team |
| The same repair, but untick **Property on file** | **Maintenance**, but **no order** — a person would follow up (we never create a job without a known property) |

---

## 5. Technical reference (for Tony) · 技术说明

### 5.1 Architecture

```
 Text tester page  (crossub-voice-agent/web  →  /messaging)
        │  POST /api/triage-preview   (browser → tester's own server route)
        ▼
 Tester server route  (web/app/api/triage-preview/route.ts)
        │  adds header x-voice-service-token   (secret stays server-side)
        ▼
 CROSSUB API   POST /api/voice/triage-preview        ← NEW, machine-authed
        │  reuses the REAL classifier + issue extractor
        │  (email-triage-ai.util + support-maintenance-ai.util + AiService)
        │  DRY RUN — writes nothing, ignores MESSAGE_TRIAGE_ENABLED
        ▼
 { classification: {department, urgency, summary, confidence, confident},
   action: {wouldSpawnMaintenance, reasonNotSpawned, orderType, maintenance{…}},
   meta: {model, dryRun:true, propertyOnFile} }
```

This is the **same brain** as the production async pipeline (`MessageTriageService`, fired from
`MessagingService.createThread` when `MESSAGE_TRIAGE_ENABLED=true`). The preview endpoint calls the
identical pure classifier + extractor utils, but **skips every DB write** — no `CommConversation`
stamp, no `MaintenanceRequest`, no officer notification.

### 5.2 The endpoint

`POST /api/voice/triage-preview` — machine-authed by the `x-voice-service-token` header
(`VoiceServiceGuard`, same as every other `/api/voice/*` route). No user login.

Request body:

```jsonc
{
  "body": "the kitchen tap is leaking…",   // required
  "subject": "Leaking tap",                // optional
  "senderName": "Emma Tenant",             // optional (fed to the prompt as the From name)
  "senderRole": "tenant",                  // optional: tenant | landlord | contractor | inspector | other
  "propertyOnFile": true                    // optional, default true — gates wouldSpawnMaintenance
}
```

Response (200):

```jsonc
{
  "ok": true,
  "classification": {
    "department": "MAINTENANCE",            // LEASING|MAINTENANCE|INSPECTION|ACCOUNTING|TRIBUNAL|GENERAL
    "urgency": "HIGH",                       // LOW|MEDIUM|HIGH|CRITICAL
    "summary": "Tenant reports a leaking kitchen tap…",
    "suggestedAssignee": "Maintenance team / Plumber",
    "confidence": 95,
    "confident": true                        // confidence >= 60
  },
  "action": {
    "wouldSpawnMaintenance": true,
    "reasonNotSpawned": null,                // else: not_maintenance | low_confidence | no_property_on_file | not_a_repair_request
    "orderType": "TENANT_REQUEST",           // owner → PROPERTY_MAINTENANCE, else TENANT_REQUEST
    "maintenance": { "isRepairRequest": true, "confidence": 95, "issues": [ { "issueType": "…", "detail": "…", "urgent": true } ], "propertyAddress": null, "tenantName": "Emma Tenant" }
  },
  "meta": { "model": "claude-haiku-4-5", "dryRun": true, "propertyOnFile": true }
}
```

If the API has **no AI key**, it returns `{ "ok": false, "reason": "ai_unavailable", "meta": {…} }`
(HTTP 200) — the tester shows a friendly message instead of crashing.

**`wouldSpawnMaintenance`** is a faithful mirror of the real spawn gate:
`confident (≥60) && department == MAINTENANCE && propertyOnFile && (2nd extraction: isRepairRequest && confidence ≥ 60 && issues > 0)`.

### 5.3 Environment variables (the tester)

Add to the tester's env (locally `web/.env.local`, on Render the tester service):

```bash
VOICE_API_BASE_URL=https://crossub-api-staging.onrender.com   # the CROSSUB API, no trailing slash
VOICE_SERVICE_TOKEN=<same value as VOICE_SERVICE_TOKEN on the API>
```

The API side needs **an AI key** (`ANTHROPIC_API_KEY`) — already present on `crossub-api-staging`.
The preview does **not** require `MESSAGE_TRIAGE_ENABLED`, so you can demo the brain on staging
without turning the live pipeline on.

### 5.4 Run locally

```bash
# 1) API (from crossub_web)
pnpm --filter @crossub/api start:dev          # or: node apps/api/dist/main.js  (needs ANTHROPIC_API_KEY + DATABASE_URL)

# 2) Tester (from crossub-voice-agent/web)
cp .env.local.example .env.local              # set VOICE_API_BASE_URL=http://localhost:3000 + VOICE_SERVICE_TOKEN
npm install && npm run dev                    # open http://localhost:3000/messaging
```

Quick curl (no browser):

```bash
curl -s -X POST "$VOICE_API_BASE_URL/api/voice/triage-preview" \
  -H 'content-type: application/json' \
  -H "x-voice-service-token: $VOICE_SERVICE_TOKEN" \
  -d '{"body":"the kitchen tap is leaking, water pooling under the sink","senderRole":"tenant"}'
```

### 5.5 Deploy the demo on staging

1. Deploy the API change (the new `/api/voice/triage-preview` route) — commit + push `crossub_web`; `crossub-api-staging` auto-deploys.
2. On the Render **tester** service (`crossub-staging-voice-tester`), add env vars `VOICE_API_BASE_URL` (= the API URL) and `VOICE_SERVICE_TOKEN` (= the API's token), then redeploy.
3. Open the tester → **Text / messaging** tab → run the examples.

### 5.6 Safety notes

- **No writes.** The preview service only calls the AI; it never touches the database, so no
  conversation, order, or notification is ever created by the tester.
- **Flag-independent.** It ignores `MESSAGE_TRIAGE_ENABLED`; enabling the real production pipeline
  is a separate decision (that flag makes real in-app messages create real Comm Hub items + orders).
- **Same auth as the phone endpoints.** The `x-voice-service-token` secret is only ever read
  server-side by the tester's route; it never reaches the browser.
