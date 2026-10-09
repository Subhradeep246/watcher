import { useEffect, useRef } from "react";

export default function AgentChat({
  log,
  compact,
}: {
  log: string[];
  compact?: boolean;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [log.length]);

  if (!log.length) {
    return (
      <div className={`panel-empty${compact ? " panel-empty-compact" : ""}`}>
        <p className="empty-hint">Tracking log appears here…</p>
      </div>
    );
  }

  return (
    <div className={`chat${compact ? " chat-compact" : ""}`}>
      {log.map((line, i) => {
        const bracket = line.match(/^\[[^\]]+\]\s*/);
        const time = bracket ? bracket[0].trim() : "";
        const msg = bracket ? line.slice(bracket[0].length) : line;
        return (
          <div className="chat-line" key={`${i}-${line.slice(0, 24)}`}>
            {time && <span className="chat-time">{time}</span>}
            <span className="chat-msg">{msg}</span>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
