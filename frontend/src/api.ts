import type { BoundingBox, Camera, Health, WatchResponse } from "./types";

const BASE = "/api";

export async function getHealth(): Promise<Health> {
  const r = await fetch(`${BASE}/health`);
  return r.json();
}

export async function getCameras(): Promise<Camera[]> {
  const r = await fetch(`${BASE}/cameras`);
  return r.json();
}

export interface WatchPayload {
  camera_id: string;
  object_description: string;
  mode: "nyc" | "factory" | "hospital";
  image_data_uri?: string;
  bounding_box?: BoundingBox;
  fast?: boolean;
  skip_camera_scan?: boolean;
}

export async function watch(payload: WatchPayload): Promise<WatchResponse> {
  const r = await fetch(`${BASE}/watch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`watch failed: ${r.status}`);
  return r.json();
}

/** Fast tracking tick — dual-model vision, skips camera scan & model comparison. */
export async function track(payload: WatchPayload): Promise<WatchResponse> {
  const r = await fetch(`${BASE}/track`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`track failed: ${r.status}`);
  return r.json();
}

/** Fetch a road-following route polyline [[lat, lng], ...]. */
export async function getRoadRoute(
  from: [number, number],
  to: [number, number]
): Promise<[number, number][]> {
  const params = new URLSearchParams({
    from_lat: String(from[0]),
    from_lng: String(from[1]),
    to_lat: String(to[0]),
    to_lng: String(to[1]),
  });
  const r = await fetch(`${BASE}/route?${params}`);
  if (!r.ok) return [from, to];
  const data = (await r.json()) as { coordinates: [number, number][] };
  return data.coordinates?.length ? data.coordinates : [from, to];
}
