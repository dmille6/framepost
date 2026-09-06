import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAppConfig,
  fetchFaceCenter,
  thumbnailUrl,
  updatePost,
  type Post,
} from "../api/client";
import IgCropStudio, { type CropRect, type IgFit } from "./IgCropStudio";

const MAX_ASPECT = 1.91;
const RATIOS: Record<string, number> = { "3:4": 3 / 4, "4:5": 4 / 5 };

type Entry = { fit: IgFit; rect: CropRect | null };

/**
 * Crop a whole selection to one Instagram ratio, one frame at a time.
 *
 * Built for carousels, useful before them: a set posted as a carousel is cropped to the
 * FIRST image's ratio by Meta, so the frames have to agree — and a set curated one photo
 * at a time is where a single bad auto-crop spoils the whole thing. Until sets are a
 * persisted concept this works on the current multi-selection; the component won't have
 * to change when they are.
 */
export default function IgCropFilmstrip({
  posts,
  onClose,
}: {
  posts: Post[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [idx, setIdx] = useState(0);
  const [edits, setEdits] = useState<Record<string, Entry>>(() =>
    Object.fromEntries(
      posts.map((p) => [
        p.id,
        {
          fit: (p.ig_fit ?? "crop") as IgFit,
          rect:
            p.ig_crop_x != null && p.ig_crop_y != null &&
            p.ig_crop_w != null && p.ig_crop_h != null
              ? { x: p.ig_crop_x, y: p.ig_crop_y, w: p.ig_crop_w, h: p.ig_crop_h }
              : null,
        },
      ]),
    ),
  );

  const { data: cfg } = useQuery({ queryKey: ["config"], queryFn: fetchAppConfig });
  const ratioKey = cfg?.["ig_min_ratio_support"] ?? "4:5";

  const current = posts[idx];

  // Arrow keys step the strip — this is a review flow, not a form.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // The zoom control is a range input: left/right already mean something there.
      // Without this guard, one arrow press both nudged the zoom on the frame being
      // left AND advanced the strip, quietly committing a crop nobody asked for.
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) {
        if (e.key === "Escape") onClose();
        return;
      }
      if (e.key === "ArrowRight") setIdx((i) => Math.min(posts.length - 1, i + 1));
      else if (e.key === "ArrowLeft") setIdx((i) => Math.max(0, i - 1));
      else if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [posts.length, onClose]);

  // A carousel takes its ratio from the first frame, so the set has to agree on one.
  // Panoramas can't reach the portrait floor, so they cap out and are flagged instead.
  const targets = useMemo(
    () =>
      posts.map((p) => {
        const r = p.width && p.height ? p.width / p.height : 1;
        return r > MAX_ASPECT ? MAX_ASPECT : RATIOS[ratioKey] ?? RATIOS["4:5"];
      }),
    [posts, ratioKey],
  );
  const setTarget = targets[0];
  const mismatched = targets.filter((t) => t !== setTarget).length;

  const save = useMutation({
    mutationFn: async () => {
      for (const p of posts) {
        const e = edits[p.id];
        // Only the crop fields; the backend patches what it's given.
        await updatePost(p.id, {
          ig_fit: e.fit === "crop" ? null : e.fit,
          ig_crop_x: e.rect?.x ?? null,
          ig_crop_y: e.rect?.y ?? null,
          ig_crop_w: e.rect?.w ?? null,
          ig_crop_h: e.rect?.h ?? null,
          ig_crop_ratio: e.rect ? ratioKey : null,
        });
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      onClose();
    },
  });

  const cropped = posts.filter((p) => edits[p.id]?.rect).length;

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 20 }}
      onClick={save.isPending ? undefined : onClose}
    >
      <div
        className="fp-card fp-fade"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(880px, 100%)",
          maxHeight: "94vh",
          padding: 0,
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
            gap: 12,
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>
              Crop {posts.length} photo{posts.length === 1 ? "" : "s"} for Instagram
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              Every frame goes out at the same ratio — {ratioKey} — so a carousel
              doesn&rsquo;t re-crop them. Flickr still gets the full frame.
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={save.isPending}
            aria-label="Close"
            style={{
              background: "transparent",
              border: "0.5px solid var(--border-strong)",
              borderRadius: 8,
              width: 32,
              height: 32,
              color: "var(--text-dim)",
              cursor: "pointer",
            }}
          >
            ×
          </button>
        </div>

        {mismatched > 0 && (
          <div
            style={{
              padding: "8px 20px",
              fontSize: 12,
              color: "var(--danger)",
              borderBottom: "0.5px solid var(--border)",
            }}
          >
            {mismatched} photo{mismatched === 1 ? " is" : "s are"} too wide to reach{" "}
            {ratioKey} and will cap at 1.91:1 — Instagram would crop{" "}
            {mismatched === 1 ? "it" : "them"} to match the first frame in a carousel.
          </div>
        )}

        <div style={{ padding: 20, overflow: "auto", display: "grid", gap: 14 }}>
          {current && (
            <>
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {idx + 1} of {posts.length} ·{" "}
                {current.title || current.original_filename || "(untitled)"}
              </div>
              <IgCropStudio
                key={current.id}
                postId={current.id}
                width={current.width}
                height={current.height}
                ratioKey={ratioKey}
                fit={edits[current.id]?.fit ?? "crop"}
                rect={edits[current.id]?.rect ?? null}
                offset={current.ig_crop_offset}
                onFitChange={(f) =>
                  setEdits((e) => ({ ...e, [current.id]: { ...e[current.id], fit: f } }))
                }
                onRectChange={(r) =>
                  setEdits((e) => ({ ...e, [current.id]: { ...e[current.id], rect: r } }))
                }
              />
            </>
          )}
        </div>

        {/* Filmstrip */}
        <div
          style={{
            display: "flex",
            gap: 8,
            padding: "10px 20px",
            overflowX: "auto",
            borderTop: "0.5px solid var(--border)",
          }}
        >
          {posts.map((p, i) => (
            <FilmFrame
              key={p.id}
              post={p}
              active={i === idx}
              manual={!!edits[p.id]?.rect}
              onClick={() => setIdx(i)}
            />
          ))}
        </div>

        <div
          style={{
            padding: "12px 20px",
            borderTop: "0.5px solid var(--border)",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {cropped} of {posts.length} cropped by hand · the rest use face-anchored auto
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <button className="fp-btn-ghost" onClick={onClose} disabled={save.isPending}>
              Cancel
            </button>
            <button
              className="fp-btn"
              onClick={() => save.mutate()}
              disabled={save.isPending}
            >
              {save.isPending ? "Saving…" : `Save ${posts.length} crop${posts.length === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** One thumbnail in the strip, flagged if the auto crop would lose the face. */
function FilmFrame({
  post, active, manual, onClick,
}: {
  post: Post;
  active: boolean;
  manual: boolean;
  onClick: () => void;
}) {
  const { data: face } = useQuery({
    queryKey: ["face-center", post.id],
    queryFn: () => fetchFaceCenter(post.id),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  return (
    <button
      type="button"
      onClick={onClick}
      title={post.title || post.original_filename || "(untitled)"}
      style={{
        position: "relative",
        flex: "0 0 auto",
        width: 58,
        height: 72,
        padding: 0,
        borderRadius: 6,
        overflow: "hidden",
        cursor: "pointer",
        background: "var(--bg)",
        border: active
          ? "2px solid var(--teal)"
          : "0.5px solid var(--border-strong)",
        opacity: active ? 1 : 0.72,
      }}
    >
      <img
        src={thumbnailUrl(post.id)}
        alt=""
        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
      />
      {manual && (
        <span
          title="Cropped by hand"
          style={{
            position: "absolute", right: 3, bottom: 3, width: 7, height: 7,
            borderRadius: "50%", background: "var(--teal)",
            boxShadow: "0 0 0 1px rgba(0,0,0,.5)",
          }}
        />
      )}
      {face && !face.detected && (
        <span
          title="No face detected — auto crop falls back to centre"
          style={{
            position: "absolute", left: 3, bottom: 3, fontSize: 9,
            color: "var(--text-fade)", textShadow: "0 1px 2px rgba(0,0,0,.7)",
          }}
        >
          ?
        </span>
      )}
    </button>
  );
}
