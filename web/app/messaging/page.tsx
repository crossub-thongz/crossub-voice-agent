"use client";

import { useCallback, useState } from "react";

type Role = "tenant" | "landlord" | "contractor";

type TriageResult = {
  ok: boolean;
  reason?: string;
  error?: string;
  classification?: {
    department: string;
    urgency: string;
    summary: string;
    suggestedAssignee: string | null;
    confidence: number;
    confident: boolean;
  };
  action?: {
    wouldSpawnMaintenance: boolean;
    reasonNotSpawned: string | null;
    orderType: string | null;
    maintenance: {
      isRepairRequest: boolean;
      confidence: number;
      propertyAddress: string | null;
      tenantName: string | null;
      issues: { issueType: string; detail: string; urgent: boolean }[];
    } | null;
  };
  meta?: { model: string; dryRun: boolean; propertyOnFile: boolean };
};

type Example = { label: string; role: Role; subject: string; body: string };

const EXAMPLES: Example[] = [
  {
    label: "Leaking tap (EN)",
    role: "tenant",
    subject: "Leaking kitchen tap",
    body: "Hi, the kitchen tap in my unit has been leaking for two days and water is pooling under the sink. Can someone please come and fix it?",
  },
  {
    label: "热水器坏了 (中文)",
    role: "tenant",
    subject: "热水器没有热水",
    body: "你好，我租住的房子热水器坏了，已经两天没有热水了，请尽快安排师傅上门维修，谢谢。",
  },
  {
    label: "Rent question (EN)",
    role: "tenant",
    subject: "Rent payment",
    body: "Hi, I just want to check whether my rent payment for this month has been received. Thanks.",
  },
  {
    label: "退租 (中文)",
    role: "tenant",
    subject: "我想退租",
    body: "你好，我打算下个月底搬出去，请问退租需要办理什么手续？谢谢。",
  },
  {
    label: "Owner: fix fence (EN)",
    role: "landlord",
    subject: "Fence repair at my property",
    body: "Hello, I own the property at 42 Lorikeet Lane. The back fence has fallen over after the storm — please arrange a repair.",
  },
  {
    label: "Update phone (EN)",
    role: "tenant",
    subject: "Contact details",
    body: "Hi, could you please update the contact phone number you have on file for me?",
  },
];

const DEPT_LABEL: Record<string, string> = {
  LEASING: "Leasing",
  MAINTENANCE: "Maintenance",
  INSPECTION: "Inspection",
  ACCOUNTING: "Accounting",
  TRIBUNAL: "Tribunal",
  GENERAL: "General",
};

const NOT_SPAWNED_LABEL: Record<string, string> = {
  not_maintenance: "Not a maintenance request — no repair order.",
  low_confidence:
    "Low confidence — routed to General for a person to review, no order.",
  no_property_on_file:
    "No property on file for this sender — a person would follow up, no order.",
  not_a_repair_request:
    "No actionable repair found in the message — no order.",
};

export default function MessagingTester() {
  const [role, setRole] = useState<Role>("tenant");
  const [senderName, setSenderName] = useState("");
  const [propertyOnFile, setPropertyOnFile] = useState(true);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TriageResult | null>(null);

  const applyExample = useCallback((ex: Example) => {
    setRole(ex.role);
    setSubject(ex.subject);
    setBody(ex.body);
    setResult(null);
    setError(null);
  }, []);

  const runTriage = useCallback(async () => {
    if (!body.trim()) {
      setError("Type a message first.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/triage-preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          body,
          subject: subject.trim() || undefined,
          senderName: senderName.trim() || undefined,
          senderRole: role,
          propertyOnFile,
        }),
      });
      const data = (await res.json()) as TriageResult;
      if (!res.ok) throw new Error(data.error || "Triage failed");
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [body, subject, senderName, role, propertyOnFile]);

  return (
    <main className="wrap">
      <header className="hd">
        <div className="brand">
          CROSSUB<span className="brandLight"> AI Triage</span>
        </div>
        <div className="sub">Async message tester · English + 中文</div>
        <nav className="nav">
          <a className="navlink" href="/">
            ← Voice tester
          </a>
          <span className="navlink active">Text / messaging</span>
        </nav>
      </header>

      <section className="form">
        <label className="fieldLabel">Try an example</label>
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              className="chip"
              onClick={() => applyExample(ex)}
              type="button"
            >
              {ex.label}
            </button>
          ))}
        </div>

        <label className="fieldLabel">I am a…</label>
        <div className="segment">
          {(["tenant", "landlord", "contractor"] as Role[]).map((r) => (
            <button
              key={r}
              type="button"
              className={`seg ${role === r ? "on" : ""}`}
              onClick={() => setRole(r)}
            >
              {r === "tenant" ? "Tenant" : r === "landlord" ? "Owner" : "Contractor"}
            </button>
          ))}
        </div>

        <div className="row">
          <div className="col">
            <label className="fieldLabel" htmlFor="name">
              Your name (optional)
            </label>
            <input
              id="name"
              className="input"
              value={senderName}
              onChange={(e) => setSenderName(e.target.value)}
              placeholder="e.g. Emma Tenant"
            />
          </div>
          <label className="toggle" title="Simulate whether this sender has a property on file">
            <input
              type="checkbox"
              checked={propertyOnFile}
              onChange={(e) => setPropertyOnFile(e.target.checked)}
            />
            <span>Property on file</span>
          </label>
        </div>

        <label className="fieldLabel" htmlFor="subject">
          Subject (optional)
        </label>
        <input
          id="subject"
          className="input"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="e.g. Leaking kitchen tap"
        />

        <label className="fieldLabel" htmlFor="body">
          Message
        </label>
        <textarea
          id="body"
          className="textarea"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={5}
          placeholder="Type the message a tenant / owner / contractor would send…"
        />

        <button className="cta full" onClick={runTriage} disabled={loading} type="button">
          {loading ? "Analysing…" : "Run AI triage"}
        </button>
        {error && <p className="err">{error}</p>}
      </section>

      {result && <ResultCard result={result} />}
    </main>
  );
}

function ResultCard({ result }: { result: TriageResult }) {
  if (!result.ok) {
    return (
      <section className="card">
        <p className="hint">
          {result.reason === "ai_unavailable"
            ? "The AI is not configured on this API instance (no AI key). Set the key on the API to run the triage brain."
            : "The triage could not be completed."}
        </p>
      </section>
    );
  }

  const c = result.classification!;
  const a = result.action!;
  const dept = DEPT_LABEL[c.department] ?? c.department;

  return (
    <section className="card">
      <div className="badges">
        <span className={`badge dept ${c.department}`}>{dept}</span>
        <span className={`badge urg ${c.urgency}`}>{c.urgency}</span>
        <span className={`badge conf ${c.confident ? "ok" : "warn"}`}>
          {c.confidence}% {c.confident ? "confident" : "needs review"}
        </span>
      </div>

      <p className="summary">{c.summary}</p>
      {c.suggestedAssignee && (
        <p className="assignee">Suggested: {c.suggestedAssignee}</p>
      )}

      <div className={`action ${a.wouldSpawnMaintenance ? "spawn" : "route"}`}>
        {a.wouldSpawnMaintenance ? (
          <>
            <strong>Would create a maintenance order</strong>
            {a.orderType && <span className="tag">{a.orderType}</span>}
            {a.maintenance && a.maintenance.issues.length > 0 && (
              <ul className="issues">
                {a.maintenance.issues.map((iss, i) => (
                  <li key={i}>
                    {iss.issueType}
                    {iss.urgent && <span className="urgentTag">urgent</span>}
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <span>
            {(a.reasonNotSpawned && NOT_SPAWNED_LABEL[a.reasonNotSpawned]) ||
              "Routed to the right team — no automatic action."}
          </span>
        )}
      </div>

      {result.meta && (
        <p className="meta">
          Dry run · no records were written · model {result.meta.model}
        </p>
      )}
    </section>
  );
}
