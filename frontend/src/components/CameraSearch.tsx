import { useEffect, useRef, useState } from "react";
import type { Camera } from "../types";
import { searchCameras } from "../utils/cameras";

export default function CameraSearch({
  cameras,
  selected,
  onSelect,
  compact,
}: {
  cameras: Camera[];
  selected: Camera | null;
  onSelect: (c: Camera) => void;
  compact?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const results = searchCameras(cameras, query);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className={`cam-search${compact ? " cam-search-compact" : ""}`} ref={wrapRef}>
      {!compact && <label htmlFor="cam-search-input">Find camera</label>}
      <input
        id="cam-search-input"
        type="search"
        placeholder={compact ? "Search cameras…" : "Search NYC cameras…"}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
      />
      {open && query.trim() && (
        <ul className="cam-search-results" role="listbox">
          {results.length === 0 && (
            <li className="cam-search-empty">No cameras match</li>
          )}
          {results.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                role="option"
                aria-selected={selected?.id === c.id}
                className={selected?.id === c.id ? "active" : ""}
                onClick={() => {
                  onSelect(c);
                  setQuery(c.name);
                  setOpen(false);
                }}
              >
                {c.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
