import type { Camera } from "../types";
import { haversineM } from "./mapPaths";

const HUB = { lat: 40.7549, lng: -73.984 }; // Times Sq

const MANHATTAN = {
  latMin: 40.728,
  latMax: 40.782,
  lngMin: -74.02,
  lngMax: -73.93,
};

/** NYC intersections that usually have visible traffic for live demos. */
const JUDGE_CAMERA_PATTERNS = [
  /7\s*ave\s*@\s*34/i,
  /broadway\s*@\s*42/i,
  /canal\s*st/i,
  /houston\s*st/i,
  /lexington\s*@\s*42/i,
];

export function judgePickCameras(cameras: Camera[], limit = 5): Camera[] {
  const picks: Camera[] = [];
  const seen = new Set<string>();
  for (const pat of JUDGE_CAMERA_PATTERNS) {
    const hit = cameras.find((c) => pat.test(c.name) && !seen.has(c.id));
    if (hit) {
      seen.add(hit.id);
      picks.push(hit);
    }
    if (picks.length >= limit) break;
  }
  return picks;
}

/** Pick a sensible default — Manhattan demo area, not first API row (often Bronx). */
export function pickDefaultCamera(cameras: Camera[]): Camera | null {
  if (!cameras.length) return null;

  const inManhattan = cameras.filter(
    (c) =>
      c.lat >= MANHATTAN.latMin &&
      c.lat <= MANHATTAN.latMax &&
      c.lng >= MANHATTAN.lngMin &&
      c.lng <= MANHATTAN.lngMax
  );
  const pool = inManhattan.length ? inManhattan : cameras;

  const named = pool.find((c) =>
    /7\s*ave\s*@\s*34/i.test(c.name)
  );
  if (named) return named;

  return pool.reduce((best, c) =>
    haversineM(HUB.lat, HUB.lng, c.lat, c.lng) <
    haversineM(HUB.lat, HUB.lng, best.lat, best.lng)
      ? c
      : best
  );
}

export function searchCameras(cameras: Camera[], query: string): Camera[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return cameras
    .filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.roads.some((r) => r.toLowerCase().includes(q))
    )
    .slice(0, 20);
}

export function camerasNear(
  cameras: Camera[],
  lat: number,
  lng: number,
  radiusM: number
): Camera[] {
  return cameras
    .filter((c) => haversineM(lat, lng, c.lat, c.lng) <= radiusM)
    .sort(
      (a, b) =>
        haversineM(lat, lng, a.lat, a.lng) -
        haversineM(lat, lng, b.lat, b.lng)
    );
}
