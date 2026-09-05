import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  type FindReplaceMatch,
  type FindReplacePreview,
  findReplaceApply,
  findReplacePreview,
} from "../api/client";

const FIELDS = [
  { key: "description", label: "Description" },
  { key: "title", label: "Title" },
  { key: "tags", label: "Tags" },
];

/**
 * Fix a typo that got copied into many posts.
 *
 * Bulk edit can't do this: its Description field replaces the whole value, which would
 * flatten every selected post's description into one identical string. This rewrites
 * only the matched substring, and always makes you look at the hits before it writes.
 */
export default function FindReplaceDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [field, setField] = useState("description");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [preview, setPreview] = useState<FindReplacePreview | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const body = { find, replace, field, case_sensitive: caseSensitive };

  // Any change to the terms invalidates the preview — applying a stale match list
  // would edit posts the photographer never actually looked at.
  useEffect(() => {
    setPreview(null);
    setChecked(new Set());
    setDone(null);
  }, [find, replace, field, caseSensitive]);

  const search = useMutation({
    mutationFn: () => findReplacePreview(body),
    onSuccess: (data) => {
      setPreview(data);
      setChecked(new Set(data.matches.map((m) => m.post_id)));
      setError(null);
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "Search failed"),
  });

  const apply = useMutation({
    mutationFn: () => findReplaceApply({ ...body, post_ids: [...checked] }),
    onSuccess: (res) => {
      setDone(
        `Updated ${res.changed} post${res.changed === 1 ? "" : "s"}` +
          (res.skipped.length ? ` · ${res.skipped.length} skipped (no longer matched)` : ""),
      );
      setPreview(null);
      setChecked(new Set());
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      void qc.invalidateQueries({ queryKey: ["scheduled"] });
      void qc.invalidateQueries({ queryKey: ["schedule"] });
    },
    onError: (e: unknown) => setError(e instanceof Error ? e.message : "Replace failed"),
  });

  const busy = search.isPending || apply.isPending;
  const selectedCount = checked.size;

  const toggle = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const allChecked = useMemo(
    () => !!preview && preview.matches.length > 0 && selectedCount === preview.matches.length,
    [preview, selectedCount],
  );

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24 }}
      onClick={busy ? undefined : onClose}
    >
      <div
        className="fp-card fp-fade"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(760px, 100%)",
          maxHeight: "92vh",
          padding: 0,
          boxShadow: "var(--shadow-lg)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            padding: "14px 20px",
            borderBottom: "0.5px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Find &amp; replace
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              Searches drafts and scheduled posts. Only the matched text changes.
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            style={{
              background: "transparent",
              border: "0.5px solid var(--border-strong)",
              borderRadius: 8,
              width: 32,
              height: 32,
              color: "var(--text-dim)",
              cursor: busy ? "not-allowed" : "pointer",
              display: "grid",
              placeItems: "center",
              fontSize: 16,
            }}
          >
            ×
          </button>
        </div>

        <div style={{ padding: 20, display: "grid", gap: 14, overflow: "auto" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <label style={{ display: "grid", gap: 4, fontSize: 12, color: "var(--text-dim)" }}>
              Find
              <input
                className="fp-input"
                value={find}
                autoFocus
                placeholder="the text that's wrong"
                onChange={(e) => setFind(e.target.value)}
                disabled={busy}
              />
            </label>
            <label style={{ display: "grid", gap: 4, fontSize: 12, color: "var(--text-dim)" }}>
              Replace with
              <input
                className="fp-input"
                value={replace}
                placeholder="(leave empty to delete it)"
                onChange={(e) => setReplace(e.target.value)}
                disabled={busy}
              />
            </label>
          </div>

          <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
              <span style={{ color: "var(--text-dim)" }}>In</span>
              <select
                className="fp-input"
                value={field}
                onChange={(e) => setField(e.target.value)}
                disabled={busy}
                style={{ padding: "6px 10px" }}
              >
                {FIELDS.map((f) => (
                  <option key={f.key} value={f.key}>{f.label}</option>
                ))}
              </select>
            </label>
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={caseSensitive}
                onChange={(e) => setCaseSensitive(e.target.checked)}
                disabled={busy}
                style={{ width: 15, height: 15, cursor: "pointer" }}
              />
              Match case
            </label>
            <button
              className="fp-btn"
              onClick={() => search.mutate()}
              disabled={busy || !find.trim()}
              style={{ marginLeft: "auto" }}
            >
              {search.isPending ? "Searching" : "Find matches"}
            </button>
          </div>

          {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}
          {done && <div style={{ color: "var(--teal)", fontSize: 13 }}>{done}</div>}

          {preview && preview.matches.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-fade)" }}>
              No drafts or scheduled posts contain that text.
              {preview.published_skipped > 0 &&
                ` (${preview.published_skipped} already-published post${
                  preview.published_skipped === 1 ? "" : "s"
                } match, but those are live and aren't editable here.)`}
            </div>
          )}

          {preview && preview.matches.length > 0 && (
            <>
              <div
                style={{
                  display: "flex", alignItems: "center", gap: 10, fontSize: 13,
                  borderTop: "0.5px solid var(--border)", paddingTop: 12, flexWrap: "wrap",
                }}
              >
                <label style={{ display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={allChecked}
                    onChange={(e) =>
                      setChecked(e.target.checked
                        ? new Set(preview.matches.map((m) => m.post_id))
                        : new Set())
                    }
                    style={{ width: 15, height: 15, cursor: "pointer" }}
                  />
                  {preview.post_count} post{preview.post_count === 1 ? "" : "s"} ·{" "}
                  {preview.occurrence_count} occurrence
                  {preview.occurrence_count === 1 ? "" : "s"}
                </label>
                {preview.published_skipped > 0 && (
                  <span style={{ fontSize: 12, color: "var(--text-fade)" }}>
                    {preview.published_skipped} published post
                    {preview.published_skipped === 1 ? "" : "s"} also match — already live, not editable
                  </span>
                )}
              </div>

              <div style={{ display: "grid", gap: 8, maxHeight: "38vh", overflow: "auto" }}>
                {preview.matches.map((m) => (
                  <MatchRow
                    key={m.post_id}
                    match={m}
                    checked={checked.has(m.post_id)}
                    onToggle={() => toggle(m.post_id)}
                    disabled={busy}
                  />
                ))}
              </div>
            </>
          )}
        </div>

        <div
          style={{
            padding: "12px 20px",
            borderTop: "0.5px solid var(--border)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button className="fp-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          <button
            className="fp-btn"
            onClick={() => apply.mutate()}
            disabled={busy || !preview || selectedCount === 0}
          >
            {apply.isPending
              ? "Replacing"
              : `Replace in ${selectedCount} post${selectedCount === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </div>
  );
}

function MatchRow({
  match, checked, onToggle, disabled,
}: {
  match: FindReplaceMatch;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        padding: "8px 10px",
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        opacity: checked ? 1 : 0.55,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onToggle}
        disabled={disabled}
        style={{ width: 15, height: 15, marginTop: 3, cursor: "pointer", flexShrink: 0 }}
      />
      <div style={{ minWidth: 0, display: "grid", gap: 3 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>
          {match.title || "(untitled)"}
          {match.occurrences > 1 && (
            <span style={{ color: "var(--text-fade)", fontWeight: 400 }}>
              {" "}· {match.occurrences}×
            </span>
          )}
          {match.scheduled_at && (
            <span style={{ color: "var(--text-fade)", fontWeight: 400, fontSize: 11 }}>
              {" "}· scheduled {match.scheduled_at.slice(0, 10)}
            </span>
          )}
        </div>
        <div style={{ fontSize: 12, color: "var(--danger)", wordBreak: "break-word" }}>
          {match.before}
        </div>
        <div style={{ fontSize: 12, color: "var(--teal)", wordBreak: "break-word" }}>
          {match.after}
        </div>
      </div>
    </div>
  );
}
