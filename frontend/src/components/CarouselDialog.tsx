import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { createCarousel, thumbnailUrl, type Post } from "../api/client";

/**
 * Turn a selection into one Instagram post.
 *
 * The order here IS the carousel's order, and the first frame is the cover — Instagram
 * crops every other frame to the cover's ratio, which is why grouping refuses a mixed
 * selection outright rather than letting the rest be mangled silently.
 *
 * Validation runs server-side as the arrangement changes, so the rules live in one place
 * (services/carousel.validate) instead of being re-stated in TypeScript and drifting.
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
  const [order, setOrder] = useState<string[]>(() => posts.map((p) => p.id));
  const [errors, setErrors] = useState<string[]>([]);
  const [checking, setChecking] = useState(true);

  const byId = useMemo(() => new Map(posts.map((p) => [p.id, p])), [posts]);
  const frames = order.map((id) => byId.get(id)).filter(Boolean) as Post[];

  // Dry-run on every rearrangement: the cover decides the ratio everything else is
  // cropped to, so "is this legal" can change when you promote a different frame.
  useEffect(() => {
    let cancelled = false;
    setChecking(true);
    createCarousel(order, order[0], { dryRun: true })
      .then((r) => { if (!cancelled) setErrors(r.errors); })
      .catch((e) => { if (!cancelled) setErrors([String(e)]); })
      .finally(() => { if (!cancelled) setChecking(false); });
    return () => { cancelled = true; };
  }, [order]);

  const group = useMutation({
    mutationFn: () => createCarousel(order, order[0]),
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

  const blocked = errors.length > 0 || checking;

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
        style={{ width: "min(680px, 100%)", maxHeight: "88vh", overflow: "auto", padding: 20 }}
      >
        <h2 style={{ margin: "0 0 4px", fontSize: 16 }}>
          Group as carousel ({frames.length})
        </h2>
        <p style={{ margin: "0 0 14px", fontSize: 12, color: "var(--text-dim)", lineHeight: 1.6 }}>
          These publish as one Instagram post. The first frame is the cover, and Instagram
          crops the rest to match it.
          <br />
          Bluesky and Pixelfed take four images each, so a bigger set goes out there as a
          thread rather than losing frames.
          <br />
          Flickr still gets every photo separately.
        </p>

        {errors.length > 0 && (
          <div className="fp-banner-error" style={{ padding: "10px 12px", fontSize: 12, marginBottom: 12 }}>
            {errors.map((e) => <div key={e} style={{ lineHeight: 1.5 }}>{e}</div>)}
          </div>
        )}

        <div style={{ display: "grid", gap: 8, marginBottom: 16 }}>
          {frames.map((p, i) => (
            <div
              key={p.id}
              style={{
                display: "flex", alignItems: "center", gap: 10, padding: 8,
                borderRadius: 8, background: "var(--surface)",
                border: i === 0 ? "1px solid var(--teal)" : "0.5px solid var(--border)",
              }}
            >
              <img
                src={thumbnailUrl(p.id)}
                alt=""
                style={{ width: 42, height: 52, objectFit: "cover", borderRadius: 4, display: "block" }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {p.title || p.original_filename || "(untitled)"}
                </div>
                <div style={{ fontSize: 10.5, color: "var(--text-fade)" }}>
                  {i === 0 ? "Cover — sets the ratio" : `Slide ${i + 1}`}
                  {p.width && p.height ? ` · ${p.width}×${p.height}` : ""}
                </div>
              </div>
              <button
                className="fp-btn-ghost"
                onClick={() => move(p.id, -1)}
                disabled={i === 0}
                title="Move earlier"
                style={{ padding: "2px 8px", fontSize: 12 }}
              >
                ↑
              </button>
              <button
                className="fp-btn-ghost"
                onClick={() => move(p.id, 1)}
                disabled={i === frames.length - 1}
                title="Move later"
                style={{ padding: "2px 8px", fontSize: 12 }}
              >
                ↓
              </button>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", alignItems: "center" }}>
          {checking && (
            <span style={{ fontSize: 11, color: "var(--text-fade)", marginRight: "auto" }}>
              Checking…
            </span>
          )}
          <button className="fp-btn-ghost" onClick={onCancel}>Cancel</button>
          <button
            className="fp-btn"
            onClick={() => group.mutate()}
            disabled={blocked || group.isPending}
          >
            {group.isPending ? "Grouping…" : "Group"}
          </button>
        </div>
      </div>
    </div>
  );
}
