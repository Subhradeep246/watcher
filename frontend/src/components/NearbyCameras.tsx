import { useMemo } from "react";
import type { Camera } from "../types";
import { haversineM } from "../utils/mapPaths";

const RADIUS_M = 800;
const MAX_SHOW = 24;

function shortName(name: string): string {
  const at = name.indexOf("@");
  return at > 0 ? name.slice(0, at).trim() : name.length > 28 ? name.slice(0, 26) + "…" : name;
}

export default function NearbyCameras({
  cameras,
  selected,
  onSelect,
}: {
  cameras: Camera[];
  selected: Camera | null;
  onSelect: (c: Camera) => void;
}) {
  const nearby = useMemo(() => {
    if (!selected) return [];
    return cameras
      .map((c) => ({
        camera: c,
        dist: haversineM(selected.lat, selected.lng, c.lat, c.lng),
      }))
      .filter((x) => x.dist <= RADIUS_M)
      .sort((a, b) => a.dist - b.dist)
      .slice(0, MAX_SHOW);
  }, [cameras, selected]);

  if (!selected || nearby.length === 0) return null;

  return (
    <div className="nearby-cams">
      <div className="nearby-head">
        <span className="nearby-title">Nearby feeds</span>
        <span className="nearby-count">{nearby.length} within {RADIUS_M} m</span>
      </div>
      <div className="nearby-scroll" role="list">
        {nearby.map(({ camera, dist }) => {
          const active = camera.id === selected.id;
          const thumb = camera.sample_image
            ? camera.sample_image
            : `/api/cameras/${camera.id}/snapshot`;
          return (
            <button
              key={camera.id}
              type="button"
              role="listitem"
              className={`nearby-card${active ? " active" : ""}`}
              onClick={() => onSelect(camera)}
              title={camera.name}
            >
              <div className="nearby-thumb">
                <img src={thumb} alt="" loading="lazy" />
                {active && <span className="nearby-live">Live</span>}
              </div>
              <span className="nearby-name">{shortName(camera.name)}</span>
              <span className="nearby-dist">{Math.round(dist)} m</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
