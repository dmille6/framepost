// Single-select venue picker — type-ahead matches existing venues, lets you create
// new ones inline (with optional IG handle). Parallel to PerformersField but for a
// single value (a photo is only ever at one venue).

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createVenue,
  listVenues,
  type Venue,
} from "../api/client";

type Props = {
  /** Currently-selected venue (or null). */
  selected: Venue | null;
  /** Called when the selection changes (pick, create, or clear). */
  onChange: (next: Venue | null) => void;
  label?: string;
};

export default function VenueField({ selected, onChange, label = "Venue" }: Props) {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [creatingName, setCreatingName] = useState<string | null>(null);
  const [createHandle, setCreateHandle] = useState("");
  const [createErr, setCreateErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const trimmed = query.trim();
  const { data: matches = [] } = useQuery({
    queryKey: ["venues", trimmed],
    queryFn: () => listVenues(trimmed || undefined),
    staleTime: 30_000,
  });

  // Don't show the currently-selected venue in the dropdown.
  const filtered = useMemo(
    () => matches.filter((v) => v.id !== selected?.id),
    [matches, selected?.id],
  );

  const showCreate =
    trimmed.length > 0 &&
    !filtered.some((v) => v.display_name.toLowerCase() === trimmed.toLowerCase()) &&
    selected?.display_name.toLowerCase() !== trimmed.toLowerCase();

  const totalOptions = filtered.length + (showCreate ? 1 : 0);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
        setCreatingName(null);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function pick(v: Venue) {
    onChange(v);
    setQuery("");
    setOpen(false);
    setHighlight(0);
  }

  function clear() {
    onChange(null);
    inputRef.current?.focus();
  }

  const createMutation = useMutation({
    mutationFn: ({ name, handle }: { name: string; handle: string }) =>
      createVenue(name, handle.trim() || null),
    onSuccess: (v) => {
      qc.invalidateQueries({ queryKey: ["venues"] });
      pick(v);
      setCreatingName(null);
      setCreateHandle("");
      setCreateErr(null);
    },
    onError: (e) => {
      setCreateErr(e instanceof ApiError ? e.message : "Couldn't create venue");
    },
  });

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) setOpen(true);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, Math.max(0, totalOptions - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlight < filtered.length) pick(filtered[highlight]);
      else if (showCreate) setCreatingName(trimmed);
    } else if (e.key === "Escape") {
      setOpen(false);
      setCreatingName(null);
    }
  }

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "grid", gap: 6 }}>
      <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
        {label}
      </span>

      {selected ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 10px",
            background: "var(--bg)",
            border: "0.5px solid var(--border)",
            borderRadius: 8,
            minHeight: 36,
          }}
        >
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "3px 4px 3px 10px",
              fontSize: 12,
              background: "var(--teal-tint, rgba(93,202,165,0.1))",
              color: "var(--teal, #5dcaa5)",
              border: "0.5px solid rgba(93,202,165,0.25)",
              borderRadius: 999,
              flex: 1,
            }}
          >
            {selected.display_name}
            {selected.instagram_handle && (
              <span style={{ opacity: 0.7, fontSize: 11 }}>@{selected.instagram_handle}</span>
            )}
            <button
              type="button"
              onClick={clear}
              aria-label="Clear venue"
              style={{
                background: "transparent",
                border: 0,
                color: "inherit",
                cursor: "pointer",
                padding: "0 4px",
                fontSize: 14,
                lineHeight: 1,
                marginLeft: "auto",
              }}
            >
              ×
            </button>
          </span>
        </div>
      ) : (
        <div
          style={{
            padding: 6,
            background: "var(--bg)",
            border: "0.5px solid var(--border)",
            borderRadius: 8,
            minHeight: 36,
            display: "flex",
            alignItems: "center",
          }}
          onClick={() => inputRef.current?.focus()}
        >
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
              setHighlight(0);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
            placeholder="Type to search or create a venue…"
            style={{
              flex: 1,
              border: 0,
              outline: "none",
              background: "transparent",
              color: "inherit",
              fontSize: 13,
              fontFamily: "inherit",
              padding: "4px 6px",
            }}
          />
        </div>
      )}

      {!selected && open && !creatingName && (filtered.length > 0 || showCreate) && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            marginTop: 4,
            background: "var(--card)",
            border: "0.5px solid var(--border)",
            borderRadius: 8,
            boxShadow: "var(--shadow-lg)",
            maxHeight: 260,
            overflow: "auto",
            zIndex: 50,
          }}
        >
          {filtered.map((v, i) => (
            <button
              key={v.id}
              type="button"
              onClick={() => pick(v)}
              onMouseEnter={() => setHighlight(i)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
                width: "100%",
                padding: "8px 12px",
                background: i === highlight ? "var(--bg)" : "transparent",
                border: 0,
                cursor: "pointer",
                textAlign: "left",
                color: "inherit",
                font: "inherit",
              }}
            >
              <span style={{ fontSize: 13 }}>{v.display_name}</span>
              <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {v.instagram_handle ? `@${v.instagram_handle}` : "no IG handle"}
                {v.usage_count > 0 && ` · ${v.usage_count} posts`}
              </span>
            </button>
          ))}
          {showCreate && (
            <button
              type="button"
              onClick={() => setCreatingName(trimmed)}
              onMouseEnter={() => setHighlight(filtered.length)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                width: "100%",
                padding: "8px 12px",
                background: highlight === filtered.length ? "var(--bg)" : "transparent",
                border: 0,
                cursor: "pointer",
                textAlign: "left",
                color: "var(--teal, #5dcaa5)",
                font: "inherit",
                borderTop: filtered.length > 0 ? "0.5px solid var(--border)" : 0,
                fontSize: 13,
              }}
            >
              + Create new venue "{trimmed}"
            </button>
          )}
        </div>
      )}

      {creatingName && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            marginTop: 4,
            background: "var(--card)",
            border: "0.5px solid var(--border)",
            borderRadius: 8,
            boxShadow: "var(--shadow-lg)",
            padding: 12,
            zIndex: 50,
            display: "grid",
            gap: 8,
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 500 }}>
            New venue: <strong>{creatingName}</strong>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>@</span>
            <input
              autoFocus
              className="fp-input"
              value={createHandle}
              onChange={(e) => setCreateHandle(e.target.value.replace(/^@+/, ""))}
              placeholder="instagram-handle (optional)"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  createMutation.mutate({ name: creatingName, handle: createHandle });
                } else if (e.key === "Escape") {
                  setCreatingName(null);
                  setCreateErr(null);
                }
              }}
              style={{ flex: 1 }}
            />
          </div>
          {createErr && (
            <div style={{ fontSize: 11, color: "var(--danger)" }}>{createErr}</div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
            <button
              type="button"
              className="fp-btn-ghost"
              onClick={() => {
                setCreatingName(null);
                setCreateErr(null);
              }}
              style={{ padding: "5px 10px", fontSize: 12 }}
            >
              Cancel
            </button>
            <button
              type="button"
              className="fp-btn"
              disabled={createMutation.isPending}
              onClick={() => createMutation.mutate({ name: creatingName, handle: createHandle })}
              style={{ padding: "5px 10px", fontSize: 12 }}
            >
              {createMutation.isPending ? "Creating…" : "Create + select"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
