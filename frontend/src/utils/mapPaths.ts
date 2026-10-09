import type { Camera, WatchResponse } from "../types";

const EARTH_R = 6_371_000;

export function haversineM(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number
): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dp / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_R * Math.asin(Math.sqrt(a));
}

export function bearingDeg(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number
): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dl = ((lng2 - lng1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x =
    Math.cos(p1) * Math.sin(p2) -
    Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function angleDiff(a: number, b: number): number {
  const d = Math.abs(a - b) % 360;
  return d <= 180 ? d : 360 - d;
}

export function normalizeDirection(dir: string): string {
  const d = dir.toLowerCase().replace(/bound/g, "").trim();
  if (d.startsWith("n") || d === "nb") return "north";
  if (d.startsWith("s") || d === "sb") return "south";
  if (d.startsWith("e") || d === "eb") return "east";
  if (d.startsWith("w") || d === "wb") return "west";
  if (d.includes("stop") || d.includes("idle") || d.includes("stationary"))
    return "stop";
  return d;
}

const DIR_BEARING: Record<string, number> = {
  north: 0,
  east: 90,
  south: 180,
  west: 270,
};

export function projectMeters(
  lat: number,
  lng: number,
  bearing: number,
  meters: number
): [number, number] {
  const br = (bearing * Math.PI) / 180;
  const p1 = (lat * Math.PI) / 180;
  const lng1 = (lng * Math.PI) / 180;
  const ang = meters / EARTH_R;
  const p2 = Math.asin(
    Math.sin(p1) * Math.cos(ang) +
      Math.cos(p1) * Math.sin(ang) * Math.cos(br)
  );
  const lng2 =
    lng1 +
    Math.atan2(
      Math.sin(br) * Math.sin(ang) * Math.cos(p1),
      Math.cos(ang) - Math.sin(p1) * Math.sin(p2)
    );
  return [(p2 * 180) / Math.PI, (lng2 * 180) / Math.PI];
}

function isAvenue(road: string): boolean {
  return /\b(ave|avenue|av|blvd|boulevard|broadway|fdr|highway|hwy|expy)\b/i.test(
    road
  );
}

function isStreet(road: string): boolean {
  return /\b(st|street|road|rd|way|place|pl|dr|drive|lane|ln)\b/i.test(road);
}

function roadTokens(road: string): string[] {
  const norm = road
    .toLowerCase()
    .replace(/[.@&/]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const stripped = norm.replace(/^(w|e|n|s)\s+/, "");
  const parts = stripped.split(" ").filter(Boolean);
  const tokens = new Set<string>([norm, stripped, ...parts]);
  const num = stripped.match(/\d+/)?.[0];
  if (num) tokens.add(num);
  return [...tokens];
}

function cameraMatchesRoad(cam: Camera, road: string): boolean {
  const keys = roadTokens(road);
  const hay = `${cam.name} ${cam.roads.join(" ")}`.toLowerCase();
  return keys.some((k) => k.length > 2 && hay.includes(k));
}

function pickRoadForDirection(
  direction: string,
  roads: string[]
): string | null {
  const isNS = direction === "north" || direction === "south";
  const avenue = roads.find(isAvenue);
  const street = roads.find(isStreet);
  if (isNS && avenue) return avenue;
  if (!isNS && street) return street;
  if (isNS && street) return street;
  if (!isNS && avenue) return avenue;
  return roads[0] ?? null;
}

/** Next DOT camera along the same named road in the predicted direction. */
function findNextCameraOnRoad(
  start: [number, number],
  direction: string,
  roads: string[],
  cameras: Camera[],
  exclude: Set<string>
): Camera | null {
  const targetRoad = pickRoadForDirection(direction, roads);
  if (!targetRoad) return null;

  const minDelta = 0.00015; // ~15m along axis
  const candidates: { cam: Camera; dist: number }[] = [];

  for (const cam of cameras) {
    if (exclude.has(cam.id)) continue;
    if (!cameraMatchesRoad(cam, targetRoad)) continue;

    const dist = haversineM(start[0], start[1], cam.lat, cam.lng);
    if (dist < 45 || dist > 650) continue;

    let along = false;
    if (direction === "north" && cam.lat > start[0] + minDelta) along = true;
    if (direction === "south" && cam.lat < start[0] - minDelta) along = true;
    if (direction === "east" && cam.lng > start[1] + minDelta) along = true;
    if (direction === "west" && cam.lng < start[1] - minDelta) along = true;
    if (!along) continue;

    candidates.push({ cam, dist });
  }

  candidates.sort((a, b) => a.dist - b.dist);
  return candidates[0]?.cam ?? null;
}

function pickPathEndpoint(
  start: [number, number],
  bearing: number,
  cameras: Camera[],
  exclude: Set<string>
): Camera | null {
  let best: { cam: Camera; score: number } | null = null;

  for (const cam of cameras) {
    if (exclude.has(cam.id)) continue;
    const dist = haversineM(start[0], start[1], cam.lat, cam.lng);
    if (dist < 55 || dist > 520) continue;

    const camBearing = bearingDeg(start[0], start[1], cam.lat, cam.lng);
    const delta = angleDiff(camBearing, bearing);
    if (delta > 38) continue;

    const score = delta * 3 + Math.abs(dist - 175) + (dist < 90 ? 80 : 0);
    if (!best || score < best.score) best = { cam, score };
  }

  return best?.cam ?? null;
}

/** Infer object map position from tracker + vision bbox center in frame. */
export function objectPosition(result: WatchResponse): [number, number] {
  const tracker = result.tracker;
  if (!tracker) return [0, 0];

  const box = result.vision?.bounding_box;
  if (!box) return [tracker.lat, tracker.lng];

  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const northM = ((500 - cy) / 500) * 40;
  const eastM = ((cx - 500) / 500) * 40;
  const [latN] = projectMeters(tracker.lat, tracker.lng, 0, northM);
  return projectMeters(latN, tracker.lng, 90, eastM);
}

export interface MapPath {
  direction: string;
  probability: number;
  risk: number;
  start: [number, number];
  end: [number, number];
  label: [number, number];
  roadName: string;
  endpointCamera: Camera | null;
}

export function buildMapPaths(
  result: WatchResponse,
  cameras: Camera[]
): MapPath[] {
  const tracker = result.tracker;
  const paths = result.prediction?.paths ?? [];
  const risks = result.risk?.path_risks ?? [];
  if (!tracker || !paths.length) return [];

  const start = objectPosition(result);
  const roads = tracker.intersection?.roads ?? [];
  const exclude = new Set([tracker.camera_id, result.active_camera_id]);
  for (const s of result.sightings ?? []) {
    if (s.detected) exclude.add(s.camera_id);
  }
  const out: MapPath[] = [];

  for (const raw of paths) {
    const direction = normalizeDirection(raw.direction);
    if (direction === "stop") continue;

    const bearing = DIR_BEARING[direction];
    if (bearing === undefined) continue;

    const risk =
      risks.find((r) => normalizeDirection(r.direction) === direction)
        ?.risk_score ?? 0.3;

    const roadName = pickRoadForDirection(direction, roads) ?? direction;
    const onRoad = findNextCameraOnRoad(
      start,
      direction,
      roads,
      cameras,
      exclude
    );
    const byBearing = pickPathEndpoint(
      start,
      bearing,
      cameras,
      exclude
    );
    const dest = onRoad ?? byBearing;

    const end: [number, number] = dest
      ? [dest.lat, dest.lng]
      : projectMeters(start[0], start[1], bearing, 200);

    const label: [number, number] = [
      start[0] + (end[0] - start[0]) * 0.55,
      start[1] + (end[1] - start[1]) * 0.55,
    ];

    out.push({
      direction,
      probability: raw.probability,
      risk,
      start,
      end,
      label,
      roadName,
      endpointCamera: dest,
    });
  }

  return out.sort((a, b) => b.probability - a.probability);
}

export function routeMidpoint(coords: [number, number][]): [number, number] {
  if (!coords.length) return [0, 0];
  return coords[Math.floor(coords.length / 2)];
}

export function riskColor(score: number): string {
  if (score >= 0.66) return "#ff4d4f";
  if (score >= 0.33) return "#ffa940";
  return "#52c41a";
}

export function directionLabel(dir: string): string {
  return dir.charAt(0).toUpperCase() + dir.slice(1);
}

export function roadShortName(road: string): string {
  return road.length > 18 ? road.slice(0, 16) + "…" : road;
}
