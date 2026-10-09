import { useCallback, useEffect, useRef, useState } from "react";
import { getCameras, getHealth, watch, track } from "./api";
import type { BoundingBox, Camera, Health, WatchResponse } from "./types";
import MapView from "./components/MapView";
import SnapshotPanel, { type AnalyzedFrame } from "./components/SnapshotPanel";
import NearbyFeeds from "./components/NearbyFeeds";
import CameraSearch from "./components/CameraSearch";
import AgentChat from "./components/AgentChat";
import InferenceLogs from "./components/InferenceLogs";
import IncidentBrief from "./components/IncidentBrief";
import { judgePickCameras, pickDefaultCamera } from "./utils/cameras";

type Mode = "nyc" | "factory" | "hospital";
type Tab = "log" | "models" | "brief";
type TrackStatus = "idle" | "locking" | "tracking" | "searching" | "lost";

function visionTrackLabel(v: WatchResponse["vision"]): string {
  if (!v) return "";
  if (v.appearance) return v.appearance;
  if (v.identity_hint) return `${v.object_label} · ${v.identity_hint}`;
  return v.object_label ?? "";
}

function visionShortLabel(v: WatchResponse["vision"]): string {
  if (!v) return "—";
  return v.object_label || "—";
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<Camera | null>(null);
  const [description, setDescription] = useState("");
  const [mode] = useState<Mode>("nyc");
  const [tab, setTab] = useState<Tab>("brief");
  const [history, setHistory] = useState<WatchResponse[]>([]);
  const [result, setResult] = useState<WatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [followEnabled, setFollowEnabled] = useState(true);
  const [handoffNotice, setHandoffNotice] = useState<string | null>(null);
  const [pickSeed, setPickSeed] = useState<BoundingBox | null>(null);
  const [followingPick, setFollowingPick] = useState(false);
  const [trackStatus, setTrackStatus] = useState<TrackStatus>("idle");
  const [trailIds, setTrailIds] = useState<string[]>([]);
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const trackingActive = useRef(false);
  const pickSeedRef = useRef<BoundingBox | null>(null);
  const trackLabelRef = useRef<string>("");
  const trackInFlight = useRef(false);
  // Bumped on every Stop / camera switch so in-flight requests can be discarded.
  const runGen = useRef(0);
  const latestFrame = useRef<AnalyzedFrame | null>(null);
  const [analysisFrame, setAnalysisFrame] = useState<AnalyzedFrame | null>(null);
  const onFrameAvailable = useCallback((imageUri: string) => {
    if (selected) latestFrame.current = { cameraId: selected.id, imageUri, capturedAt: Date.now() };
  }, [selected]);

  useEffect(() => {
    pickSeedRef.current = pickSeed;
  }, [pickSeed]);

  useEffect(() => {
    getHealth().then(setHealth).catch(() => {});
    getCameras()
      .then((cs) => {
        setCameras(cs);
        const def = pickDefaultCamera(cs);
        if (def) setSelected(def);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const judgeCams = judgePickCameras(cameras);
  const primaryOn = health?.providers.primary.enabled;
  const primaryLabel = health?.providers.primary.label ?? "NVIDIA NIM";


  const appendLog = useCallback((lines: string[]) => {
    if (!lines.length) return;
    const ts = new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    // Collapse consecutive lines of the same kind (digits/timestamp ignored) so
    // the live "Tracking …" line updates in place instead of flooding the log.
    const kind = (s: string) =>
      s
        .replace(/^\[[^\]]*\]\s*/, "")
        .replace(/\d+/g, "#")
        .trim()
        .toLowerCase();
    setActivityLog((prev) => {
      const out = [...prev];
      for (const l of lines) {
        const last = out[out.length - 1];
        if (last && kind(last) === kind(l)) continue;
        out.push(`[${ts}] ${l}`);
      }
      return out.slice(-120);
    });
  }, []);

  const applyResult = useCallback(
    (res: WatchResponse) => {
      setResult(res);
      setHistory(prev => [...prev, res].slice(-60));
      appendLog(res.log ?? []);
    },
    [appendLog]
  );

  const extendTrail = useCallback((res: WatchResponse) => {
    const add: string[] = [];
    if (res.active_camera_id) add.push(res.active_camera_id);
    for (const s of res.sightings ?? []) {
      if (s.detected && s.confidence >= 0.48) add.push(s.camera_id);
    }
    if (!add.length) return;
    setTrailIds((prev) => {
      const next = [...prev];
      for (const id of add) {
        if (!next.includes(id)) next.push(id);
      }
      return next;
    });
  }, []);

  const resetTrack = useCallback(() => {
    runGen.current += 1;
    trackInFlight.current = false;
    setPickSeed(null);
    pickSeedRef.current = null;
    trackLabelRef.current = "";
    setFollowingPick(false);
    trackingActive.current = false;
    setTracking(false);
    setLoading(false);
    setTrackStatus("idle");
    setHandoffNotice(null);
    setTrailIds([]);
  }, []);

  const selectCamera = useCallback(
    (c: Camera) => {
      resetTrack();
      setActivityLog([]);
      setHistory([]);
      setResult(null);
      setAnalysisFrame(null);
      latestFrame.current = null;
      setSelected(c);
    },
    [resetTrack]
  );

  /** Switch feed without dropping an active track (nearby strip / handoff). */
  const viewCamera = useCallback(
    (c: Camera) => {
      if (followingPick) {
        runGen.current += 1;
        trackInFlight.current = false;
        setLoading(false);
        setTracking(false);
        setPickSeed(null);
        pickSeedRef.current = null;
        setSelected(c);
        setAnalysisFrame(null);
        latestFrame.current = null;
      } else {
        selectCamera(c);
      }
    },
    [followingPick, selectCamera]
  );

  const applyHandoff = useCallback(
    (res: WatchResponse) => {
      const label = visionTrackLabel(res.vision);
      if (label && res.vision?.detected) {
        trackLabelRef.current = label;
        setDescription(label);
      }
      if (followEnabled && res.handoff) {
        const next = cameras.find((cam) => cam.id === res.handoff!.camera_id);
        if (next && next.id !== selected?.id) {
          setHandoffNotice(res.handoff.reason);
          // Carry the matched box onto the new camera so we keep tracking it.
          const box = res.sightings?.find(s => s.camera_id === next.id && s.detected)?.bounding_box ?? (res.active_camera_id === next.id ? res.vision?.bounding_box ?? null : null);
          setPickSeed(box);
          pickSeedRef.current = box;
          setSelected(next);
          setTrackStatus("tracking");
        }
      }
    },
    [cameras, selected, followEnabled]
  );

  const payloadBase = useCallback(
    () => ({
      camera_id: selected!.id,
      object_description:
        trackLabelRef.current || description || "vehicle or person",
      mode,
      bounding_box: pickSeedRef.current ?? undefined,
    }),
    [selected, description, mode]
  );

  const applyVision = useCallback((res: WatchResponse) => {
    if (res.vision?.bounding_box && res.vision.detected) {
      setPickSeed(res.vision.bounding_box);
      pickSeedRef.current = res.vision.bounding_box;
    }
    const st = res.status;
    if (st === "tracking") setTrackStatus("tracking");
    else if (st === "searching") setTrackStatus("searching");
    else if (st === "lost") setTrackStatus("lost");
    else if (res.vision?.detected) setTrackStatus("tracking");
    const label = visionTrackLabel(res.vision);
    if (label && res.vision?.detected) trackLabelRef.current = label;
  }, []);

  const runTrack = useCallback(
    async (imageUri?: string | null) => {
      if (!selected || trackInFlight.current) return;
      if (!followingPick && !trackingActive.current) return;
      const gen = runGen.current;
      const frame = imageUri ? { cameraId: selected.id, imageUri, capturedAt: latestFrame.current?.capturedAt ?? Date.now() } : null;
      trackInFlight.current = true;
      setTracking(true);
      try {
        const res = await track({
          ...payloadBase(),
          image_data_uri: imageUri ?? undefined,
        });
        if (gen !== runGen.current) return;
        applyResult(res);
        setAnalysisFrame(frame && res.active_camera_id === frame.cameraId ? frame : null);
        trackingActive.current = true;
        applyVision(res);
        extendTrail(res);
        setFollowingPick(true);
        applyHandoff(res);
      } catch {
        if (gen === runGen.current) setTrackStatus("lost");
      } finally {
        if (gen === runGen.current) {
          trackInFlight.current = false;
          setTracking(false);
        }
      }
    },
    [selected, payloadBase, applyHandoff, applyVision, extendTrail, followingPick, applyResult]
  );

  const runWatch = useCallback(async () => {
    if (!selected) return;
    const gen = runGen.current;
    const frame = latestFrame.current?.cameraId === selected.id ? latestFrame.current : null;
    setLoading(true);
    setError(null);
    try {
      const res = await watch({ ...payloadBase(), image_data_uri: frame?.imageUri, fast: false });
      if (gen !== runGen.current) return;
      applyResult(res);
      setAnalysisFrame(frame && res.active_camera_id === frame.cameraId ? frame : null);
      trackingActive.current = true;
      applyVision(res);
      if (res.vision?.detected && res.vision.object_label) {
        setDescription(visionTrackLabel(res.vision));
      }
      applyHandoff(res);
    } catch (e) {
      if (gen === runGen.current) setError(String(e));
    } finally {
      if (gen === runGen.current) setLoading(false);
    }
  }, [selected, payloadBase, applyHandoff, applyVision, applyResult]);

  const onPickObject = useCallback(
    async (box: BoundingBox, imageUri: string | null, capturedAt?: number) => {
      if (!selected || trackInFlight.current) return;
      setPickSeed(box);
      pickSeedRef.current = box;
      setFollowingPick(true);
      setTrackStatus("locking");
      setHandoffNotice(null);
      trackingActive.current = true;
      trackLabelRef.current = "";
      trackInFlight.current = true;
      const gen = runGen.current;
      const frame = imageUri ? { cameraId: selected.id, imageUri, capturedAt: capturedAt ?? Date.now() } : null;
      setLoading(true);
      setError(null);
      try {
        const res = await track({
          camera_id: selected.id,
          object_description: "clicked object — describe every visible detail",
          mode,
          bounding_box: box,
          image_data_uri: imageUri ?? undefined,
        });
        if (gen !== runGen.current) return;
        applyResult(res);
        setAnalysisFrame(frame && res.active_camera_id === frame.cameraId ? frame : null);
        applyVision(res);
        extendTrail(res);
        const label = visionTrackLabel(res.vision);
        if (label && res.vision?.detected) setDescription(label);
        applyHandoff(res);
      } catch (e) {
        if (gen === runGen.current) {
          setError(String(e));
          setTrackStatus("lost");
        }
      } finally {
        if (gen === runGen.current) {
          trackInFlight.current = false;
          setLoading(false);
        }
      }
    },
    [selected, mode, applyHandoff, applyVision, extendTrail, applyResult]
  );

  const onFrameTrack = useCallback(
    (imageUri: string) => {
      if (!followEnabled || !followingPick || loading) return;
      void runTrack(imageUri);
    },
    [followEnabled, followingPick, loading, runTrack]
  );

  const detectionForCamera =
    result && selected && result.active_camera_id === selected.id
      ? result
      : null;

  const sightingCount = (detectionForCamera?.sightings ?? []).filter(
    (s) => s.detected
  ).length;

  const primaryMs = result?.comparisons?.find(
    (c) => c.agent === "vision"
  )?.primary?.latency_ms;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <img src="/logo.png" alt="" className="brand-logo" width={28} height={28} />
          <div className="brand-text">
            <h1>TheWatcher</h1>
            <span className="topbar-guide">
              Pick camera → <em>click object</em> → AI tracks nearby
            </span>
          </div>
        </div>
        <div className="topbar-center">
          {judgeCams.map((c) => (
            <button
              key={c.id}
              type="button"
              className={`cam-chip${selected?.id === c.id ? " active" : ""}`}
              onClick={() => selectCamera(c)}
            >
              {c.name.split("@")[0].trim()}
            </button>
          ))}
          <CameraSearch
            cameras={cameras}
            selected={selected}
            onSelect={selectCamera}
            compact
          />
        </div>
        <div className="topbar-right">
          <StatusDot
            label={primaryLabel}
            on={primaryOn}
            detail={health?.providers.primary.model}
          />
          {health?.tracing?.weave && (
            <span className="speed-badge" title={`W&B Weave · ${health.tracing.project}`}>
              Weave
            </span>
          )}
          {primaryOn && primaryMs != null && (
            <span
              className="speed-badge"
              title={`Last vision inference on ${primaryLabel}`}
            >
              {primaryMs} ms
            </span>
          )}
          <span className="cam-count">
            {cameras.length > 5 ? `${cameras.length} cams` : "demo"}
          </span>
        </div>
      </header>

      {health && !primaryOn && (
        <div className="judge-warn">
          Set <code>NVIDIA_API_KEY</code> (or <code>COREWEAVE_BASE_URL</code>) in backend/.env
        </div>
      )}

      {handoffNotice && <div className="notice-bar">{handoffNotice}</div>}

      <main className="workspace">
        <section className="panel panel-map">
          <MapView
            cameras={cameras}
            selected={selected}
            onSelect={selectCamera}
            result={result}
            trailIds={trailIds}
          />
        </section>

        <section className="panel panel-feed">
          <div className="feed-head">
            <span className="feed-cam-name" title={selected?.name ?? ""}>
              {selected?.name ?? "No camera"}
            </span>
            {detectionForCamera && (
              <span
                className="feed-stats-inline"
                title={visionTrackLabel(detectionForCamera.vision) || undefined}
              >
                <span>Last analysis · </span>
                <b>{visionShortLabel(detectionForCamera.vision)}</b>
                {detectionForCamera.vision?.appearance && (
                  <>
                    <span className="sep">·</span>
                    <span className="feed-appearance">
                      {detectionForCamera.vision.appearance.length > 48
                        ? `${detectionForCamera.vision.appearance.slice(0, 46)}…`
                        : detectionForCamera.vision.appearance}
                    </span>
                  </>
                )}
                <span className="sep">·</span>
                {detectionForCamera.vision?.confidence != null
                  ? `${Math.round(detectionForCamera.vision.confidence * 100)}%`
                  : "—"}
                {sightingCount > 1 && (
                  <>
                    <span className="sep">·</span>
                    <span className="ok-text">{sightingCount} feeds</span>
                  </>
                )}
              </span>
            )}
          </div>

          <NearbyFeeds
            cameras={cameras}
            selected={selected}
            sightings={result?.sightings}
            scanning={followingPick && (loading || tracking)}
            onSelect={viewCamera}
          />

          <div className="feed-main">
            <div className="feed-main-head">
              <span>Main feed — click object to track</span>
              <span className="feed-cam-name" title={selected?.name ?? ""}>
                {selected?.name ?? ""}
              </span>
            </div>
            <SnapshotPanel
              camera={selected}
              result={detectionForCamera}
              pickSeed={pickSeed}
              followingPick={followingPick}
              trackStatus={trackStatus}
              onPick={onPickObject}
              onFrameTrack={onFrameTrack}
              trackBusy={loading || tracking}
              analysisFrame={analysisFrame?.cameraId === selected?.id ? analysisFrame : null}
              onFrameAvailable={onFrameAvailable}
            />
          </div>

          <footer className="feed-bar">
            <input
              type="text"
              className="feed-input"
              value={description}
              onChange={(e) => {
                setDescription(e.target.value);
                trackLabelRef.current = e.target.value;
              }}
              placeholder="Visual signature (auto from vision model)"
            />
            <label className="check-inline">
              <input
                type="checkbox"
                checked={followEnabled}
                onChange={(e) => setFollowEnabled(e.target.checked)}
              />
              Auto
            </label>
            {followingPick ? (
              <button type="button" className="btn-sm" onClick={resetTrack}>
                Stop
              </button>
            ) : (
              <button
                type="button"
                className="btn-sm btn-accent"
                onClick={() => runWatch()}
                disabled={loading || !selected}
              >
                Scan
              </button>
            )}
            {error && <span className="feed-err">{error}</span>}
          </footer>
        </section>

        <section className="panel panel-output">
          <header className="panel-head panel-head-tabs">
            <h2>Control room</h2>
            <div className="tab-group">
              <button type="button" className={tab === "brief" ? "active" : ""} onClick={() => setTab("brief")}>Brief</button>
              <button
                type="button"
                className={tab === "log" ? "active" : ""}
                onClick={() => setTab("log")}
              >
                Agent
              </button>
              <button
                type="button"
                className={tab === "models" ? "active" : ""}
                onClick={() => setTab("models")}
              >
                CoreWeave logs
              </button>
            </div>
          </header>
          <div className="panel-content">
            {tab === "log" ? (
              <AgentChat log={activityLog} />
            ) : tab === "brief" ? (
              <IncidentBrief history={history} />
            ) : (
              <InferenceLogs />
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function StatusDot({
  label,
  on,
  detail,
}: {
  label: string;
  on?: boolean;
  detail?: string;
}) {
  return (
    <span className="status-item" title={detail}>
      <span className={`status-dot${on ? " on" : ""}`} />
      {label}
    </span>
  );
}
