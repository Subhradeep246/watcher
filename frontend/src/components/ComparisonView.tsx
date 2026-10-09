import type { AgentComparison, ModelRun } from "../types";

const AGENT_INFO: Record<string, { title: string; blurb: string }> = {
  vision: {
    title: "Vision Inspector",
    blurb: "Finds the object in the camera image and draws a bounding box.",
  },
  tracker: {
    title: "Tracker",
    blurb: "Maps the object to a real NYC intersection on the map.",
  },
  prediction: {
    title: "Prediction Planner",
    blurb: "Guesses which direction the object is likely to move next.",
  },
  risk: {
    title: "Risk Assessor",
    blurb: "Scores each path using live NYC crash history nearby.",
  },
};

function Metric({ run }: { run: ModelRun | null }) {
  if (!run) return <span className="muted">—</span>;
  if (run.mocked) return <span className="badge badge-mock">mock</span>;
  if (!run.ok) return <span className="badge badge-err">error</span>;
  return (
    <span className="metrics">
      <span className="badge badge-ok">{run.latency_ms} ms</span>
      {run.total_tokens != null && (
        <span className="badge">{run.total_tokens} tok</span>
      )}
      {run.parsed ? (
        <span className="badge badge-ok">valid JSON</span>
      ) : (
        <span className="badge badge-warn">unparsed</span>
      )}
    </span>
  );
}

function pickWinner(a: ModelRun | null, b: ModelRun | null): "primary" | "secondary" | null {
  const score = (r: ModelRun | null) => {
    if (!r || r.mocked || !r.ok) return -1;
    if (!r.parsed) return 0;
    return 1000 - r.latency_ms;
  };
  const sa = score(a);
  const sb = score(b);
  if (sa < 0 && sb < 0) return null;
  if (sa === sb) return null;
  return sa > sb ? "primary" : "secondary";
}

function totals(runs: ModelRun[]) {
  return {
    ms: runs.reduce((s, r) => s + (r.latency_ms || 0), 0),
    tok: runs.reduce((s, r) => s + (r.total_tokens || 0), 0),
    ok: runs.filter((r) => r.ok && r.parsed && !r.mocked).length,
  };
}

export default function ComparisonView({
  comparisons,
  primaryLabel,
  secondaryLabel,
}: {
  comparisons: AgentComparison[];
  primaryLabel: string;
  secondaryLabel: string;
}) {
  if (!comparisons.length)
    return (
      <div className="panel-empty">
        <p className="empty-title">No analysis yet</p>
        <p className="empty-hint">
          Pick a camera, describe what to watch, then hit <strong>Analyze</strong>.
          You'll see {primaryLabel} vs {secondaryLabel} side-by-side for each AI agent.
        </p>
      </div>
    );

  const primaryRuns = comparisons.map((c) => c.primary).filter(Boolean) as ModelRun[];
  const secondaryRuns = comparisons.map((c) => c.secondary).filter(Boolean) as ModelRun[];
  const gTot = totals(primaryRuns);
  const mTot = totals(secondaryRuns);
  const primaryLead =
    gTot.ok > mTot.ok || (gTot.ok === mTot.ok && gTot.ms <= mTot.ms);

  return (
    <div className="comparison">
      <p className="cmp-intro">
        Same prompt, same image — two models, four agents. Green border = faster
        valid JSON for that step.
      </p>

      <div className="scoreboard">
        <div className={`score ${primaryLead ? "lead" : ""}`}>
          <div className="score-name">{primaryLabel}</div>
          <div className="score-num">
            {gTot.ms}
            <span className="unit">ms</span>
          </div>
          <div className="score-sub">
            {gTot.tok} tok · {gTot.ok}/{comparisons.length} valid
          </div>
        </div>
        <div className="score-vs">vs</div>
        <div className={`score ${!primaryLead ? "lead" : ""}`}>
          <div className="score-name">{secondaryLabel}</div>
          <div className="score-num">
            {mTot.ms}
            <span className="unit">ms</span>
          </div>
          <div className="score-sub">
            {mTot.tok} tok · {mTot.ok}/{comparisons.length} valid
          </div>
        </div>
      </div>

      {comparisons.map((c) => {
        const info = AGENT_INFO[c.agent] ?? { title: c.agent, blurb: "" };
        const winner = pickWinner(c.primary, c.secondary);
        return (
          <div className="cmp-row" key={c.agent}>
            <div className="cmp-agent-head">
              <div>
                <span className="cmp-agent">{info.title}</span>
                <p className="cmp-blurb">{info.blurb}</p>
              </div>
            </div>
            <div className="cmp-grid">
              <div className="cmp-head">{primaryLabel}</div>
              <div className="cmp-head">{secondaryLabel}</div>
              <div
                className={`cmp-cell ${winner === "primary" ? "cmp-winner" : ""}`}
              >
                <Metric run={c.primary} />
                {c.primary?.error && (
                  <div className="cmp-error">{c.primary.error}</div>
                )}
                <pre className="cmp-json">
                  {c.primary?.parsed
                    ? JSON.stringify(c.primary.parsed, null, 2)
                    : c.primary?.raw_text
                      ? c.primary.raw_text.slice(0, 500)
                      : "(no output)"}
                </pre>
              </div>
              <div
                className={`cmp-cell ${winner === "secondary" ? "cmp-winner" : ""}`}
              >
                <Metric run={c.secondary} />
                {c.secondary?.error && (
                  <div className="cmp-error">{c.secondary.error}</div>
                )}
                <pre className="cmp-json">
                  {c.secondary?.parsed
                    ? JSON.stringify(c.secondary.parsed, null, 2)
                    : c.secondary?.raw_text
                      ? c.secondary.raw_text.slice(0, 500)
                      : "(no output)"}
                </pre>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
