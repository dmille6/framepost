/**
 * Turn a selection of drafts into a scheduled reel, in one pass.
 *
 * Reels are the default for a group of photographs now, and the reason is a mechanism
 * rather than a measurement: a carousel is shown to people who already follow the
 * account, and on this account roughly 91% of followers never see a given post. A reel
 * is the only format Instagram pushes at people who don't follow you. Carousels also
 * show no reach advantage over single photos in this account's own numbers.
 *
 * The existing Reel tab builds from *published* history, which is the wrong end of the
 * pipeline for a fresh shoot — the whole point is to schedule the thing, not to
 * assemble one out of posts that already went out. The API never required published
 * posts; only that UI did.
 *
 * The frames also stop targeting Instagram individually. A carousel groups its members
 * so they publish as one post; a reel does not group anything, so without this the same
 * five photographs would go out as a reel AND as five separate Instagram posts. They
 * keep every other destination -- Flickr and Bluesky want the single frames, and only
 * Instagram is receiving the assembled version.
 *
 * Crops are centred 9:16 and landscape frames are excluded rather than warned about.
 * That is stricter than the Reel tab deliberately: nobody is looking at a per-frame
 * crop here, and a centred crop of a 3:2 landscape discards 62% of the width — on the
 * first reel published through the API it turned the cover into a disembodied arm and
 * a row of audience heads.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  createReel,
  getReel,
  listConnectedPlatforms,
  scheduleReel,
  thumbnailUrl,
  updatePost,
  type Post,
  type Reel,
} from "../api/client";

const ASPECT = 9 / 16;
const MAX_FRAMES = 10;
const DEFAULT_SECONDS = 15;

/** Largest centred 9:16 rectangle inside the source, in original-image pixels. */
function centreCrop(width: number, height: number) {
  if (width / height > ASPECT) {
    const w = Math.round(height * ASPECT);
    return { x: Math.round((width - w) / 2), y: 0, width: w, height };
  }
  const h = Math.round(width / ASPECT);
  return { x: 0, y: Math.round((height - h) / 2), width, height: h };
}

export function isPortrait(p: Post): boolean {
  return !!p.width && !!p.height && p.height > p.width;
}

export default function ReelFromDraftsDialog({
  posts,
  onCancel,
  onDone,
}: {
  posts: Post[];
  onCancel: () => void;
  onDone: () => void;
}) {
  const usable = useMemo(() => posts.filter(isPortrait).slice(0, MAX_FRAMES), [posts]);
  const skipped = posts.length - usable.length;

  const [seconds, setSeconds] = useState(DEFAULT_SECONDS);
  const [caption, setCaption] = useState("");
  const [when, setWhen] = useState("");
  const [reel, setReel] = useState<Reel | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!caption && usable[0]) setCaption(usable[0].title ?? "");
  }, [usable, caption]);

  // The render runs server-side in the background; poll until it lands.
  useEffect(() => {
    if (!reel || reel.status !== "pending") return;
    const t = setInterval(async () => {
      try {
        setReel(await getReel(reel.id));
      } catch {
        /* transient — the next tick retries */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [reel]);

  const { data: connected = [] } = useQuery({
    queryKey: ["platforms"],
    queryFn: listConnectedPlatforms,
  });

  /** This post's destinations as they stand, resolving the "use defaults" null. */
  function effectiveTargets(p: Post): string[] {
    // A non-null list is an explicit choice, empty included; null means "use defaults".
    if (p.target_platforms) return p.target_platforms;
    return connected.filter((c) => c.default_target).map((c) => c.platform);
  }

  const build = useMutation({
    mutationFn: () =>
      createReel({
        cover_post_id: usable[0].id,
        total_duration_seconds: seconds,
        caption,
        photos: usable.map((p, i) => ({
          post_id: p.id,
          position: i,
          crop_start: centreCrop(p.width ?? 1080, p.height ?? 1920),
          crop_end: null,
        })),
      }),
    onSuccess: async (created) => {
      // Take Instagram off each frame, so the photographs reach Instagram once — as the
      // reel — and still go everywhere else on their own.
      try {
        await Promise.all(
          usable.map((p) =>
            updatePost(p.id, {
              target_platforms: effectiveTargets(p).filter((t) => t !== "instagram"),
            }),
          ),
        );
      } catch {
        setError(
          "The reel was built, but these photos still target Instagram individually — " +
            "untick Instagram on each before they publish, or you'll post them twice.",
        );
      }
      setReel(created);
    },
    onError: (e) => setError((e as Error).message),
  });

  const queue = useMutation({
    mutationFn: () => scheduleReel(reel!.id, when),
    onSuccess: onDone,
    onError: (e) => setError((e as Error).message),
  });

  const ready = reel?.status === "ready";
  const failed = reel?.status === "failed";

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "grid", placeItems: "center", zIndex: 60, padding: 20,
      }}
      onClick={onCancel}
    >
      <div
        className="fp-card"
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(560px, 100%)", maxHeight: "85vh", overflowY: "auto", display: "grid", gap: 14 }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: 16 }}>Reel from {usable.length} photos</h2>
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
            Reels are shown to people who don't follow you; a carousel only reaches
            existing followers. Performers in any frame are invited as collaborators.
            These photos will stop posting to Instagram on their own — Instagram gets
            the reel instead. Flickr, Bluesky and the rest are unaffected.
          </div>
        </div>

        {skipped > 0 && (
          <div style={{ fontSize: 11, color: "var(--amber, #e0b268)" }}>
            {skipped} landscape {skipped === 1 ? "photo" : "photos"} left out — a centred
            9:16 crop of a landscape frame usually cuts the performer out entirely. Add
            those through the Reel tab if you want to crop them by hand.
          </div>
        )}

        {usable.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {usable.map((p, i) => (
              <div key={p.id} style={{ position: "relative" }}>
                <img
                  src={thumbnailUrl(p.id)}
                  alt=""
                  style={{ width: 54, height: 96, objectFit: "cover", borderRadius: 4 }}
                />
                <span
                  style={{
                    position: "absolute", top: 2, left: 2, fontSize: 9,
                    background: "rgba(0,0,0,0.65)", color: "#fff",
                    padding: "1px 4px", borderRadius: 3,
                  }}
                >
                  {i === 0 ? "cover" : i + 1}
                </span>
              </div>
            ))}
          </div>
        )}

        {usable.length < 2 ? (
          <div style={{ fontSize: 12, color: "var(--danger)" }}>
            A reel needs at least two portrait photos.
          </div>
        ) : !reel ? (
          <>
            <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
              <span>
                Length — {seconds}s, {(seconds / usable.length).toFixed(1)}s per photo
              </span>
              <input
                type="range" min={10} max={90} step={1}
                value={seconds}
                onChange={(e) => setSeconds(Number(e.target.value))}
              />
            </label>
            <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
              <span>Caption</span>
              <textarea
                className="fp-input"
                rows={3}
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                style={{ resize: "vertical", fontFamily: "inherit" }}
              />
            </label>
          </>
        ) : failed ? (
          <div style={{ fontSize: 12, color: "var(--danger)" }}>
            Rendering failed: {reel.error_message}
          </div>
        ) : !ready ? (
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            <span className="fp-spinner" style={{ marginRight: 6 }} />
            Rendering…
          </div>
        ) : (
          <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
            <span>Publish at</span>
            <input
              type="datetime-local"
              className="fp-input"
              value={when}
              onChange={(e) => setWhen(e.target.value)}
              style={{ width: 220 }}
            />
          </label>
        )}

        {error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{error}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="fp-btn-ghost" onClick={onCancel}>
            {ready ? "Close" : "Cancel"}
          </button>
          {!reel ? (
            <button
              className="fp-btn"
              disabled={usable.length < 2 || build.isPending}
              onClick={() => build.mutate()}
            >
              {build.isPending ? "Building…" : "Build reel"}
            </button>
          ) : ready ? (
            <button
              className="fp-btn"
              disabled={!when || queue.isPending}
              onClick={() => queue.mutate()}
            >
              {queue.isPending ? "Scheduling…" : "Schedule to Instagram"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
