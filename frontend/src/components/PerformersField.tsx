// Performer-tagging chip field — type-ahead matches existing performers, lets you create
// new ones inline. Used in MetadataEditor (per-post tagging) and Bulk Edit. The handle is
// optional at create time and can be filled in later from Settings → Performers.

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createPerformer,
  listPerformers,
  type Performer,
} from "../api/client";

type Props = {
  /** Currently-tagged performers, in insertion order. */
  selected: Performer[];
  /** Called when the selection changes (add, remove, reorder). */
  onChange: (next: Performer[]) => void;
  /** Optional label shown above the field. Default 'Performers'. */
  label?: string;
};

export default function PerformersField({ selected, onChange, label = "Performers" }: Props) {
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const [creatingName, setCreatingName] = useState<string | null>(null);
  const [createHandle, setCreateHandle] = useState("");
  const [createErr, setCreateErr] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Search results — debounced by react-query's natural cache + a 200ms key.
  const trimmed = query.trim();
  const { data: matches = [] } = useQuery({
    queryKey: ["performers", trimmed],
    queryFn: () => listPerformers(trimmed || undefined),
    staleTime: 30_000,
  });

  // Filter out already-selected performers from the dropdown.
  const selectedIds = useMemo(() => new Set(selected.map((p) => p.id)), [selected]);
  const filtered = useMemo(
    () => matches.filter((p) => !selectedIds.has(p.id)),
    [matches, selectedIds],
  );

  // Decide whether to show a "+ Create" option at the bottom of the dropdown.
  const showCreate =
    trimmed.length > 0 &&
    !filtered.some((p) => p.display_name.toLowerCase() === trimmed.toLowerCase()) &&
    !selected.some((p) => p.display_name.toLowerCase() === trimmed.toLowerCase());

  const totalOptions = filtered.length + (showCreate ? 1 : 0);

  // Close the dropdown when the user clicks outside.
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

  function add(p: Performer) {
    onChange([...selected, p]);
    setQuery("");
    setHighlight(0);
    inputRef.current?.focus();
  }

  function remove(id: string) {
    onChange(selected.filter((p) => p.id !== id));
    inputRef.current?.focus();
  }

  const createMutation = useMutation({
    mutationFn: ({ name, handle }: { name: string; handle: string }) =>
      createPerformer(name, handle.trim() || null),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["performers"] });
      add(p);
      setCreatingName(null);
      setCreateHandle("");
      setCreateErr(null);
    },
    onError: (e) => {
      setCreateErr(e instanceof ApiError ? e.message : "Couldn't create performer");
    },
  });

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) {
      setOpen(true);
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, Math.max(0, totalOptions - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlight < filtered.length) {
        add(filtered[highlight]);
      } else if (showCreate) {
        setCreatingName(trimmed);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      setCreatingName(null);
    } else if (e.key === "Backspace" && query === "" && selected.length > 0) {
      // Backspace on empty input removes the last chip — standard chip-field behavior.
      remove(selected[selected.length - 1].id);
    }
  }

  return (
    <div ref={wrapRef} style={{ position: "relative", display: "grid", gap: 6 }}>
      <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
        {label}
        {selected.length > 0 && (
          <span style={{ marginLeft: 6, color: "var(--text-fade)", fontWeight: 400 }}>
            · {selected.length} tagged
          </span>
        )}
      </span>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          padding: 6,
          background: "var(--bg)",
          border: "0.5px solid var(--border)",
          borderRadius: 8,
          minHeight: 36,
        }}
        onClick={() => inputRef.current?.focus()}
      >
        {selected.map((p) => (
          <span
            key={p.id}
            title={p.instagram_handle ? `@${p.instagram_handle}` : "no IG handle"}
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
            }}
          >
            {p.display_name}
            {p.instagram_handle && (
              <span style={{ opacity: 0.7, fontSize: 11 }}>@{p.instagram_handle}</span>
            )}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                remove(p.id);
              }}
              aria-label={`Remove ${p.display_name}`}
              style={{
                background: "transparent",
                border: 0,
                color: "inherit",
                cursor: "pointer",
                padding: "0 4px",
                fontSize: 14,
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </span>
        ))}
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
          placeholder={selected.length === 0 ? "Type to search or create a performer…" : ""}
          style={{
            flex: 1,
            minWidth: 160,
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

      {open && !creatingName && (filtered.length > 0 || showCreate) && (
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
          {filtered.map((p, i) => (
            <button
              key={p.id}
              type="button"
              onClick={() => add(p)}
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
              <span style={{ fontSize: 13 }}>{p.display_name}</span>
              <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {p.instagram_handle ? `@${p.instagram_handle}` : "no IG handle"}
                {p.usage_count > 0 && ` · ${p.usage_count} posts`}
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
              + Create new performer "{trimmed}"
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
            New performer: <strong>{creatingName}</strong>
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
              {createMutation.isPending ? "Creating…" : "Create + tag"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
