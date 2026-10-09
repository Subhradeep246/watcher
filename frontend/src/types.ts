export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Camera {
  id: string;
  name: string;
  lat: number;
  lng: number;
  roads: string[];
  image_url: string | null;
  sample_image: string | null;
}

export interface ModelRun {
  provider: "primary" | "secondary";
  model: string;
  ok: boolean;
  mocked: boolean;
  latency_ms: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  raw_text: string;
  parsed: Record<string, unknown> | null;
  error: string | null;
}

export interface AgentComparison {
  agent: string;
  primary: ModelRun | null;
  secondary: ModelRun | null;
}

export interface PathPrediction {
  direction: string;
  probability: number;
}

export interface PathRisk {
  direction: string;
  risk_score: number;
  reason: string;
}

export interface WatchResponse {
  camera_id: string;
  active_camera_id: string;
  mode: string;
  status?: "tracking" | "searching" | "lost" | "idle";
  searching_count?: number;
  handoff: {
    camera_id: string;
    camera_name: string;
    reason: string;
  } | null;
  vision: {
    object_label: string;
    object_class: "person" | "vehicle" | "other";
    detected: boolean;
    confidence: number;
    bounding_box: { x: number; y: number; width: number; height: number } | null;
    context: string;
    appearance?: string | null;
    identity_hint?: string | null;
  } | null;
  tracker: {
    camera_id: string;
    lat: number;
    lng: number;
    intersection: { roads: string[]; lanes: string[] };
  } | null;
  prediction: { paths: PathPrediction[] } | null;
  risk: { path_risks: PathRisk[] } | null;
  sightings?: CameraSighting[];
  comparisons: AgentComparison[];
  log: string[];
}

export interface CameraSighting {
  camera_id: string;
  camera_name: string;
  lat: number;
  lng: number;
  detected: boolean;
  confidence: number;
  object_label: string;
  bounding_box: { x: number; y: number; width: number; height: number } | null;
}

export interface Health {
  status: string;
  providers: {
    primary: { enabled: boolean; model: string; label: string };
    secondary: { enabled: boolean; model: string; label: string };
  };
  tracing?: { weave: boolean; project: string };
  data: { ny511_live: boolean };
  snapshot_interval_ms?: number;
}
