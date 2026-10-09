import type { WatchResponse } from "../types";

export default function IncidentBrief({ history }: { history: WatchResponse[] }) {
  const latest = history[history.length - 1];
  const brief = latest?.brief;
  const exportBrief = () => {
    if (!brief) return;
    const blob = new Blob([JSON.stringify({ product: "TheWatcher", exported_at: new Date().toISOString(), brief, timeline: history }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `watcher-brief-${brief.id.slice(0, 10)}.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  if (!brief) return <div className="panel-empty"><p className="empty-title">Observation → action</p><p className="empty-hint">Scan a camera or click a target to build an evidence brief with source labels, recommended next steps, and an exportable timeline.</p></div>;
  return <div className="incident-brief">
    <div className="brief-row"><span className={`priority priority-${brief.priority}`}>{brief.priority.toUpperCase()}</span><button type="button" className="btn-sm" onClick={exportBrief}>Export evidence ↓</button></div>
    <h3>{brief.title}</h3>
    <p>{brief.summary}</p>
    <div className="brief-metrics">
      <div><b>{Math.round(brief.confidence * 100)}%</b><span>model confidence</span></div>
      <div><b>{brief.risk_peak == null ? "Unverified" : `${Math.round(brief.risk_peak * 100)}%`}</b><span>estimated risk</span></div>
    </div>
    <div className="source-label">Frame: {brief.frame_source.replace(/_/g, " ")} · {new Date(brief.created_at).toLocaleTimeString()}</div>
    <h4>Next steps</h4><ol className="brief-actions">{brief.actions.map(a => <li key={a}>{a}</li>)}</ol>
    {brief.limitations.length > 0 && <div className="brief-limitations"><b>Evidence limits</b>{brief.limitations.map(w => <p key={w}>{w}</p>)}</div>}
    <h4>Incident context</h4>{brief.incident_context.map(c => <p className="event-meta" key={c}>{c}</p>)}
    <details><summary>Inference evidence & frame fingerprint</summary>{brief.evidence.map((e, i) => <p className="event-meta" key={i}>{e}</p>)}<code className="frame-hash">{brief.frame_sha256 ?? "No frame"}</code><p className="event-meta">Request {brief.id}</p></details>
    <h4>Session timeline · {history.length} observations</h4>
    <div className="brief-timeline">{[...history].reverse().map((r, i) => <div className="timeline-item" key={r.brief?.id ?? i}><span className="chat-time">{r.brief ? new Date(r.brief.created_at).toLocaleTimeString() : "—"}</span><p><b>{r.vision?.detected ? r.vision.object_label : "Target unconfirmed"}</b><br/>{r.brief?.camera_name} · {r.status} {r.handoff && "· handoff proposed"}</p></div>)}</div>
  </div>;
}
