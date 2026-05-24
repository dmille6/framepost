import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createProfile,
  deleteProfile,
  fetchTrending,
  listProfiles,
  refreshTrending,
  setTrendingSeeds,
  type TagProfileInput,
  updateProfile,
} from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

const EMPTY: TagProfileInput = { name: "", tags: "", sort_order: 10 };

export default function SettingsProfiles() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({ queryKey: ["profiles"], queryFn: listProfiles });
  const [editing, setEditing] = useState<{ form: TagProfileInput; id: string | null; isDefault: boolean } | null>(null);

  const save = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: TagProfileInput }) =>
      id ? updateProfile(id, body) : createProfile(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["profiles"] });
      setEditing(null);
    },
  });

  const del = useMutation({
    mutationFn: deleteProfile,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 800 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Tag profiles</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            Reusable tag bundles. The global default is always applied. Other profiles stack:
            tags merge and de-duplicate.
          </div>
        </div>
        <button
          className="fp-btn"
          onClick={() => setEditing({ form: { ...EMPTY }, id: null, isDefault: false })}
        >
          Add profile
        </button>
      </div>

      {isLoading ? (
        <SkeletonRows count={3} />
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {data.map((p) => (
            <div
              key={p.id}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto",
                gap: 12,
                padding: "12px 14px",
                background: "var(--bg)",
                border: "0.5px solid var(--border)",
                borderRadius: 8,
              }}
            >
              <div style={{ display: "grid", gap: 4 }}>
                <div style={{ fontSize: 14, fontWeight: 500 }}>
                  {p.name}
                  {p.is_default && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: "var(--teal)" }}>
                      always applied
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  {p.tags ? p.tags : <span style={{ color: "var(--text-fade)" }}>No tags yet</span>}
                </div>
              </div>
              <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <button
                  className="fp-link"
                  onClick={() =>
                    setEditing({
                      form: { name: p.name, tags: p.tags, sort_order: p.sort_order },
                      id: p.id,
                      isDefault: p.is_default,
                    })
                  }
                >
                  Edit
                </button>
                {!p.is_default && (
                  <button
                    className="fp-link"
                    style={{ color: "var(--danger)" }}
                    onClick={() => {
                      if (confirm(`Delete "${p.name}"?`)) del.mutate(p.id);
                    }}
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <ProfileForm
          initial={editing.form}
          isEdit={editing.id !== null}
          isDefault={editing.isDefault}
          onCancel={() => setEditing(null)}
          onSubmit={(body) => save.mutate({ id: editing.id, body })}
          submitting={save.isPending}
        />
      )}

      <TrendingSection />
    </div>
  );
}

function TrendingSection() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["trending"], queryFn: fetchTrending });
  const [seedsInput, setSeedsInput] = useState("");

  useEffect(() => {
    if (data) setSeedsInput(data.seeds.join(", "));
  }, [data?.seeds.join(",")]);

  const saveSeeds = useMutation({
    mutationFn: (seeds: string[]) => setTrendingSeeds(seeds),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trending"] }),
  });

  const refresh = useMutation({
    mutationFn: refreshTrending,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trending"] }),
  });

  const lastIso = data?.last_refresh;
  const tagCount = data?.tags.length ?? 0;

  return (
    <div
      style={{
        marginTop: 24,
        paddingTop: 24,
        borderTop: "0.5px solid var(--border)",
        display: "grid",
        gap: 12,
      }}
    >
      <div>
        <div style={{ fontSize: 14, fontWeight: 500 }}>Trending tags from Flickr</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Periodically pulls related tags + tags from popular Flickr photos for each seed.
          Surfaced as a fourth suggestion panel in the metadata editor. Auto-refreshes every
          Monday at 02:00 UTC, or trigger manually.
        </div>
      </div>
      <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
        Seed tags <span style={{ color: "var(--text-fade)", marginLeft: 8 }}>Comma-separated</span>
        <input
          className="fp-input"
          value={seedsInput}
          onChange={(e) => setSeedsInput(e.target.value)}
          placeholder="e.g. burlesque, cabaret, new orleans nightlife, stage performance"
        />
      </label>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          className="fp-btn-ghost"
          onClick={() =>
            saveSeeds.mutate(
              seedsInput.split(",").map((t) => t.trim()).filter(Boolean),
            )
          }
          disabled={saveSeeds.isPending}
        >
          Save seeds
        </button>
        <button
          className="fp-btn"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || (data?.seeds.length ?? 0) === 0}
        >
          {refresh.isPending ? "Refreshing…" : "Refresh now"}
        </button>
        <div style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-fade)" }}>
          {lastIso ? `Last refresh: ${new Date(lastIso).toLocaleString()}` : "never refreshed"}
          {tagCount > 0 && ` · ${tagCount} tags cached`}
        </div>
      </div>
      {refresh.error && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          {refresh.error instanceof ApiError ? refresh.error.message : "refresh failed"}
        </div>
      )}
      {refresh.data && (
        <div style={{ color: "var(--teal)", fontSize: 13 }}>
          Refreshed {refresh.data.refreshed} rows across {refresh.data.seeds.length} seed(s).
        </div>
      )}
    </div>
  );
}

function ProfileForm({
  initial,
  isEdit,
  isDefault,
  onCancel,
  onSubmit,
  submitting,
}: {
  initial: TagProfileInput;
  isEdit: boolean;
  isDefault: boolean;
  onCancel: () => void;
  onSubmit: (body: TagProfileInput) => void;
  submitting: boolean;
}) {
  const [form, setForm] = useState<TagProfileInput>(initial);

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit(form);
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "grid",
        placeItems: "center",
        zIndex: 100,
      }}
      onClick={onCancel}
    >
      <form
        className="fp-card"
        style={{ width: 520, display: "grid", gap: 12 }}
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <div style={{ fontSize: 16, fontWeight: 500 }}>
          {isEdit ? (isDefault ? "Edit global default" : "Edit profile") : "Add profile"}
        </div>
        <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
          Name
          <input
            className="fp-input"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            disabled={isDefault}
          />
        </label>
        <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
          Tags <span style={{ color: "var(--text-fade)", marginLeft: 8 }}>Comma-separated</span>
          <textarea
            className="fp-textarea"
            rows={3}
            value={form.tags}
            onChange={(e) => setForm({ ...form, tags: e.target.value })}
            placeholder="performer name, city, genre…"
          />
        </label>
        {!isDefault && (
          <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
            Sort order <span style={{ color: "var(--text-fade)", marginLeft: 8 }}>lower = listed first</span>
            <input
              className="fp-input"
              type="number"
              value={form.sort_order ?? 0}
              onChange={(e) => setForm({ ...form, sort_order: Number(e.target.value) })}
            />
          </label>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
          <button type="button" className="fp-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="fp-btn" disabled={submitting || !form.name.trim()}>
            {submitting ? "Saving…" : isEdit ? "Save" : "Add"}
          </button>
        </div>
      </form>
    </div>
  );
}
