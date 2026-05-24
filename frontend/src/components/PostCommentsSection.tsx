import { useQuery } from "@tanstack/react-query";

import { fetchPostComments, type PostComment } from "../api/client";
import { absoluteTime, relativeTime } from "../lib/time";
import { SkeletonRows } from "./Skeleton";

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
};

export default function PostCommentsSection({ postId }: { postId: string }) {
  const { data: comments = [], isLoading } = useQuery({
    queryKey: ["post-comments", postId],
    queryFn: () => fetchPostComments(postId),
  });

  if (isLoading) {
    return (
      <div>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>Comments</div>
        <SkeletonRows count={2} height={48} />
      </div>
    );
  }
  if (comments.length === 0) return null;

  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>
        Comments ({comments.length})
      </div>
      <div style={{ display: "grid", gap: 10 }}>
        {comments.map((c) => (
          <CommentRow key={c.id} comment={c} />
        ))}
      </div>
    </div>
  );
}

function CommentRow({ comment }: { comment: PostComment }) {
  const tint = PLATFORM_TINT[comment.platform] ?? {
    bg: "rgba(255,255,255,0.04)",
    fg: "var(--text-dim)",
    border: "var(--border)",
  };
  return (
    <div
      style={{
        padding: "10px 12px",
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        fontSize: 13,
        lineHeight: 1.5,
      }}
    >
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
            fontSize: 10,
            fontWeight: 600,
            padding: "1px 7px",
            borderRadius: 999,
            background: tint.bg,
            color: tint.fg,
            border: `0.5px solid ${tint.border}`,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {PLATFORM_LABEL[comment.platform] ?? comment.platform}
        </span>
        {comment.author_url ? (
          <a
            href={comment.author_url}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: 12, fontWeight: 500, color: "var(--text)", textDecoration: "none" }}
          >
            {comment.author_display_name || comment.author_handle || "(unknown)"}
          </a>
        ) : (
          <span style={{ fontSize: 12, fontWeight: 500 }}>
            {comment.author_display_name || comment.author_handle || "(unknown)"}
          </span>
        )}
        {comment.author_handle &&
          comment.author_handle !== comment.author_display_name && (
            <span style={{ fontSize: 11, color: "var(--text-fade)" }}>
              {comment.author_handle}
            </span>
          )}
        <span
          style={{ fontSize: 11, color: "var(--text-fade)", marginLeft: "auto" }}
          title={absoluteTime(comment.posted_at ?? comment.fetched_at)}
        >
          {relativeTime(comment.posted_at ?? comment.fetched_at)}
        </span>
      </div>
      <div style={{ color: "var(--text)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {comment.body || <span style={{ color: "var(--text-fade)" }}>(empty)</span>}
      </div>
    </div>
  );
}
