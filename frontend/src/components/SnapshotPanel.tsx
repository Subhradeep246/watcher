import { useCallback, useEffect, useRef, useState } from "react";
import type { BoundingBox, Camera, WatchResponse } from "../types";

const SNAPSHOT_INTERVAL_MS = 2000;
const PICK_SIZE = 120;

export function captureImageDataUri(img: HTMLImageElement): string | null {
  try {
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    if (!w || !h) return null;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, w, h);
    return canvas.toDataURL("image/jpeg", 0.88);
  } catch {
    return null;
  }
}

function clampPct(v: number) {
  return Math.max(0, Math.min((v / 1000) * 100, 100));
}

function clickToSeed(e: React.MouseEvent<HTMLImageElement>): BoundingBox {
  const img = e.currentTarget;
  const rect = img.getBoundingClientRect();
  const cx = ((e.clientX - rect.left) / rect.width) * 1000;
  const cy = ((e.clientY - rect.top) / rect.height) * 1000;
  const half = PICK_SIZE / 2;
  return {
    x: Math.round(Math.max(0, Math.min(1000 - PICK_SIZE, cx - half))),
    y: Math.round(Math.max(0, Math.min(1000 - PICK_SIZE, cy - half))),
    width: PICK_SIZE,
    height: PICK_SIZE,
  };
}

function bboxCaption(result: WatchResponse | null): string {
  const v = result?.vision;
  if (!v) return "";
  const raw = v.appearance || v.object_label || "";
  return raw.length > 52 ? `${raw.slice(0, 50)}…` : raw;
}

export default function SnapshotPanel({
  camera,
  result,
  pickSeed,
  trackStatus,
  onPick,
  onFrameTrack,
  trackBusy,
  followingPick,
}: {
  camera: Camera | null;
  result: WatchResponse | null;
  pickSeed: BoundingBox | null;
  followingPick: boolean;
  trackStatus: "idle" | "locking" | "tracking" | "searching" | "lost";
  onPick: (box: BoundingBox, imageUri: string | null) => void;
  onFrameTrack?: (imageUri: string, frameTick: number) => void;
  trackBusy?: boolean;
}) {
  const [tick, setTick] = useState(0);
  const [displaySrc, setDisplaySrc] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [frameSize, setFrameSize] = useState<{ w: number; h: number } | null>(
    null
  );
  const shellRef = useRef<HTMLDivElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const nextAtRef = useRef(Date.now() + SNAPSHOT_INTERVAL_MS);
  const lastAutoTrackTick = useRef(-1);

  const isLive = Boolean(camera && !camera.sample_image);
  const nextSrc = !camera
    ? null
    : camera.sample_image
      ? camera.sample_image
      : `/api/cameras/${camera.id}/snapshot?t=${tick}`;

  const fitFrame = useCallback(() => {
    const shell = shellRef.current;
    const img = imgRef.current;
    if (!shell || !img?.naturalWidth || !img.naturalHeight) return;
    const ar = img.naturalWidth / img.naturalHeight;
    const maxW = shell.clientWidth;
    const maxH = shell.clientHeight;
    if (maxW < 8 || maxH < 8) return;
    let w = maxW;
    let h = maxW / ar;
    if (h > maxH) {
      h = maxH;
      w = maxH * ar;
    }
    setFrameSize({ w: Math.floor(w), h: Math.floor(h) });
  }, []);

  useEffect(() => {
    lastAutoTrackTick.current = -1;
    setFrameSize(null);
    setDisplaySrc(null);
  }, [camera?.id]);

  useEffect(() => {
    if (!camera || camera.sample_image) return;
    nextAtRef.current = Date.now() + SNAPSHOT_INTERVAL_MS;
    const id = window.setInterval(() => {
      const now = Date.now();
      if (now >= nextAtRef.current) {
        setTick((t) => t + 1);
        while (nextAtRef.current <= now) {
          nextAtRef.current += SNAPSHOT_INTERVAL_MS;
        }
      }
    }, 200);
    return () => clearInterval(id);
  }, [camera]);

  useEffect(() => {
    if (!nextSrc || !camera) {
      setDisplaySrc(null);
      return;
    }
    if (camera.sample_image) {
      setDisplaySrc(nextSrc);
      return;
    }
    setRefreshing(true);
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      setDisplaySrc(nextSrc);
      setRefreshing(false);
    };
    img.onerror = () => { if (!cancelled) setRefreshing(false); };
    img.src = nextSrc;
    return () => { cancelled = true; };
  }, [nextSrc, camera]);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    const ro = new ResizeObserver(() => fitFrame());
    ro.observe(shell);
    return () => ro.disconnect();
  }, [fitFrame, displaySrc]);

  const tryAutoTrack = useCallback(() => {
    if (!followingPick || !onFrameTrack || trackBusy || !imgRef.current) return;
    if (!imgRef.current.complete || !imgRef.current.naturalWidth) return;
    if (isLive && !imgRef.current.src.includes(`/api/cameras/${camera?.id}/snapshot`)) return;
    if (tick === lastAutoTrackTick.current) return;
    const uri = captureImageDataUri(imgRef.current);
    if (!uri) return;
    lastAutoTrackTick.current = tick;
    onFrameTrack(uri, tick);
  }, [followingPick, onFrameTrack, trackBusy, tick, isLive, camera?.id]);

  const onImgLoad = useCallback(() => {
    setRefreshing(false);
    fitFrame();
    tryAutoTrack();
  }, [fitFrame, tryAutoTrack]);

  useEffect(() => {
    if (displaySrc && followingPick && onFrameTrack) tryAutoTrack();
  }, [displaySrc, followingPick, onFrameTrack, tryAutoTrack]);

  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLImageElement>) => {
      const img = imgRef.current;
      if (!displaySrc || !img || trackBusy) return;
      onPick(clickToSeed(e), captureImageDataUri(img));
    },
    [displaySrc, onPick, trackBusy]
  );

  if (!camera) {
    return (
      <div className="snapshot-empty">
        <p>Pick a camera on the map or search above</p>
      </div>
    );
  }

  const detBox = result?.vision?.bounding_box;
  const showDet = result?.vision?.detected !== false && detBox;
  const detPct = showDet &&
    detBox && {
      left: clampPct(detBox.x),
      top: clampPct(detBox.y),
      width: clampPct(detBox.width),
      height: clampPct(detBox.height),
    };

  const pickPct = pickSeed && {
    left: clampPct(pickSeed.x),
    top: clampPct(pickSeed.y),
    width: clampPct(pickSeed.width),
    height: clampPct(pickSeed.height),
  };

  return (
    <div className="snapshot" ref={shellRef}>
      <div
        className={`snapshot-frame pickable${trackBusy ? " busy" : ""}`}
        style={
          frameSize
            ? { width: frameSize.w, height: frameSize.h }
            : { width: "100%", height: "100%" }
        }
      >
        {displaySrc ? (
          <img
            ref={imgRef}
            src={displaySrc}
            alt={camera.name}
            onLoad={onImgLoad}
            onClick={handleClick}
            title="Click a vehicle or person to track"
          />
        ) : (
          <div className="snapshot-empty">Loading feed…</div>
        )}

        <div className="snapshot-overlay-top">
          <span className={`track-pill track-pill-${trackStatus}`}>
            {trackStatus === "idle" && "Click object to track"}
            {trackStatus === "locking" && "Locking target…"}
            {trackStatus === "tracking" && "Tracking"}
            {trackStatus === "searching" && "Searching nearby feeds…"}
            {trackStatus === "lost" && "Lost — click again"}
          </span>
          {isLive && <span className="pill pill-live">~2s</span>}
          {trackBusy && <span className="pill pill-primary">INFERENCE</span>}
        </div>

        {result?.vision && (
          <span
            className={`det-badge${
              result.vision.detected === false ? " det-miss" : ""
            }${(result.vision.confidence ?? 0) >= 0.8 ? " det-high" : ""}`}
          >
            {result.vision.detected === false
              ? "NOT FOUND"
              : `${result.vision.object_class} ${Math.round(
                  (result.vision.confidence ?? 0) * 100
                )}%`}
          </span>
        )}
        {isLive && refreshing && <span className="refresh-badge" />}

        {pickPct && (
          <div
            className="pick-box pick-active"
            style={{
              left: `${pickPct.left}%`,
              top: `${pickPct.top}%`,
              width: `${pickPct.width}%`,
              height: `${pickPct.height}%`,
            }}
          />
        )}
        {detPct && (
          <div
            className="bbox"
            style={{
              left: `${detPct.left}%`,
              top: `${detPct.top}%`,
              width: `${detPct.width}%`,
              height: `${detPct.height}%`,
            }}
          >
            <span title={result?.vision?.appearance || result?.vision?.object_label}>
              {bboxCaption(result)}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
