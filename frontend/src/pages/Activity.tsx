import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  type CommentActivityItem,
  type PostActivitySummary,
  fetchActivityByPost,
  fetchCommentActivity,
  markAllActivitySeen,
  syncActivityNow,
  thumbnailUrl,
} from "../api/client";
import EmptyState from "../components/EmptyState";
import PageHeader from "../components/PageHeader";
import { SkeletonRows } from "../components/Skeleton";
import Topbar from "../components/Topbar";
import { usePageTitle } from "../hooks/usePageTitle";
import { absoluteTime, relativeTime } from "../lib/time";

const PLATFORM_LABEL: Record<string, string> = {
  flickr: "Flickr",
  bluesky: "Bluesky",
  pixelfed: "Pixelfed",
  mastodon: "Mastodon",
};
const PLATFORM_TINT: Record<string, { bg: string; fg: string; border: string }> = {
  flickr: { bg: "rgba(255, 0, 132, 0.08)", fg: "#ff5fa6", border: "rgba(255, 95, 166, 0.25)" },
  bluesky: { bg: "rgba(80, 138, 255, 0.1)", fg: "#7ba6ff", border: "rgba(123, 166, 255, 0.25)" },
  pixelfed: { bg: "rgba(184, 132, 245, 0.1)", fg: "#c5a3f5", border: "rgba(197, 163, 245, 0.25)" },
  mastodon: { bg: "rgba(99, 100, 255, 0.1)", fg: "#9899ff", border: "rgba(152, 153, 255, 0.25)" },
};

type View = "stream" | "by-post";

export default function Activity() {
  usePageTitle("Activity");
  const qc = useQueryClient();
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [view, setView] = useState<View>(() => {
    const saved = localStorage.getItem("framepost.activity.view");
    return saved === "by-post" ? "by-post" : "stream";
  });

  function setViewPersistent(v: View) {
    setView(v);
    localStorage.setItem("framepost.activity.view", v);
  }

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["activity", onlyUnread],
    queryFn: () => fetchCommentActivity(onlyUnread, 200, 0),
    refetchInterval: 60_000,
    enabled: view === "stream",
  });

  const { data: byPost = [], isLoading: isByPostLoading } = useQuery({
    queryKey: ["activity-by-post"],
    queryFn: () => fetchActivityByPost(200),
    refetchInterval: 60_000,
    enabled: view === "by-post",
  });

  const markSeen = useMutation({
    mutationFn: markAllActivitySeen,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["activity"] });
      void qc.invalidateQueries({ queryKey: ["activity-unread-count"] });
    },
  });

  const syncNow = useMutation({
    mutationFn: syncActivityNow,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["activity"] });
      void qc.invalidateQueries({ queryKey: ["activity-unread-count"] });
    },
  });

  // Auto-mark all unread as seen ~3 seconds after the page renders. Gives the user a moment
  // to glance at the unread state before it resets — just enough to register "yeah, that's
  // new" without forcing a manual click.
  useEffect(() => {
    if (items.length === 0) return;
    const hasUnread = items.some((i) => i.seen_at === null);
    if (!hasUnread) return;
    const t = setTimeout(() => markSeen.mutate(), 3000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length]);

  return (
    <>
      <Topbar />
      <div className="fp-page fp-fade-in">
        <PageHeader
          title="Activity"
          subtitle="Comments, faves, and replies across all your connected platforms. Synced daily; click 'Sync now' for fresh data."
          actions={
            <div style={{ display: "flex", gap: 8 }}>
              <div
                role="tablist"
                style={{
                  display: "inline-flex",
                  border: "0.5px solid var(--border-strong)",
                  borderRadius: 8,
                  overflow: "hidden",
                }}
              >
                <ViewToggleButton active={view === "stream"} onClick={() => setViewPersistent("stream")}>
                  Stream
                </ViewToggleButton>
                <ViewToggleButton active={view === "by-post"} onClick={() => setViewPersistent("by-post")}>
                  By post
                </ViewToggleButton>
              </div>
              {view === "stream" && (
                <button
                  className={onlyUnread ? "fp-btn" : "fp-btn-ghost"}
                  onClick={() => setOnlyUnread((v) => !v)}
                  style={{ padding: "7px 12px", fontSize: 13 }}
                >
                  {onlyUnread ? "Unread only" : "All"}
                </button>
              )}
              <button
                className="fp-btn-ghost"
                onClick={() => syncNow.mutate()}
                disabled={syncNow.isPending}
                style={{ padding: "7px 12px", fontSize: 13 }}
              >
                {syncNow.isPending && <span className="fp-spinner" />}
                {syncNow.isPending ? "Syncing" : "Sync now"}
              </button>
            </div>
          }
        />

        {view === "stream" ? (
          isLoading ? (
            <div className="fp-card">
              <SkeletonRows count={6} height={56} />
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              title={onlyUnread ? "No unread activity" : "No activity yet"}
              body={
                onlyUnread
                  ? "Everything's caught up. Switch to All to see past activity."
                  : "Once you have posts on Flickr / Bluesky / Pixelfed, likes and comments will show up here. Click 'Sync now' to pull the latest."
              }
            />
          ) : (
            <div className="fp-card" style={{ padding: 0, overflow: "hidden" }}>
              {items.map((item, i) => (
                <ActivityRow
                  key={`${item.kind}-${item.id}`}
                  item={item}
                  divider={i < items.length - 1}
                />
              ))}
            </div>
          )
        ) : isByPostLoading ? (
          <div className="fp-card">
            <SkeletonRows count={4} height={88} />
          </div>
        ) : byPost.length === 0 ? (
          <EmptyState
            title="No engagement yet"
            body="Once your posts start collecting likes or comments, they'll show up here grouped by photo. Click 'Sync now' to pull the latest."
          />
        ) : (
          <div className="fp-card" style={{ padding: 0, overflow: "hidden" }}>
            {byPost.map((post, i) => (
              <ByPostRow key={post.post_id} post={post} divider={i < byPost.length - 1} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

function ViewToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        background: active ? "var(--hover)" : "transparent",
        color: active ? "var(--text)" : "var(--text-dim)",
        border: 0,
        padding: "7px 14px",
        fontSize: 13,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function ByPostRow({ post, divider }: { post: PostActivitySummary; divider: boolean }) {
  const isUnread = post.unread > 0;
  return (
    <Link
      to={`/published?post=${post.post_id}`}
      style={{
        display: "grid",
        gridTemplateColumns: "72px 1fr auto",
        gap: 16,
        padding: "16px 18px",
        borderBottom: divider ? "0.5px solid var(--border)" : "none",
        background: isUnread ? "rgba(93,202,165,0.04)" : "transparent",
        textDecoration: "none",
        color: "inherit",
        alignItems: "center",
        transition: "background 120ms ease",
        position: "relative",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover)")}
      onMouseLeave={(e) =>
        (e.currentTarget.style.background = isUnread ? "rgba(93,202,165,0.04)" : "transparent")
      }
    >
      {isUnread && (
        <span
          aria-label="has unread"
          style={{
            position: "absolute",
            left: 4,
            top: "50%",
            transform: "translateY(-50%)",
            width: 6,
            height: 6,
            borderRadius: 999,
            background: "var(--teal)",
            boxShadow: "0 0 6px rgba(93,202,165,0.6)",
          }}
        />
      )}
      <img
        src={thumbnailUrl(post.post_id)}
        alt=""
        style={{
          width: 72,
          height: 72,
          borderRadius: 8,
          objectFit: "cover",
          background: "#0a0a0a",
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            marginBottom: 4,
          }}
        >
          {post.post_title || "(untitled)"}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-fade)", marginBottom: 8 }}>
          {post.posted_at && (
            <span title={absoluteTime(post.posted_at)}>
              Posted {relativeTime(post.posted_at)}
            </span>
          )}
          {post.newest_activity_at && post.newest_activity_at !== post.posted_at && (
            <>
              {post.posted_at && <span> · </span>}
              <span title={absoluteTime(post.newest_activity_at)}>
                last activity {relativeTime(post.newest_activity_at)}
              </span>
            </>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(post.platforms).map(([platform, breakdown]) => (
            <PlatformPill key={platform} platform={platform} breakdown={breakdown} />
          ))}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--text)" }}>
            <span style={{ color: "#ff6b9c" }}>♥</span>
            {post.total_likes}
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 13, color: "var(--text)" }}>
            <span style={{ color: "var(--text-dim)" }}>💬</span>
            {post.total_comments}
          </span>
        </div>
        {post.unread > 0 && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: "var(--teal)",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            {post.unread} new
          </span>
        )}
      </div>
    </Link>
  );
}

function PlatformPill({
  platform,
  breakdown,
}: {
  platform: string;
  breakdown: { likes: number; comments: number; unread: number };
}) {
  const tint = PLATFORM_TINT[platform] ?? {
    bg: "rgba(255,255,255,0.04)",
    fg: "var(--text-dim)",
    border: "var(--border)",
  };
  return (
    <span
      title={`${breakdown.unread} unread`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "2px 9px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 500,
        color: tint.fg,
        background: tint.bg,
        border: `0.5px solid ${tint.border}`,
      }}
    >
      <span>{PLATFORM_LABEL[platform] ?? platform}</span>
      {breakdown.likes > 0 && <span>♥ {breakdown.likes}</span>}
      {breakdown.comments > 0 && <span>💬 {breakdown.comments}</span>}
    </span>
  );
}

function ActivityRow({ item, divider }: { item: CommentActivityItem; divider: boolean }) {
  const tint = PLATFORM_TINT[item.platform] ?? {
    bg: "rgba(255,255,255,0.04)",
    fg: "var(--text-dim)",
    border: "var(--border)",
  };
  const isUnread = item.seen_at === null;

  return (
    <Link
      to={`/published?post=${item.post_id}`}
      style={{
        display: "grid",
        gridTemplateColumns: "48px 1fr auto",
        gap: 14,
        padding: "14px 18px",
        borderBottom: divider ? "0.5px solid var(--border)" : "none",
        background: isUnread ? "rgba(93,202,165,0.04)" : "transparent",
        textDecoration: "none",
        color: "inherit",
        alignItems: "center",
        transition: "background 120ms ease",
        position: "relative",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover)")}
      onMouseLeave={(e) =>
        (e.currentTarget.style.background = isUnread ? "rgba(93,202,165,0.04)" : "transparent")
      }
    >
      {isUnread && (
        <span
          aria-label="unread"
          style={{
            position: "absolute",
            left: 4,
            top: "50%",
            transform: "translateY(-50%)",
            width: 6,
            height: 6,
            borderRadius: 999,
            background: "var(--teal)",
            boxShadow: "0 0 6px rgba(93,202,165,0.6)",
          }}
        />
      )}
      <img
        src={thumbnailUrl(item.post_id)}
        alt=""
        style={{
          width: 48,
          height: 48,
          borderRadius: 6,
          objectFit: "cover",
          background: "#0a0a0a",
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "baseline",
            gap: 8,
            flexWrap: "wrap",
            marginBottom: 4,
          }}
        >
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: 999,
              background: tint.bg,
              color: tint.fg,
              border: `0.5px solid ${tint.border}`,
            }}
          >
            {PLATFORM_LABEL[item.platform] ?? item.platform}
          </span>
          {item.kind === "like" && (
            <span
              aria-label="like"
              style={{
                fontSize: 13,
                color: "#ff6b9c",
                lineHeight: 1,
              }}
              title="liked your post"
            >
              ♥
            </span>
          )}
          {item.author_display_name && (
            <span style={{ fontSize: 13, fontWeight: 500 }}>{item.author_display_name}</span>
          )}
          {item.author_handle &&
            item.author_handle !== item.author_display_name && (
              <span style={{ fontSize: 12, color: "var(--text-fade)" }}>
                {item.author_handle}
              </span>
            )}
          <span
            style={{ fontSize: 11, color: "var(--text-fade)" }}
            title={absoluteTime(item.posted_at ?? item.fetched_at)}
          >
            {relativeTime(item.posted_at ?? item.fetched_at)}
          </span>
        </div>
        {item.kind === "comment" ? (
          <div
            style={{
              fontSize: 13,
              color: "var(--text)",
              overflow: "hidden",
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              wordBreak: "break-word",
            }}
          >
            {item.body || <span style={{ color: "var(--text-fade)" }}>(empty comment)</span>}
          </div>
        ) : (
          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>liked your post</div>
        )}
        {item.post_title && (
          <div
            style={{
              fontSize: 11,
              color: "var(--text-dim)",
              marginTop: 4,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            on: {item.post_title}
          </div>
        )}
      </div>
      <span style={{ fontSize: 14, color: "var(--text-fade)", paddingLeft: 4 }}>›</span>
    </Link>
  );
}
