import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  createCarousel,
  fetchCarousel,
  reorderCarousel,
  thumbnailUrl,
  type Post,
} from "../api/client";

/**
 * Arrange a set of photos into one Instagram post, or re-arrange one that already
 * exists.
 *
 * The order here IS the carousel's order, and the first frame is the cover — Instagram
 * crops every other frame to the cover's ratio, which is why grouping refuses a mixed
 * selection outright rather than letting the rest be mangled silently.
 *
 * Rows deliberately don't show the title: every frame in a carousel is the same
 * performer at the same show, so the titles are identical and repeating a 90-character
 * string N times is noise that also crowds the reorder controls off the row. The
 * thumbnail and the filename are what actually tell two frames apart.
 */
export default function CarouselDialog({
  posts,
  onCancel,
  onGrouped,
}: {
  posts: Post[];
  onCancel: () => void;
  onGrouped: (carouselId: string) => void;
}) {
  // Selecting a whole existing carousel means "let me rearrange this", not "group it
  // again" — which is all the grouping path could ever tell you, via an error.
  const existingId = useMemo(() => {
    const ids = new Set(posts.map((p) => p.carousel_id));
    const only = [...ids][0];
    return ids.size === 1 && only ? only : null;
  }, [posts]);

  // Reordering works on the WHOLE carousel, not the subset that happened to be ticked.
  // Renumbering a subset would leave the unselected frames on stale positions.
  const carouselQuery = useQuery({
    queryKey: ["carousel", existingId],
    queryFn: () => fetchCarousel(existingId as string),
    enabled: !!existingId,
  });

  type Row = { id: string; label: string; dims: string };

  const rows = useMemo<Row[]>(() => {
    if (existingId) {
      const frames = carouselQuery.data?.frames ?? [];
      return [...frames]
        .sort((a, b) => a.position - b.position)
        .map((f) => ({
          id: f.post_id,
          label: f.original_filename || f.post_id.slice(0, 8),
          dims: f.width && f.height ? `${f.width}×${f.height}` : "",
        }));
    }
    return posts.map((p) => ({
      id: p.id,
      label: p.original_filename || p.id.slice(0, 8),
      dims: p.width && p.height ? `${p.width}×${p.height}` : "",
    }));
  }, [existingId, carouselQuery.data, posts]);

  const [order, setOrder] = useState<string[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [checking, setChecking] = useState(!existingId);

  // Seed (and re-seed) once the rows are known — in reorder mode they arrive async.
  useEffect(() => {
    setOrder((prev) => {
      const ids = rows.map((r) => r.id);
      const same = prev.length === ids.length && prev.every((x) => ids.includes(x));
      return same ? prev : ids;
    });
  }, [rows]);

  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const frames = order.map((id) => byId.get(id)).filter(Boolean) as Row[];
  const extra = existingId ? rows.length - posts.length : 0;

  // Dry-run on every rearrangement: the cover decides the ratio everything else is
  // cropped to, so "is this legal" can change when you promote a different frame.
  // An existing carousel was already validated when it was made.
  useEffect(() => {
    if (existingId || order.length === 0) return;
    let cancelled = false;
    setChecking(true);
    createCarousel(order, order[0], { dryRun: true })
      .then((r) => { if (!cancelled) setErrors(r.errors); })
      .catch((e) => { if (!cancelled) setErrors([String(e)]); })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [order, existingId]);

  const save = useMutation({
    mutationFn: async () => {
      if (existingId) {
        await reorderCarousel(existingId, order);
        return { carousel_id: existingId, errors: [] as string[] };
      }
      return createCarousel(order, order[0]);
    },
    onSuccess: (r) => {
      if (r.errors.length) setErrors(r.errors);
      else if (r.carousel_id) onGrouped(r.carousel_id);
    },
    onError: (e) => setErrors([String(e)]),
  });

  const move = (id: string, delta: number) => {
    setOrder((prev) => {
      const i = prev.indexOf(id);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  };

  // Promoting a frame to cover is most of what reordering is for — it sets the ratio and
  // it's the thumbnail people see — and by arrow alone it costs one click per position.
  const makeCover = (id: string) =>
    setOrder((prev) => [id, ...prev.filter((x) => x !== id)]);

  const blocked = errors.length > 0 || checking;
  const verb = existingId ? "Reorder carousel" : "Group as carousel";

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "grid", placeItems: "center", zIndex: 50, padding: 20,
      }}
      onClick={onCancel}
    >
      <div
        className="fp-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(560px, 100%)", maxHeight: "90vh",
          display: "flex", flexDirection: "column", padding: 18, gap: 12,
        }}
      >
        <div style={{ flex: "0 0 auto" }}>
          <h2 style={{ margin: "0 0 4px", fontSize: 15 }}>
            {verb} ({frames.length})
          </h2>
          <p style={{ margin: 0, fontSize: 11.5, color: "var(--text-dim)", lineHeight: 1.5 }}>
            One Instagram post; the cover sets the ratio. Bluesky and Pixelfed take four
            each, so a bigger set threads there. Flickr still gets every photo separately.
          </p>
        </div>

        {extra > 0 && (
          <div style={{ fontSize: 11, color: "var(--text-dim)", flex: "0 0 auto" }}>
            Showing all {rows.length} frames — {extra} more than you selected, because a
            carousel is reordered as a whole.
          </div>
        )}

        {errors.length > 0 && (
          <div
            className="fp-banner-error"
            style={{ padding: "8px 10px", fontSize: 11.5, flex: "0 0 auto" }}
          >
            {errors.map((e) => <div key={e} style={{ lineHeight: 1.5 }}>{e}</div>)}
          </div>
        )}

        {/* The only part allowed to scroll, so the header and the buttons stay put. */}
        <div style={{ display: "grid", gap: 5, overflowY: "auto", minHeight: 0, flex: 1 }}>
          {frames.map((p, i) => (
            <div
              key={p.id}
              style={{
                display: "flex", alignItems: "center", gap: 8, padding: 5,
                borderRadius: 6, background: "var(--surface)",
                border: i === 0 ? "1px solid var(--teal)" : "0.5px solid var(--border)",
              }}
            >
              <img
                src={thumbnailUrl(p.id)}
                alt=""
                style={{
                  width: 30, height: 38, objectFit: "cover", borderRadius: 3,
                  display: "block", flex: "0 0 auto",
                }}
              />
              <div style={{ flex: 1, minWidth: 0, lineHeight: 1.35 }}>
                <div style={{ fontSize: 11.5, color: i === 0 ? "var(--teal)" : "var(--text)" }}>
                  {i === 0 ? "Cover" : `Slide ${i + 1}`}
                  <span style={{ color: "var(--text-fade)" }}>
                    {p.dims ? ` · ${p.dims}` : ""}
                  </span>
                </div>
                <div
                  style={{
                    fontSize: 10, color: "var(--text-fade)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}
                >
                  {p.label}
                </div>
              </div>
              {/* flex:0 0 auto so a long filename can never push these off the row. */}
              <button
                className="fp-btn-ghost"
                onClick={() => makeCover(p.id)}
                disabled={i === 0}
                title="Make this the cover"
                style={{ padding: "2px 7px", fontSize: 10.5, flex: "0 0 auto" }}
              >
                Cover
              </button>
              <button
                className="fp-btn-ghost"
                onClick={() => move(p.id, -1)}
                disabled={i === 0}
                title="Move earlier"
                style={{ padding: "2px 7px", fontSize: 11, flex: "0 0 auto" }}
              >
                ↑
              </button>
              <button
                className="fp-btn-ghost"
                onClick={() => move(p.id, 1)}
                disabled={i === frames.length - 1}
                title="Move later"
                style={{ padding: "2px 7px", fontSize: 11, flex: "0 0 auto" }}
              >
                ↓
              </button>
            </div>
          ))}
        </div>

        <div
          style={{
            display: "flex", gap: 10, justifyContent: "flex-end", alignItems: "center",
            flex: "0 0 auto",
          }}
        >
          {checking && (
            <span style={{ fontSize: 11, color: "var(--text-fade)", marginRight: "auto" }}>
              Checking…
            </span>
          )}
          <button className="fp-btn-ghost" onClick={onCancel}>Cancel</button>
          <button
            className="fp-btn"
            onClick={() => save.mutate()}
            disabled={blocked || save.isPending}
          >
            {save.isPending
              ? (existingId ? "Saving…" : "Grouping…")
              : (existingId ? "Save order" : "Group")}
          </button>
        </div>
      </div>
    </div>
  );
}
