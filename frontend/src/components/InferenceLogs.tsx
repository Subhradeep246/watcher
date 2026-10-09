import { useEffect, useState } from "react";
import { getTelemetry } from "../api";
import type { Telemetry } from "../types";

export default function InferenceLogs() {
  const [data, setData] = useState<Telemetry | null>(null);
  const [error, setError] = useState("");
  const [onlyCoreWeave, setOnlyCoreWeave] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await getTelemetry();
        if (active) { setData(next); setError(""); }
      } catch { if (active) setError("Inference journal unavailable. Retrying…"); }
      if (active) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, []);
  const rows = data?.events.filter(e => !onlyCoreWeave || e.host.includes("CoreWeave")) ?? [];
  return <div className="inference-logs">
    <p className="cmp-intro">Real API calls, including W&B Inference on CoreWeave. Dedicated GPU infrastructure logs require a connected deployment.</p>
    <div className="brief-metrics">
      <div><b>{data?.summary.calls ?? 0}</b><span>calls</span></div>
      <div><b>{data ? `${data.summary.successes}/${data.summary.calls}` : "—"}</b><span>successful</span></div>
      <div><b>{data?.summary.p95_ms == null ? "—" : `${data.summary.p95_ms} ms`}</b><span>p95 latency</span></div>
      <div><b>{data?.summary.tokens ?? 0}</b><span>tokens</span></div>
    </div>
    <label className="check-inline"><input type="checkbox" checked={onlyCoreWeave} onChange={e => setOnlyCoreWeave(e.target.checked)} /> CoreWeave calls only</label>
    {error && <p className="cmp-error" role="status">{error}</p>}
    {!rows.length && <div className="panel-empty"><p>Run a Scan to populate the inference journal.</p></div>}
    {rows.map(e => <article className="inference-event" key={e.id}>
      <div className="brief-row"><b>{e.agent}</b><span className={`badge ${e.mocked ? "badge-mock" : e.ok ? "badge-ok" : "badge-err"}`}>{e.mocked ? "disabled" : e.ok ? "success" : "failed"}</span></div>
      <p className="event-host">{e.host}</p><code className="event-model">{e.model}</code>
      <p className="event-meta">{new Date(e.timestamp).toLocaleTimeString()} · {e.latency_ms} ms · {e.total_tokens ?? "—"} tokens · HTTP {e.status_code ?? "—"} · {e.retries} retries</p>
      <p className="event-meta">{e.valid_json ? "JSON parsed" : "No parsed JSON"} · trace {e.request_id.slice(0, 10) || "standalone"}</p>
      {e.error && <p className="cmp-error">{e.error}</p>}
    </article>)}
    <p className="event-meta">{data?.retention}</p>
  </div>;
}
