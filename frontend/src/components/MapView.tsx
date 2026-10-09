import { Fragment, useEffect, useMemo, useState } from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Polyline,
  CircleMarker,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import type { Camera, WatchResponse } from "../types";
import { getRoadRoute } from "../api";
import {
  buildMapPaths,
  directionLabel,
  haversineM,
  riskColor,
  roadShortName,
  routeMidpoint,
} from "../utils/mapPaths";

const GEOAPIFY_KEY = import.meta.env.VITE_GEOAPIFY_KEY as string | undefined;
// CARTO basemaps now watermark tiles "API KEY REQUIRED" without a key.
const CARTO_KEY = import.meta.env.VITE_CARTO_KEY as string | undefined;
const MAX_MARKERS = 200;

function dotIcon(color: string, size: number, ring?: boolean) {
  const ringStyle = ring
    ? "box-shadow:0 0 0 2px #1c1c1e, 0 0 0 4px " + color
    : "";
  return L.divIcon({
    className: "cam-dot-wrap",
    html: `<div class="cam-dot" style="width:${size}px;height:${size}px;background:${color};${ringStyle}"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

const ICON = {
  selected: dotIcon("#ff9f0a", 14, true),
  route: dotIcon("#ff6b35", 10, true),
  sighted: dotIcon("#32d74b", 11, true),
  trail: dotIcon("#0a84ff", 9, true),
  nearby: dotIcon("#636366", 7),
};

function Recenter({
  lat,
  lng,
  zoom,
}: {
  lat: number;
  lng: number;
  zoom: number;
}) {
  const map = useMap();
  useEffect(() => {
    map.setView([lat, lng], zoom, { animate: true });
  }, [lat, lng, zoom, map]);
  return null;
}

function ViewportMarkers({
  cameras,
  selected,
  mapPaths,
  sightedIds,
  trailIds,
  onSelect,
}: {
  cameras: Camera[];
  selected: Camera | null;
  mapPaths: ReturnType<typeof buildMapPaths>;
  sightedIds: Set<string>;
  trailIds: Set<string>;
  onSelect: (c: Camera) => void;
}) {
  const map = useMap();
  const [tick, setTick] = useState(0);

  useMapEvents({
    moveend: () => setTick((t) => t + 1),
    zoomend: () => setTick((t) => t + 1),
  });

  const endpointIds = useMemo(
    () => new Set(mapPaths.map((p) => p.endpointCamera?.id).filter(Boolean)),
    [mapPaths]
  );

  const visible = useMemo(() => {
    const bounds = map.getBounds();
    const inView = cameras.filter((c) => bounds.contains([c.lat, c.lng]));
    const inViewIds = new Set(inView.map((c) => c.id));
    const must = new Set<string>();
    if (selected) must.add(selected.id);
    endpointIds.forEach((id) => id && must.add(id));
    trailIds.forEach((id) => must.add(id));
    sightedIds.forEach((id) => must.add(id));

    const merged = [
      ...inView,
      ...cameras.filter((c) => must.has(c.id) && !inViewIds.has(c.id)),
    ];
    const uniq = new Map(merged.map((c) => [c.id, c]));
    const list = [...uniq.values()];
    if (list.length <= MAX_MARKERS) return list;
    const center = map.getCenter();
    const priority = list.filter(
      (c) =>
        c.id === selected?.id ||
        endpointIds.has(c.id) ||
        trailIds.has(c.id) ||
        sightedIds.has(c.id)
    );
    const rest = list
      .filter((c) => !priority.includes(c))
      .sort(
        (a, b) =>
          haversineM(center.lat, center.lng, a.lat, a.lng) -
          haversineM(center.lat, center.lng, b.lat, b.lng)
      )
      .slice(0, MAX_MARKERS - priority.length);
    return [...priority, ...rest];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameras, selected, endpointIds, trailIds, sightedIds, tick, map]);

  return (
    <>
      {visible.map((c) => {
        const isSelected = selected?.id === c.id;
        const isRoute = endpointIds.has(c.id);
        const isSighted = sightedIds.has(c.id) && !isSelected;
        const isTrail = trailIds.has(c.id) && !isSelected && !isSighted;
        const icon = isSelected
          ? ICON.selected
          : isSighted
            ? ICON.sighted
            : isTrail
              ? ICON.trail
              : isRoute
                ? ICON.route
                : ICON.nearby;
        return (
          <Marker
            key={c.id}
            position={[c.lat, c.lng]}
            icon={icon}
            zIndexOffset={
              isSelected ? 1000 : isSighted ? 800 : isTrail ? 700 : isRoute ? 600 : 0
            }
            eventHandlers={{ click: () => onSelect(c) }}
          >
            <Popup>
              <div className="map-popup">
                <strong>{c.name}</strong>
                {isSighted && <span className="map-popup-tag">Object seen</span>}
                {isTrail && <span className="map-popup-tag">On trail</span>}
                {isRoute && <span className="map-popup-tag">Predicted</span>}
              </div>
            </Popup>
          </Marker>
        );
      })}
    </>
  );
}

export default function MapView({
  cameras,
  selected,
  onSelect,
  result,
  trailIds = [],
}: {
  cameras: Camera[];
  selected: Camera | null;
  onSelect: (c: Camera) => void;
  result: WatchResponse | null;
  trailIds?: string[];
}) {
  const center: [number, number] = selected
    ? [selected.lat, selected.lng]
    : [40.7549, -73.984];

  const sightedIds = useMemo(() => {
    const ids = new Set<string>();
    for (const s of result?.sightings ?? []) {
      if (s.detected && s.confidence >= 0.48) ids.add(s.camera_id);
    }
    return ids;
  }, [result?.sightings]);

  const trailSet = useMemo(() => new Set(trailIds), [trailIds]);

  const mapPaths = useMemo(
    () => (result ? buildMapPaths(result, cameras) : []),
    [result, cameras]
  );

  const trailCoords = useMemo(() => {
    return trailIds
      .map((id) => {
        const c = cameras.find((cam) => cam.id === id);
        return c ? ([c.lat, c.lng] as [number, number]) : null;
      })
      .filter((x): x is [number, number] => x != null);
  }, [trailIds, cameras]);

  const [roadGeometries, setRoadGeometries] = useState<
    Record<string, [number, number][]>
  >({});
  const [trailGeometry, setTrailGeometry] = useState<[number, number][]>([]);

  useEffect(() => {
    if (!mapPaths.length) {
      setRoadGeometries({});
      return;
    }
    let cancelled = false;
    (async () => {
      const pairs = await Promise.all(
        mapPaths.map(async (p) => {
          const coords = await getRoadRoute(p.start, p.end);
          return [p.direction, coords] as const;
        })
      );
      if (!cancelled) setRoadGeometries(Object.fromEntries(pairs));
    })();
    return () => {
      cancelled = true;
    };
  }, [mapPaths]);

  useEffect(() => {
    if (trailCoords.length < 2) {
      setTrailGeometry([]);
      return;
    }
    let cancelled = false;
    (async () => {
      const merged: [number, number][] = [trailCoords[0]];
      for (let i = 1; i < trailCoords.length; i++) {
        const seg = await getRoadRoute(trailCoords[i - 1], trailCoords[i]);
        merged.push(...seg.slice(1));
      }
      if (!cancelled) setTrailGeometry(merged);
    })();
    return () => {
      cancelled = true;
    };
  }, [trailCoords]);

  const objectPos: [number, number] | null = result?.tracker
    ? [result.tracker.lat, result.tracker.lng]
    : mapPaths[0]?.start ?? null;

  const focus = objectPos ?? (selected ? [selected.lat, selected.lng] : center);
  const zoom = selected || objectPos ? 15 : 13;

  return (
    <MapContainer center={center} zoom={15} className="map">
      <TileLayer
        attribution={
          GEOAPIFY_KEY ? "&copy; OpenStreetMap" : "&copy; OpenStreetMap &copy; CARTO"
        }
        url={
          GEOAPIFY_KEY
            ? `https://maps.geoapify.com/v1/tile/osm-bright/{z}/{x}/{y}.png?apiKey=${GEOAPIFY_KEY}`
            : `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png${
                CARTO_KEY ? `?key=${CARTO_KEY}` : ""
              }`
        }
      />
      <Recenter lat={focus[0]} lng={focus[1]} zoom={zoom} />

      <div className="map-legend">
        <span>Green = matched · blue = trail</span>
      </div>

      {trailGeometry.length > 1 && (
        <Polyline
          positions={trailGeometry}
          pathOptions={{
            color: "#0a84ff",
            weight: 5,
            opacity: 0.85,
            dashArray: "6 8",
            lineCap: "round",
          }}
        />
      )}

      <ViewportMarkers
        cameras={cameras}
        selected={selected}
        mapPaths={mapPaths}
        sightedIds={sightedIds}
        trailIds={trailSet}
        onSelect={onSelect}
      />

      {objectPos && result?.tracker && (
        <CircleMarker
          center={objectPos}
          radius={9}
          pathOptions={{
            color: "#fff",
            fillColor: "#0a84ff",
            fillOpacity: 1,
            weight: 2,
          }}
        >
          <Popup>{result?.vision?.object_label ?? "Tracked object"}</Popup>
        </CircleMarker>
      )}

      {mapPaths.map((p) => {
        const pct = Math.round(p.probability * 100);
        const color = riskColor(p.risk);
        const geometry = roadGeometries[p.direction] ?? [p.start, p.end];
        const labelPos = routeMidpoint(geometry);
        const road = roadShortName(p.roadName);

        return (
          <Fragment key={p.direction}>
            <Polyline
              positions={geometry}
              pathOptions={{
                color,
                weight: 4 + p.probability * 6,
                opacity: geometry.length > 2 ? 0.9 : 0.4,
                lineCap: "round",
                lineJoin: "round",
              }}
            >
              <Popup>
                {road} · {directionLabel(p.direction)} · {pct}%
              </Popup>
            </Polyline>
            <Marker
              position={labelPos}
              icon={L.divIcon({
                className: "path-label-icon",
                html: `<div class="path-label"><span>${road}</span><strong>${pct}%</strong></div>`,
                iconSize: [64, 28],
                iconAnchor: [32, 14],
              })}
              interactive={false}
            />
          </Fragment>
        );
      })}
    </MapContainer>
  );
}
