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
  brief: IncidentBriefData | null;
  description_review?: DescriptionReview | null;
}

export interface DescriptionReview {
  status: "not_configured" | "skipped" | "evaluated" | "unavailable";
  requested_description: string;
  reported_description: string;
  contradiction_probability: number | null;
  coverage_probability: number | null;
  model: string;
  latency_ms: number;
  input_tokens: number | null;
  output_tokens: number | null;
  note: string;
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
    // Slot keys: primary = primary model, secondary = comparison model.
    primary: { enabled: boolean; model: string; label: string };
    secondary: { enabled: boolean; model: string; label: string };
  };
  tracing?: { weave: boolean; project: string };
  data: { ny511_live: boolean };
  snapshot_interval_ms?: number;
}

export interface IncidentBriefData {
  id: string;
  created_at: string;
  title: string;
  priority: "verify" | "observe" | "review";
  frame_source: string;
  frame_sha256: string | null;
  camera_name: string;
  confidence: number;
  summary: string;
  actions: string[];
  limitations: string[];
  incident_context: string[];
  risk_supported: boolean;
  risk_peak: number | null;
  evidence: string[];
}
export interface Telemetry {
  events: {
    id: string; request_id: string; timestamp: string; agent: string;
    slot: string; host: string; model: string; ok: boolean; mocked: boolean;
    latency_ms: number; total_tokens: number | null; valid_json: boolean;
    status_code: number | null; retries: number; error: string | null;
  }[];
  summary: { calls: number; successes: number; tokens: number; p95_ms: number | null };
  retention: string;
}
