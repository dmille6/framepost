// Reels builder — generate a silent 9:16 MP4 from up to 10 stills for IG Reels upload.
//
// Picks photos (starts with the current post; "Add more" picks from published history),
// each cropped to 9:16 via CropModal, reorderable, with a separate cover pin that drives
// both grid thumbnail and caption source. Generation is async on the backend; we poll
// every 2s while pending, then surface a Download MP4 button.

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createReel,
  deleteReel,
  fetchInstagramFormat,
  getReel,
  listHistory,
  listReels,
  reelDownloadUrl,
  thumbnailUrl,
  type HistoryPost,
  type Reel,
  type ReelCrop,
  type ReelPhoto,
} from "../api/client";
import { CardHeader } from "./PageHeader";
import CopyableBox from "./CopyableBox";
import CropModal from "./CropModal";
import { SkeletonRows } from "./Skeleton";

const MAX_PHOTOS = 10;
const DEFAULT_DURATION = 60;
const ASPECT = 9 / 16;

type Selected = {
  post: HistoryPost;
  crop: ReelCrop | null;
  cropEnd?: ReelCrop | null;
};

function defaultCropForAspect(width: number, height: number): ReelCrop {
  // Return the largest centered 9:16 rectangle that fits inside the source image.
  const srcAspect = width / height;
  if (srcAspect > ASPECT) {
    // Source is wider than 9:16 — height is the limit.
    const cropW = Math.round(height * ASPECT);
    return {
      x: Math.round((width - cropW) / 2),
      y: 0,
      width: cropW,
      height,
    };
  } else {
    // Source is taller or equal — width is the limit.
    const cropH = Math.round(width / ASPECT);
    return {
      x: 0,
      y: Math.round((height - cropH) / 2),
      width,
      height: cropH,
    };
  }
}

type ReelTabPost = {
  id: string;
  title: string | null;
  original_filename: string | null;
  width: number | null;
  height: number | null;
};

type Props = {
  postId: string;
  post: ReelTabPost;
};

export default function ReelTab({ postId, post }: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Selected[]>(() => {
    const w = post.width ?? 1080;
    const h = post.height ?? 1920;
    // Cast: Selected.post needs HistoryPost-shape fields but our consumers only
    // use id / title / original_filename / width / height. Picker fills in real HistoryPosts.
    return [{ post: post as unknown as HistoryPost, crop: defaultCropForAspect(w, h) }];
  });
  const [coverPostId, setCoverPostId] = useState<string>(postId);
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  const [caption, setCaption] = useState<string>("");
  const [captionEdited, setCaptionEdited] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [cropTarget, setCropTarget] = useState<number | null>(null);
  const [generatingReelId, setGeneratingReelId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Auto-pull caption from cover photo when cover changes (unless user has edited).
  // Aggregate performers across every photo in the Reel (not just the cover) so a
  // multi-performer recap captions everyone, even when the cover photo features only
  // one of them. The IG-format endpoint dedupes by performer.id internally.
  const extraPostIds = selected
    .map((s) => s.post.id)
    .filter((id) => id !== coverPostId);
  const coverFormat = useQuery({
    queryKey: ["instagram-format", coverPostId, extraPostIds.slice().sort().join(",")],
    queryFn: () =>
      fetchInstagramFormat(coverPostId, { extraPerformerPostIds: extraPostIds }),
  });
  useEffect(() => {
    if (!captionEdited && coverFormat.data?.caption) {
      const hashtags = coverFormat.data.hashtags.join(" ");
      const full = coverFormat.data.caption + (hashtags ? "\n\n" + hashtags : "");
      setCaption(full);
    }
  }, [coverFormat.data, captionEdited]);

  // Poll for generation progress.
  const reelStatus = useQuery({
    queryKey: ["reel", generatingReelId],
    queryFn: () => getReel(generatingReelId!),
    enabled: !!generatingReelId,
    refetchInterval: (q) => {
      const d = q.state.data as Reel | undefined;
      return d && d.status === "pending" ? 2000 : false;
    },
  });

  function addPhotos(posts: HistoryPost[]) {
    setSelected((prev) => {
      const existing = new Set(prev.map((p) => p.post.id));
      const additions = posts
        .filter((p) => !existing.has(p.id))
        .map((p) => ({
          post: p,
          crop: defaultCropForAspect(p.width ?? 1080, p.height ?? 1920),
        }));
      return [...prev, ...additions].slice(0, MAX_PHOTOS);
    });
    setPickerOpen(false);
  }

  function move(idx: number, dir: -1 | 1) {
    setSelected((prev) => {
      const target = idx + dir;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[target]] = [next[target], next[idx]];
      return next;
    });
  }

  function remove(idx: number) {
    setSelected((prev) => {
      const next = prev.filter((_, i) => i !== idx);
      // If we removed the cover, fall back to position 0.
      if (next.length > 0 && !next.some((s) => s.post.id === coverPostId)) {
        setCoverPostId(next[0].post.id);
      }
      return next;
    });
  }

  function saveCrop(idx: number, crop: ReelCrop, cropEnd: ReelCrop | null) {
    setSelected((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], crop, cropEnd };
      return next;
    });
    setCropTarget(null);
  }

  const generate = useMutation({
    mutationFn: () => {
      const photos: ReelPhoto[] = selected.map((s, i) => ({
        post_id: s.post.id,
        position: i,
        crop_start: s.crop!,
        crop_end: s.cropEnd ?? null,
      }));
      return createReel({
        cover_post_id: coverPostId,
        total_duration_seconds: duration,
        caption,
        photos,
      });
    },
    onSuccess: (reel) => {
      setError(null);
      setGeneratingReelId(reel.id);
      void qc.invalidateQueries({ queryKey: ["reels"] });
    },
    onError: (e) => {
      setError(e instanceof ApiError ? e.message : "Generation failed");
    },
  });

  const reel = reelStatus.data;
  const isGenerating =
    !!generatingReelId && (!reel || reel.status === "pending" || generate.isPending);
  const isReady = reel && reel.status === "ready";
  const isFailed = reel && reel.status === "failed";

  const allCropped = selected.every((s) => s.crop !== null);
  const canGenerate = selected.length >= 1 && allCropped && !isGenerating;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <CardHeader
        title="Reel"
        subtitle="Build a silent 9:16 MP4 from up to 10 stills. Works for Instagram Reels and TikTok — same file, upload to either. Add music in the platform's app after upload."
      />

      <PastReelsForThisPhoto postId={postId} />

      {/* Selected photo sequence */}
      <div>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>
            Photos ({selected.length}/{MAX_PHOTOS})
          </div>
          <button
            className="fp-btn-ghost"
            disabled={selected.length >= MAX_PHOTOS}
            onClick={() => setPickerOpen(true)}
            style={{ padding: "6px 12px", fontSize: 12 }}
          >
            + Add photos
          </button>
        </div>
        <div style={{ display: "grid", gap: 6 }}>
          {selected.map((s, i) => (
            <SequenceRow
              key={s.post.id}
              index={i}
              count={selected.length}
              selected={s}
              isCover={s.post.id === coverPostId}
              onMoveUp={() => move(i, -1)}
              onMoveDown={() => move(i, 1)}
              onRemove={() => remove(i)}
              onCrop={() => setCropTarget(i)}
              onSetCover={() => setCoverPostId(s.post.id)}
            />
          ))}
        </div>
      </div>

      {/* Duration */}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <label style={{ fontSize: 12, color: "var(--text-dim)", minWidth: 100 }}>
          Total length
        </label>
        <input
          type="range"
          min={10}
          max={90}
          step={1}
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <div style={{ fontSize: 12, color: "var(--text-dim)", minWidth: 60, textAlign: "right" }}>
          {duration}s · {(duration / selected.length).toFixed(1)}s each
        </div>
      </div>

      {/* Caption */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <div style={{ fontSize: 13, fontWeight: 500 }}>Caption</div>
          <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
            {captionEdited ? "Edited" : `From cover photo`}
            {caption.length > 0 && ` · ${caption.length} chars`}
          </div>
        </div>
        <textarea
          className="fp-input"
          value={caption}
          onChange={(e) => {
            setCaption(e.target.value);
            setCaptionEdited(true);
          }}
          rows={6}
          style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
        />
      </div>

      {/* Actions */}
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
          {!allCropped && "Crop all photos before generating."}
        </div>
        <button
          className="fp-btn"
          onClick={() => generate.mutate()}
          disabled={!canGenerate}
        >
          {isGenerating && <span className="fp-spinner" style={{ marginRight: 6 }} />}
          {isGenerating ? "Generating…" : "Generate Reel"}
        </button>
      </div>

      {error && (
        <div className="fp-banner-error" style={{ padding: "10px 12px", fontSize: 12 }}>
          {error}
        </div>
      )}

      {isFailed && (
        <div className="fp-banner-error" style={{ padding: "10px 12px", fontSize: 12 }}>
          Generation failed: {reel?.error_message}
        </div>
      )}

      {isReady && reel && (
        <div
          style={{
            background: "var(--teal-tint, rgba(93,202,165,0.1))",
            border: "0.5px solid rgba(93,202,165,0.25)",
            color: "var(--teal, #5dcaa5)",
            padding: 12,
            borderRadius: 8,
            display: "grid",
            gap: 10,
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 500 }}>Reel ready</div>
          <div style={{ fontSize: 11 }}>
            Download the MP4 and upload to <strong>Instagram Reels</strong> (drag into
            instagram.com) or <strong>TikTok</strong> (via the mobile app). Same file
            works for both. Hashtag conventions differ — IG likes a long block, TikTok
            prefers 3-5 trending ones.
          </div>
          <a className="fp-btn" href={reelDownloadUrl(reel.id)} download>
            Download MP4
          </a>
          {caption && (
            <>
              <div style={{ fontSize: 11, marginTop: 4 }}>Caption (paste after upload):</div>
              <CopyableBox text={caption}>
                <pre
                  style={{
                    margin: 0,
                    padding: "10px 44px 10px 12px",
                    background: "var(--bg)",
                    border: "0.5px solid var(--border)",
                    borderRadius: 8,
                    whiteSpace: "pre-wrap",
                    fontSize: 12,
                    color: "var(--text)",
                  }}
                >
                  {caption}
                </pre>
              </CopyableBox>
            </>
          )}
        </div>
      )}

      {pickerOpen && (
        <PhotoPicker
          excludeIds={new Set(selected.map((s) => s.post.id))}
          slotsRemaining={MAX_PHOTOS - selected.length}
          onClose={() => setPickerOpen(false)}
          onConfirm={addPhotos}
        />
      )}

      {cropTarget !== null && selected[cropTarget] && (
        <CropModal
          postId={selected[cropTarget].post.id}
          postWidth={selected[cropTarget].post.width ?? 1080}
          postHeight={selected[cropTarget].post.height ?? 1920}
          initialCrop={selected[cropTarget].crop}
          initialCropEnd={selected[cropTarget].cropEnd ?? null}
          onSave={(crop, cropEnd) => saveCrop(cropTarget, crop, cropEnd)}
          onCancel={() => setCropTarget(null)}
        />
      )}
    </div>
  );
}

function PastReelsForThisPhoto({ postId }: { postId: string }) {
  const qc = useQueryClient();
  const { data: reels = [], isLoading } = useQuery({
    queryKey: ["reels"],
    queryFn: listReels,
    staleTime: 30_000,
  });

  // Filter client-side — GET /api/reels returns all of them, but list size is bounded
  // (typically <100 Reels per active user) so no need for a server-side filter param yet.
  const mine = reels.filter((r) => r.cover_post_id === postId);

  const del = useMutation({
    mutationFn: (reelId: string) => deleteReel(reelId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reels"] }),
  });

  if (isLoading || mine.length === 0) return null;

  return (
    <div
      style={{
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        padding: 10,
        display: "grid",
        gap: 6,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text-dim)" }}>
        Past Reels for this photo · {mine.length}
      </div>
      {mine.map((r) => {
        const created = new Date(r.created_at);
        const isReady = r.status === "ready" && r.mp4_available;
        const isFailed = r.status === "failed";
        const isExpired = r.status === "ready" && !r.mp4_available;
        return (
          <div
            key={r.id}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto",
              alignItems: "center",
              gap: 10,
              padding: "6px 4px",
              fontSize: 12,
            }}
          >
            <div style={{ display: "grid", gap: 2 }}>
              <div>
                {created.toLocaleString()} ·{" "}
                <span style={{ color: "var(--text-dim)" }}>
                  {r.photos.length} photo{r.photos.length === 1 ? "" : "s"} ·{" "}
                  {Math.round(r.total_duration_seconds)}s
                </span>
              </div>
              {isFailed && (
                <div style={{ color: "var(--danger)", fontSize: 11 }}>
                  Failed: {r.error_message || "unknown error"}
                </div>
              )}
              {isExpired && (
                <div style={{ color: "var(--text-fade)", fontSize: 11 }}>
                  MP4 expired (30-day retention) — regenerate to download.
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {isReady && (
                <a
                  href={reelDownloadUrl(r.id)}
                  download
                  className="fp-btn-ghost"
                  style={{ padding: "4px 10px", fontSize: 11, textDecoration: "none" }}
                >
                  Download MP4
                </a>
              )}
              <button
                className="fp-btn-ghost"
                onClick={() => {
                  if (confirm(`Delete this Reel from ${created.toLocaleDateString()}?`)) {
                    del.mutate(r.id);
                  }
                }}
                disabled={del.isPending}
                style={{ padding: "4px 10px", fontSize: 11, color: "var(--danger)" }}
              >
                ×
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SequenceRow({
  index,
  count,
  selected,
  isCover,
  onMoveUp,
  onMoveDown,
  onRemove,
  onCrop,
  onSetCover,
}: {
  index: number;
  count: number;
  selected: Selected;
  isCover: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onRemove: () => void;
  onCrop: () => void;
  onSetCover: () => void;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 60px 1fr auto",
        gap: 10,
        alignItems: "center",
        padding: "8px 10px",
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
      }}
    >
      <div style={{ display: "grid", gap: 2 }}>
        <button
          className="fp-btn-ghost"
          disabled={index === 0}
          onClick={onMoveUp}
          style={{ padding: "2px 6px", fontSize: 10, lineHeight: 1 }}
          aria-label="Move up"
        >
          ▲
        </button>
        <button
          className="fp-btn-ghost"
          disabled={index === count - 1}
          onClick={onMoveDown}
          style={{ padding: "2px 6px", fontSize: 10, lineHeight: 1 }}
          aria-label="Move down"
        >
          ▼
        </button>
      </div>
      <img
        src={thumbnailUrl(selected.post.id)}
        alt=""
        style={{
          width: 60,
          height: 60,
          objectFit: "cover",
          borderRadius: 4,
        }}
      />
      <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {selected.post.title || selected.post.original_filename || "(untitled)"}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-dim)", display: "flex", gap: 8 }}>
          <span>#{index + 1}</span>
          <span>
            {selected.crop
              ? selected.cropEnd
                ? `Director pan — ${selected.crop.width}×${selected.crop.height} → ${selected.cropEnd.width}×${selected.cropEnd.height}`
                : `Cropped ${selected.crop.width}×${selected.crop.height}`
              : "Not yet cropped"}
          </span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 4 }}>
        <button
          className={isCover ? "fp-btn" : "fp-btn-ghost"}
          onClick={onSetCover}
          style={{ padding: "4px 8px", fontSize: 10 }}
          title="Set as cover — supplies the IG grid thumbnail and caption source"
        >
          {isCover ? "★ Cover" : "☆"}
        </button>
        <button
          className="fp-btn-ghost"
          onClick={onCrop}
          style={{ padding: "4px 8px", fontSize: 10 }}
        >
          Crop
        </button>
        <button
          className="fp-btn-ghost"
          onClick={onRemove}
          style={{ padding: "4px 8px", fontSize: 10, color: "var(--danger)" }}
          aria-label="Remove"
        >
          ×
        </button>
      </div>
    </div>
  );
}

function PhotoPicker({
  excludeIds,
  slotsRemaining,
  onClose,
  onConfirm,
}: {
  excludeIds: Set<string>;
  slotsRemaining: number;
  onClose: () => void;
  onConfirm: (posts: HistoryPost[]) => void;
}) {
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  const { data: posts = [], isLoading } = useQuery({
    queryKey: ["published-for-reel", q],
    queryFn: () => listHistory(q || undefined, ["posted"]),
  });

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const available = useMemo(
    () => posts.filter((p) => !excludeIds.has(p.id)),
    [posts, excludeIds],
  );

  function togglePick(id: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < slotsRemaining) {
        next.add(id);
      }
      return next;
    });
  }

  function confirm() {
    const pickedPosts = available.filter((p) => picked.has(p.id));
    onConfirm(pickedPosts);
  }

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24, zIndex: 90 }}
      onClick={onClose}
    >
      <div
        className="fp-card fp-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 100%)",
          maxHeight: "80vh",
          display: "grid",
          gap: 12,
          padding: 16,
          boxShadow: "var(--shadow-lg)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>
            Add photos · {picked.size}/{slotsRemaining} picked
          </div>
          <button className="fp-btn-ghost" onClick={onClose} style={{ padding: "4px 8px", fontSize: 11 }}>
            Cancel
          </button>
        </div>
        <input
          ref={inputRef}
          className="fp-input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by title / tags / camera"
        />
        <div
          style={{
            overflow: "auto",
            maxHeight: "55vh",
            border: "0.5px solid var(--border)",
            borderRadius: 8,
          }}
        >
          {isLoading ? (
            <SkeletonRows count={6} height={64} />
          ) : available.length === 0 ? (
            <div style={{ padding: 20, textAlign: "center", color: "var(--text-dim)", fontSize: 12 }}>
              No matching published photos.
            </div>
          ) : (
            <div style={{ display: "grid" }}>
              {available.map((p) => {
                const checked = picked.has(p.id);
                return (
                  <button
                    key={p.id}
                    onClick={() => togglePick(p.id)}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "auto 56px 1fr",
                      gap: 10,
                      alignItems: "center",
                      padding: "8px 10px",
                      background: checked ? "var(--teal-tint, rgba(93,202,165,0.1))" : "transparent",
                      border: 0,
                      borderBottom: "0.5px solid var(--border)",
                      cursor: "pointer",
                      textAlign: "left",
                      color: "inherit",
                      font: "inherit",
                    }}
                  >
                    <input type="checkbox" checked={checked} readOnly />
                    <img
                      src={thumbnailUrl(p.id)}
                      alt=""
                      style={{ width: 56, height: 56, objectFit: "cover", borderRadius: 4 }}
                    />
                    <div style={{ minWidth: 0 }}>
                      <div
                        style={{
                          fontSize: 12,
                          fontWeight: 500,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {p.title || p.original_filename || "(untitled)"}
                      </div>
                      <div style={{ fontSize: 10, color: "var(--text-dim)" }}>
                        {p.captured_at ? new Date(p.captured_at).toLocaleDateString() : "—"}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button
            className="fp-btn"
            disabled={picked.size === 0}
            onClick={confirm}
          >
            Add {picked.size} photo{picked.size === 1 ? "" : "s"}
          </button>
        </div>
      </div>
    </div>
  );
}
