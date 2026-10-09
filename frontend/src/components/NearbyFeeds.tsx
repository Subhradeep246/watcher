import { useEffect, useMemo, useRef, useState } from "react";
import type { Camera, CameraSighting } from "../types";
import { haversineM } from "../utils/mapPaths";

const RADIUS_M = 500;
const MAX_SHOW = 8;
const REFRESH_MS = 2000;

function shortName(name: string): string {
  const at = name.indexOf("@");
  if (at > 0) return name.slice(0, at).trim();
  return name.length > 18 ? name.slice(0, 16) + "…" : name;
}

export default function NearbyFeeds({
  cameras,
  selected,
  sightings = [],
  scanning,
  onSelect,
}: {
  cameras: Camera[];
  selected: Camera | null;
  sightings?: CameraSighting[];
  scanning?: boolean;
  onSelect: (c: Camera) => void;
}) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!selected) return;
    const id = window.setInterval(() => setTick((t) => t + 1), REFRESH_MS);
    return () => clearInterval(id);
  }, [selected?.id]);

  const matched = useMemo(() => {
    const m = new Map<string, CameraSighting>();
    for (const s of sightings) {
      if (s.detected && s.confidence >= 0.48) m.set(s.camera_id, s);
    }
    return m;
  }, [sightings]);

  const nearby = useMemo(() => {
    if (!selected) return [];
    return cameras
      .filter((c) => c.id !== selected.id)
      .map((c) => ({
        camera: c,
        dist: haversineM(selected.lat, selected.lng, c.lat, c.lng),
      }))
      .filter((x) => x.dist <= RADIUS_M)
      .sort((a, b) => a.dist - b.dist)
      .slice(0, MAX_SHOW);
  }, [cameras, selected]);

  if (!selected) {
    return (
      <div className="nearby-panel nearby-panel-empty">
        Select a camera to see nearby feeds
      </div>
    );
  }

  return (
    <div className="nearby-panel">
      <div className="nearby-panel-head">
        <span className="nearby-panel-title">Nearby cameras</span>
        <span className="nearby-panel-sub">
          {scanning ? "AI scanning for match…" : `${nearby.length} within ${RADIUS_M}m`}
        </span>
      </div>
      <div className="nearby-grid" role="list">
        {nearby.length === 0 && (
          <p className="nearby-panel-empty">No other cameras in range</p>
        )}
        {nearby.map(({ camera, dist }) => {
          const hit = matched.get(camera.id);
          const thumb = camera.sample_image
            ? camera.sample_image
            : `/api/cameras/${camera.id}/snapshot?t=${tick}`;
          return (
            <button
              key={camera.id}
              type="button"
              role="listitem"
              className={`nearby-card${hit ? " matched" : ""}${scanning && !hit ? " scanning" : ""}`}
              onClick={() => onSelect(camera)}
              title={
                hit
                  ? `${camera.name} — matched ${Math.round(hit.confidence * 100)}%`
                  : camera.name
              }
            >
              <div className="nearby-card-img">
                <img src={thumb} alt="" loading="lazy" />
                {hit && (
                  <span className="nearby-match">
                    {Math.round(hit.confidence * 100)}%
                  </span>
                )}
              </div>
              <span className="nearby-card-name">{shortName(camera.name)}</span>
              <span className="nearby-card-dist">{Math.round(dist)} m</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
