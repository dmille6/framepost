import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createPerformer,
  deletePerformer,
  listPerformers,
  type Performer,
  updatePerformer,
} from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

export default function SettingsPerformers() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const { data: performers = [], isLoading } = useQuery({
    queryKey: ["performers", q.trim()],
    queryFn: () => listPerformers(q.trim() || undefined),
  });

  const [adding, setAdding] = useState(false);

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 800 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Performers</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            People you photograph repeatedly. When tagged on a post, their @-mention and a
            hashtag get auto-inserted into the caption on every platform. The IG handle is
            sent verbatim everywhere — it's the universal credit even on Bluesky / Pixelfed.
          </div>
        </div>
        <button className="fp-btn" onClick={() => setAdding(true)} disabled={adding}>
          Add performer
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
            void qc.invalidateQueries({ queryKey: ["performers"] });
          }}
        />
      )}

      {isLoading ? (
        <SkeletonRows count={4} />
      ) : performers.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--text-dim)", padding: 12 }}>
          {q.trim()
            ? "No performers match that search."
            : "No performers yet. Add one above, or tag one on any post — the chip-field there creates them too."}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {performers.map((p) => (
            <PerformerRow key={p.id} performer={p} />
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
    mutationFn: () => createPerformer(name.trim(), handle.trim() || null),
    onSuccess: () => {
      onCreated();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Couldn't create performer"),
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
        placeholder="Display name (e.g. Roxie LaRouge)"
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

function PerformerRow({ performer }: { performer: Performer }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(performer.display_name);
  const [handle, setHandle] = useState(performer.instagram_handle ?? "");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setName(performer.display_name);
    setHandle(performer.instagram_handle ?? "");
  }, [performer.display_name, performer.instagram_handle]);

  const save = useMutation({
    mutationFn: () =>
      updatePerformer(performer.id, {
        display_name: name.trim() !== performer.display_name ? name.trim() : undefined,
        instagram_handle:
          (handle.trim() || null) !== (performer.instagram_handle ?? null)
            ? handle.trim() || null
            : undefined,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["performers"] });
      setEditing(false);
      setErr(null);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  const del = useMutation({
    mutationFn: () => deletePerformer(performer.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["performers"] }),
  });

  function handleDelete() {
    const msg =
      performer.usage_count > 0
        ? `Delete "${performer.display_name}"? They're tagged on ${performer.usage_count} post${performer.usage_count === 1 ? "" : "s"} — those will be untagged but the posts themselves are untouched.`
        : `Delete "${performer.display_name}"?`;
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
              setName(performer.display_name);
              setHandle(performer.instagram_handle ?? "");
              setErr(null);
            }
          }}
        />
        <div style={{ display: "flex", gap: 6 }}>
          <button
            className="fp-btn-ghost"
            onClick={() => {
              setEditing(false);
              setName(performer.display_name);
              setHandle(performer.instagram_handle ?? "");
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
        <div style={{ fontSize: 14, fontWeight: 500 }}>{performer.display_name}</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", display: "flex", gap: 10, flexWrap: "wrap" }}>
          {performer.instagram_handle ? (
            <a
              href={`https://instagram.com/${performer.instagram_handle}`}
              target="_blank"
              rel="noreferrer"
              style={{ color: "var(--text-dim)" }}
            >
              @{performer.instagram_handle} ↗
            </a>
          ) : (
            <span style={{ color: "var(--text-fade)" }}>no IG handle</span>
          )}
          <span>
            {performer.usage_count} post{performer.usage_count === 1 ? "" : "s"}
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
          title={
            performer.usage_count > 0
              ? `Will untag from ${performer.usage_count} posts`
              : "Delete this performer"
          }
        >
          Delete
        </button>
      </div>
    </div>
  );
}
