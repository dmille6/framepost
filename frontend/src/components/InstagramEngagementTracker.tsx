import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addIGComment,
  deleteIGComment,
  fetchIGEngagement,
  setIGLikes,
} from "../api/client";
import { absoluteTime, relativeTime } from "../lib/time";

type Props = { postId: string };

/**
 * Manual engagement tracking for Instagram. IG has no public API for personal accounts,
 * so the user types in their like count and copies in any comments by hand. The data
 * flows through the same Activity views as auto-synced platforms (Bluesky/Pixelfed/Flickr):
 * likes appear as an aggregate count via EngagementSnapshot, comments are real PostComment
 * rows that show up in both the per-post comments section and the cross-platform stream.
 */
export default function InstagramEngagementTracker({ postId }: Props) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["ig-engagement", postId],
    queryFn: () => fetchIGEngagement(postId),
  });

  const [likesInput, setLikesInput] = useState<string>("");
  const [savedLikes, setSavedLikes] = useState<"idle" | "saving" | "saved">("idle");

  // Sync the input with whatever the server has stored, so when the user lands on the
  // tab they see their current value rather than blank.
  useEffect(() => {
    if (data) setLikesInput(String(data.likes_count));
  }, [data?.likes_count]);

  const saveLikes = useMutation({
    mutationFn: (count: number) => setIGLikes(postId, count),
    onMutate: () => setSavedLikes("saving"),
    onSuccess: () => {
      setSavedLikes("saved");
      void qc.invalidateQueries({ queryKey: ["ig-engagement", postId] });
      void qc.invalidateQueries({ queryKey: ["post-platforms", postId] });
      void qc.invalidateQueries({ queryKey: ["activity-by-post"] });
      setTimeout(() => setSavedLikes("idle"), 1500);
    },
  });

  const [newAuthor, setNewAuthor] = useState("");
  const [newBody, setNewBody] = useState("");
  const addComment = useMutation({
    mutationFn: () => addIGComment(postId, newAuthor, newBody),
    onSuccess: () => {
      setNewAuthor("");
      setNewBody("");
      void qc.invalidateQueries({ queryKey: ["ig-engagement", postId] });
      void qc.invalidateQueries({ queryKey: ["post-comments", postId] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
      void qc.invalidateQueries({ queryKey: ["activity-by-post"] });
      void qc.invalidateQueries({ queryKey: ["activity-unread-count"] });
    },
  });

  const deleteComment = useMutation({
    mutationFn: (commentId: number) => deleteIGComment(postId, commentId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["ig-engagement", postId] });
      void qc.invalidateQueries({ queryKey: ["post-comments", postId] });
      void qc.invalidateQueries({ queryKey: ["activity"] });
      void qc.invalidateQueries({ queryKey: ["activity-by-post"] });
    },
  });

  if (isLoading) return null;

  const likesDirty = String(data?.likes_count ?? "") !== likesInput.trim();
  const parsedLikes = Math.max(0, Math.floor(Number(likesInput) || 0));

  return (
    <div
      style={{
        marginTop: 8,
        padding: 16,
        border: "0.5px solid var(--border)",
        borderRadius: 10,
        background: "var(--bg)",
        display: "grid",
        gap: 14,
      }}
    >
      <div>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Track engagement</div>
        <div style={{ fontSize: 11, color: "var(--text-fade)", marginTop: 2 }}>
          Manually log Instagram likes and comments — they'll show up in Activity alongside
          your auto-synced platforms.
          {data?.last_updated_at && (
            <> Last update <span title={absoluteTime(data.last_updated_at)}>{relativeTime(data.last_updated_at)}</span>.</>
          )}
        </div>
      </div>

      {/* Likes */}
      <div style={{ display: "grid", gap: 6 }}>
        <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
          Likes count
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            className="fp-input"
            type="number"
            min={0}
            value={likesInput}
            onChange={(e) => setLikesInput(e.target.value)}
            placeholder="0"
            style={{ flex: 1, maxWidth: 200 }}
          />
          <button
            className="fp-btn"
            onClick={() => saveLikes.mutate(parsedLikes)}
            disabled={!likesDirty || saveLikes.isPending}
            style={{ padding: "8px 14px", fontSize: 13 }}
          >
            {saveLikes.isPending && <span className="fp-spinner" />}
            {saveLikes.isPending ? "Saving" : savedLikes === "saved" ? "Saved" : "Save"}
          </button>
        </div>
      </div>

      {/* Comments */}
      <div style={{ display: "grid", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
          Comments {(data?.comments_count ?? 0) > 0 ? `(${data?.comments_count})` : ""}
        </span>

        {data && data.comments.length > 0 && (
          <div style={{ display: "grid", gap: 8 }}>
            {data.comments.map((c) => (
              <div
                key={c.id}
                style={{
                  padding: "8px 10px",
                  border: "0.5px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 12,
                  display: "grid",
                  gap: 4,
                  position: "relative",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    gap: 8,
                    alignItems: "baseline",
                    flexWrap: "wrap",
                  }}
                >
                  <span style={{ fontWeight: 500, color: "var(--text)" }}>
                    {c.author_handle}
                  </span>
                  <span
                    style={{ fontSize: 10, color: "var(--text-fade)", marginLeft: "auto" }}
                    title={absoluteTime(c.posted_at ?? c.fetched_at)}
                  >
                    {relativeTime(c.posted_at ?? c.fetched_at)}
                  </span>
                  <button
                    onClick={() => deleteComment.mutate(c.id)}
                    aria-label="Delete comment"
                    style={{
                      background: "transparent",
                      border: 0,
                      color: "var(--text-fade)",
                      cursor: "pointer",
                      padding: 0,
                      fontSize: 13,
                      lineHeight: 1,
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.color = "var(--danger)")}
                    onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-fade)")}
                  >
                    ×
                  </button>
                </div>
                <div style={{ color: "var(--text)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {c.body}
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: "grid", gap: 6 }}>
          <input
            className="fp-input"
            value={newAuthor}
            onChange={(e) => setNewAuthor(e.target.value)}
            placeholder="@username"
            style={{ fontSize: 12 }}
          />
          <textarea
            className="fp-textarea"
            value={newBody}
            onChange={(e) => setNewBody(e.target.value)}
            placeholder="Comment text"
            rows={2}
            style={{ fontSize: 12 }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              className="fp-btn-ghost"
              onClick={() => addComment.mutate()}
              disabled={
                !newAuthor.trim() || !newBody.trim() || addComment.isPending
              }
              style={{ padding: "6px 12px", fontSize: 12 }}
            >
              {addComment.isPending && <span className="fp-spinner" />}
              {addComment.isPending ? "Adding" : "Add comment"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
