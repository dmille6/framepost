import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createVenue,
  deleteVenue,
  listVenues,
  type Venue,
  updateVenue,
} from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

export default function SettingsVenues() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const { data: venues = [], isLoading } = useQuery({
    queryKey: ["venues", q.trim()],
    queryFn: () => listVenues(q.trim() || undefined),
  });

  const [adding, setAdding] = useState(false);

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 800 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Venues</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            Places you shoot at. Tagging a venue on a post auto-inserts its IG handle
            in the caption and a hashtag of the venue name — venues regularly repost
            performance photos from their nights, so this is a real audience-growth lever.
            Manage the list here; create new venues directly from the editor too.
          </div>
        </div>
        <button className="fp-btn" onClick={() => setAdding(true)} disabled={adding}>
          Add venue
        </button>
      </div>

      <input
        className="fp-input"
        placeholder="Search by name or handle"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />

      {adding && (
        <AddRow
          onCancel={() => setAdding(false)}
          onCreated={() => {
            setAdding(false);
            void qc.invalidateQueries({ queryKey: ["venues"] });
          }}
        />
      )}

      {isLoading ? (
        <SkeletonRows count={4} />
      ) : venues.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--text-dim)", padding: 12 }}>
          {q.trim()
            ? "No venues match that search."
            : "No venues yet. Add one above, or set one on any post — the picker there creates them too."}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {venues.map((v) => (
            <VenueRow key={v.id} venue={v} />
          ))}
        </div>
      )}
    </div>
  );
}

function AddRow({ onCancel, onCreated }: { onCancel: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => createVenue(name.trim(), handle.trim() || null),
    onSuccess: () => onCreated(),
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't create venue"),
  });

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr auto",
        gap: 8,
        padding: 12,
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        alignItems: "center",
      }}
    >
      <input
        autoFocus
        className="fp-input"
        placeholder="Display name (e.g. Hi-Ho Lounge)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        className="fp-input"
        placeholder="instagram-handle (optional)"
        value={handle}
        onChange={(e) => setHandle(e.target.value.replace(/^@+/, ""))}
        onKeyDown={(e) => {
          if (e.key === "Enter" && name.trim()) create.mutate();
          else if (e.key === "Escape") onCancel();
        }}
      />
      <div style={{ display: "flex", gap: 6 }}>
        <button className="fp-btn-ghost" onClick={onCancel} style={{ padding: "6px 10px", fontSize: 12 }}>
          Cancel
        </button>
        <button
          className="fp-btn"
          onClick={() => create.mutate()}
          disabled={!name.trim() || create.isPending}
          style={{ padding: "6px 12px", fontSize: 12 }}
        >
          {create.isPending ? "Saving…" : "Save"}
        </button>
      </div>
      {err && (
        <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--danger)" }}>{err}</div>
      )}
    </div>
  );
}

function VenueRow({ venue }: { venue: Venue }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(venue.display_name);
  const [handle, setHandle] = useState(venue.instagram_handle ?? "");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setName(venue.display_name);
    setHandle(venue.instagram_handle ?? "");
  }, [venue.display_name, venue.instagram_handle]);

  const save = useMutation({
    mutationFn: () =>
      updateVenue(venue.id, {
        display_name: name.trim() !== venue.display_name ? name.trim() : undefined,
        instagram_handle:
          (handle.trim() || null) !== (venue.instagram_handle ?? null)
            ? handle.trim() || null
            : undefined,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["venues"] });
      setEditing(false);
      setErr(null);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  const del = useMutation({
    mutationFn: () => deleteVenue(venue.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["venues"] }),
  });

  function handleDelete() {
    const msg =
      venue.usage_count > 0
        ? `Delete "${venue.display_name}"? It's set on ${venue.usage_count} post${venue.usage_count === 1 ? "" : "s"} — those posts will keep all their other data but lose the venue.`
        : `Delete "${venue.display_name}"?`;
    if (confirm(msg)) del.mutate();
  }

  if (editing) {
    return (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr auto",
          gap: 8,
          padding: 12,
          background: "var(--bg)",
          border: "0.5px solid var(--teal, #5dcaa5)",
          borderRadius: 8,
          alignItems: "center",
        }}
      >
        <input
          autoFocus
          className="fp-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="fp-input"
          value={handle}
          onChange={(e) => setHandle(e.target.value.replace(/^@+/, ""))}
          placeholder="instagram-handle"
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim()) save.mutate();
            else if (e.key === "Escape") {
              setEditing(false);
              setName(venue.display_name);
              setHandle(venue.instagram_handle ?? "");
              setErr(null);
            }
          }}
        />
        <div style={{ display: "flex", gap: 6 }}>
          <button
            className="fp-btn-ghost"
            onClick={() => {
              setEditing(false);
              setName(venue.display_name);
              setHandle(venue.instagram_handle ?? "");
              setErr(null);
            }}
            style={{ padding: "6px 10px", fontSize: 12 }}
          >
            Cancel
          </button>
          <button
            className="fp-btn"
            onClick={() => save.mutate()}
            disabled={!name.trim() || save.isPending}
            style={{ padding: "6px 12px", fontSize: 12 }}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
        {err && (
          <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--danger)" }}>{err}</div>
        )}
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 12,
        padding: "10px 14px",
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        alignItems: "center",
      }}
    >
      <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 500 }}>{venue.display_name}</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", display: "flex", gap: 10, flexWrap: "wrap" }}>
          {venue.instagram_handle ? (
            <a
              href={`https://instagram.com/${venue.instagram_handle}`}
              target="_blank"
              rel="noreferrer"
              style={{ color: "var(--text-dim)" }}
            >
              @{venue.instagram_handle} ↗
            </a>
          ) : (
            <span style={{ color: "var(--text-fade)" }}>no IG handle</span>
          )}
          <span>
            {venue.usage_count} post{venue.usage_count === 1 ? "" : "s"}
          </span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          className="fp-btn-ghost"
          onClick={() => setEditing(true)}
          style={{ padding: "6px 10px", fontSize: 12 }}
        >
          Edit
        </button>
        <button
          className="fp-btn-danger"
          onClick={handleDelete}
          disabled={del.isPending}
          style={{ padding: "6px 10px", fontSize: 12 }}
        >
          Delete
        </button>
      </div>
    </div>
  );
}
