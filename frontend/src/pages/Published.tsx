import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, type HistoryPost, listHistory, repostToFlickr, thumbnailUrl } from "../api/client";
import ActivityTimeline from "../components/ActivityTimeline";
import PostCommentsSection from "../components/PostCommentsSection";
import EmptyState from "../components/EmptyState";
import InstagramPanel from "../components/InstagramPanel";
import PageHeader from "../components/PageHeader";
import PlatformStatusRow from "../components/PlatformStatusRow";
import RedditPanel from "../components/RedditPanel";
import ReelTab from "../components/ReelTab";
import { SkeletonGrid } from "../components/Skeleton";
import Topbar from "../components/Topbar";
import { usePageTitle } from "../hooks/usePageTitle";
import { absoluteTime, relativeTime } from "../lib/time";

const STATUSES = ["posted", "late", "missed", "failed"] as const;
type StatusKey = typeof STATUSES[number];

export default function Published() {
  usePageTitle("Published");
  const [active, setActive] = useState<Set<StatusKey>>(new Set(STATUSES));
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<HistoryPost | null>(null);

  const { data: posts = [], isLoading } = useQuery({
    queryKey: ["published", q, [...active].sort()],
    queryFn: () => listHistory(q || undefined, [...active]),
    refetchInterval: 30_000,
  });

  const stats = useMemo(() => {
    const c: Record<string, number> = { posted: 0, late: 0, missed: 0, failed: 0 };
    for (const p of posts) c[p.status] = (c[p.status] ?? 0) + 1;
    return c;
  }, [posts]);

  function toggle(s: StatusKey) {
    setActive((prev) => {
      const n = new Set(prev);
      if (n.has(s)) n.delete(s);
      else n.add(s);
      return n.size === 0 ? new Set(STATUSES) : n;
    });
  }

  return (
    <>
      <Topbar />
      <div className="fp-page fp-fade-in">
        <PageHeader
          title="Published"
          subtitle="Posted, late, missed, and failed posts. Click a tile for the full activity timeline."
        />

        <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
          {STATUSES.map((s) => (
            <button
              key={s}
              className={active.has(s) ? "fp-btn" : "fp-btn-ghost"}
              onClick={() => toggle(s)}
              style={{ padding: "6px 12px", fontSize: 13 }}
            >
              {s} <span style={{ opacity: 0.7, marginLeft: 4 }}>{stats[s] ?? 0}</span>
            </button>
          ))}
          <input
            className="fp-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title / tags / camera / lens"
            style={{ marginLeft: "auto", width: 280 }}
          />
        </div>

        {isLoading ? (
          <SkeletonGrid count={6} cardHeight={200} />
        ) : posts.length === 0 ? (
          <EmptyState
            title="Nothing here yet"
            body="No posts match the current filters. Try widening the status set, or come back once your scheduled posts have fired."
          />
        ) : (
          <div className="fp-grid-cards">
            {posts.map((p) => (
              <Tile key={p.id} post={p} onClick={() => setSelected(p)} />
            ))}
          </div>
        )}
      </div>

      {selected && <DetailModal post={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function Tile({ post, onClick }: { post: HistoryPost; onClick: () => void }) {
  const when = post.posted_at ?? post.scheduled_at;
  return (
    <button
      onClick={onClick}
      className="fp-card-button"
      style={{
        display: "block",
        textAlign: "left",
        background: "var(--card)",
        border: "0.5px solid var(--border)",
        borderRadius: 12,
        padding: 0,
        overflow: "hidden",
        color: "inherit",
        font: "inherit",
      }}
    >
      <div style={{ aspectRatio: "4 / 3", background: "#0a0a0a" }}>
        <img src={thumbnailUrl(post.id)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </div>
      <div style={{ padding: "12px 14px" }}>
        <div style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {post.title || post.original_filename || "(untitled)"}
        </div>
        <div
          style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}
          title={absoluteTime(when)}
        >
          {relativeTime(when)}
        </div>
        <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className={`fp-pill fp-pill-${post.status}`}>{post.status}</span>
          {post.posted_to_instagram_at && (
            <span
              title={`Posted to Instagram ${absoluteTime(post.posted_to_instagram_at)}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 7px",
                borderRadius: 999,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.04em",
                color: "#e4a3d2",
                background: "rgba(228, 163, 210, 0.1)",
                border: "0.5px solid rgba(228, 163, 210, 0.25)",
              }}
            >
              IG
            </span>
          )}
          {post.reddit_posted_at && (
            <span
              title={`Posted to Reddit ${absoluteTime(post.reddit_posted_at)}`}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 7px",
                borderRadius: 999,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.04em",
                color: "#ff8a65",
                background: "rgba(255, 138, 101, 0.1)",
                border: "0.5px solid rgba(255, 138, 101, 0.25)",
              }}
            >
              Reddit
            </span>
          )}
          {post.error_message && (
            <span style={{ fontSize: 11, color: "var(--danger)" }}>
              {post.error_message.length > 36 ? post.error_message.slice(0, 36) + "…" : post.error_message}
            </span>
          )}
        </div>
      </div>
    </button>
  );
}

function DetailModal({ post, onClose }: { post: HistoryPost; onClose: () => void }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"details" | "instagram" | "reddit" | "reel">("details");
  const [repostBanner, setRepostBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const repost = useMutation({
    mutationFn: () => repostToFlickr(post.id),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["published"] });
      void qc.invalidateQueries({ queryKey: ["post-platforms", post.id] });
      void qc.invalidateQueries({ queryKey: ["events", post.id] });
      const note = r.flickr_delete_error
        ? `Re-queued, but Flickr delete failed: ${r.flickr_delete_error}. The new upload will still go through, but you may have a duplicate to clean up manually.`
        : "Re-queued. The worker will re-upload to Flickr within a minute.";
      setRepostBanner({ kind: r.flickr_delete_error ? "error" : "ok", text: note });
    },
    onError: (e) => {
      setRepostBanner({
        kind: "error",
        text: e instanceof ApiError ? e.message : "Re-post failed",
      });
    },
  });

  const canRepost = !!post.flickr_photo_id || post.status === "failed" || post.status === "missed";
  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24 }}
      onClick={onClose}
    >
      <div
        className="fp-card fp-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(900px, 100%)", maxHeight: "90vh", overflow: "auto", display: "grid", gap: 16, boxShadow: "var(--shadow-lg)" }}
      >
        <div style={{ display: "grid", gridTemplateColumns: "240px 1fr", gap: 16 }}>
          <img
            src={thumbnailUrl(post.id)}
            alt=""
            style={{ width: "100%", borderRadius: 8, objectFit: "cover" }}
          />
          <div style={{ display: "grid", gap: 8 }}>
            <div>
              <div style={{ fontSize: 16, fontWeight: 500 }}>{post.title || post.original_filename || "(untitled)"}</div>
              <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span className={`fp-pill fp-pill-${post.status}`}>{post.status}</span>
                {post.flickr_url && (
                  <a href={post.flickr_url} target="_blank" rel="noreferrer" style={{ fontSize: 13 }}>
                    View on Flickr ↗
                  </a>
                )}
              </div>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", display: "grid", gap: 2 }}>
              <div>Camera: {[post.camera_make, post.camera_model].filter(Boolean).join(" ") || "—"}</div>
              <div>Lens: {post.lens || "—"}</div>
              <div>
                Exposure: {[
                  post.aperture ? `f/${post.aperture}` : null,
                  post.shutter_speed,
                  post.iso ? `ISO ${post.iso}` : null,
                ].filter(Boolean).join(" · ") || "—"}
              </div>
              <div>Captured: {post.captured_at ? new Date(post.captured_at).toLocaleString() : "—"}</div>
              <div>Scheduled: {post.scheduled_at ? new Date(post.scheduled_at + "Z").toLocaleString() : "—"}</div>
              <div>Posted: {post.posted_at ? new Date(post.posted_at + "Z").toLocaleString() : "—"}</div>
              {post.error_message && (
                <div style={{ color: "var(--danger)" }}>Error: {post.error_message}</div>
              )}
            </div>
            <PlatformStatusRow postId={post.id} />
          </div>
        </div>

        <div
          role="tablist"
          style={{
            display: "flex",
            gap: 0,
            borderBottom: "0.5px solid var(--border)",
            marginTop: 4,
          }}
        >
          <ModalTab active={tab === "details"} onClick={() => setTab("details")}>
            Details
          </ModalTab>
          <ModalTab active={tab === "instagram"} onClick={() => setTab("instagram")}>
            Instagram
          </ModalTab>
          <ModalTab active={tab === "reddit"} onClick={() => setTab("reddit")}>
            Reddit
          </ModalTab>
          <ModalTab active={tab === "reel"} onClick={() => setTab("reel")}>
            Reel
          </ModalTab>
        </div>

        {tab === "details" && (
          <>
            {post.description && (
              <div style={{ background: "var(--bg)", padding: 12, borderRadius: 8, fontSize: 13 }}>
                {post.description}
              </div>
            )}
            {post.tags && (
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Tags: {post.tags}</div>
            )}

            <div>
              <PostCommentsSection postId={post.id} />

              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>Activity timeline</div>
              <ActivityTimeline postId={post.id} />
            </div>
          </>
        )}

        {tab === "instagram" && <InstagramPanel postId={post.id} />}

        {tab === "reddit" && <RedditPanel postId={post.id} />}

        {tab === "reel" && <ReelTab postId={post.id} post={post} />}

        {repostBanner && (
          <div
            style={{
              padding: "10px 12px",
              borderRadius: 8,
              fontSize: 12,
              background: repostBanner.kind === "ok" ? "var(--teal-tint)" : "var(--danger-tint)",
              color: repostBanner.kind === "ok" ? "var(--teal)" : "var(--danger)",
              border: `0.5px solid ${repostBanner.kind === "ok" ? "rgba(93,202,165,0.2)" : "rgba(245,156,156,0.2)"}`,
            }}
          >
            {repostBanner.text}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          {canRepost ? (
            <button
              className="fp-btn-danger"
              disabled={repost.isPending}
              onClick={() => {
                const msg = post.flickr_photo_id
                  ? "This will DELETE the photo from Flickr (faves, views, and comments will be lost) and re-upload a fresh copy with full EXIF metadata. Bluesky/Pixelfed posts won't be touched.\n\nProceed?"
                  : "This will queue the post for another Flickr upload attempt. Bluesky/Pixelfed posts won't be touched.\n\nProceed?";
                if (confirm(msg)) repost.mutate();
              }}
              title={post.flickr_photo_id
                ? "Delete from Flickr and re-upload with up-to-date metadata"
                : "Re-attempt Flickr upload"}
            >
              {repost.isPending && <span className="fp-spinner" style={{ marginRight: 6 }} />}
              {repost.isPending ? "Re-queuing" : post.flickr_photo_id ? "Re-post to Flickr" : "Retry Flickr upload"}
            </button>
          ) : <span />}
          <button className="fp-btn-ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function ModalTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        background: "transparent",
        border: 0,
        borderBottom: active ? "2px solid var(--teal)" : "2px solid transparent",
        marginBottom: -1,
        padding: "8px 14px",
        fontSize: 13,
        fontWeight: active ? 500 : 400,
        color: active ? "var(--text)" : "var(--text-dim)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}
