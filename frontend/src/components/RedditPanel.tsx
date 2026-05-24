import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchRedditFormat,
  fullImageUrl,
  markRedditPosted,
  redditImageUrl,
} from "../api/client";
import { absoluteTime, relativeTime } from "../lib/time";
import CopyableBox from "./CopyableBox";
import { CardHeader } from "./PageHeader";
import { SkeletonRows } from "./Skeleton";

type Props = { postId: string };

export default function RedditPanel({ postId }: Props) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["reddit-format", postId],
    queryFn: () => fetchRedditFormat(postId),
  });

  const [withOc, setWithOc] = useState(true);

  const mark = useMutation({
    mutationFn: ({ posted }: { posted: boolean }) => markRedditPosted(postId, posted),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["reddit-format", postId] });
      void qc.invalidateQueries({ queryKey: ["post", postId] });
      void qc.invalidateQueries({ queryKey: ["published"] });
    },
  });

  if (isLoading) return <SkeletonRows count={4} height={36} />;
  if (error) return <div style={{ color: "var(--danger)" }}>{(error as Error).message}</div>;
  if (!data) return null;

  const title = withOc ? data.title_with_oc : data.title_clean;
  const titleLen = title.length;
  const overLimit = titleLen > 300;
  const posted = !!data.reddit_posted_at;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <CardHeader
        title="Reddit"
        subtitle={
          posted ? (
            <span title={absoluteTime(data.reddit_posted_at)}>
              Marked posted to Reddit {relativeTime(data.reddit_posted_at)}
            </span>
          ) : (
            "Open a subreddit's submit page with the title pre-filled, then drag the image in."
          )
        }
        action={
          <button
            onClick={() => mark.mutate({ posted: !posted })}
            className={posted ? "fp-btn-ghost" : "fp-btn"}
            disabled={mark.isPending}
            style={{ padding: "7px 12px", fontSize: 12 }}
            title={posted ? "Remove the Reddit-posted flag" : "Mark this photo as posted to Reddit"}
          >
            {posted ? "Unmark posted" : "Mark posted"}
          </button>
        }
      />

      {/* Title block */}
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>Title</span>
          <span
            style={{
              fontSize: 11,
              color: overLimit ? "var(--danger)" : "var(--text-fade)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {titleLen}/300
          </span>
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              color: "var(--text-dim)",
              cursor: "pointer",
              userSelect: "none",
            }}
          >
            <input
              type="checkbox"
              checked={withOc}
              onChange={(e) => setWithOc(e.target.checked)}
              style={{ accentColor: "var(--teal)", margin: 0 }}
            />
            Prefix <code style={{ fontSize: 11, color: "var(--teal)" }}>[OC]</code>
          </label>
        </div>
        <CopyableBox text={title}>
          <div
            style={{
              padding: "10px 44px 10px 12px",
              background: "var(--bg)",
              border: "0.5px solid var(--border)",
              borderRadius: 8,
              fontSize: 13,
              color: "var(--text)",
              wordBreak: "break-word",
            }}
          >
            {title || <span style={{ color: "var(--text-fade)" }}>(empty title)</span>}
          </div>
        </CopyableBox>
      </div>

      {/* Image download — two presets */}
      <div style={{ display: "grid", gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>Image</span>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <a
            href={redditImageUrl(postId)}
            download
            className="fp-btn"
            style={{ padding: "8px 14px", fontSize: 13, textDecoration: "none" }}
            title="2048 px, sRGB, ~2-3 MB. Recommended for Reddit."
          >
            Download Reddit-optimized
          </a>
          <a
            href={fullImageUrl(postId)}
            download
            className="fp-btn-ghost"
            style={{ padding: "8px 14px", fontSize: 13, textDecoration: "none" }}
            title="Original Lightroom JPEG, full resolution. Reddit will re-encode it."
          >
            Download original
          </a>
        </div>
        <span style={{ fontSize: 11, color: "var(--text-fade)", lineHeight: 1.5 }}>
          Reddit-optimized: 2048 px long edge, sRGB color profile applied so it renders correctly
          in Reddit's feed. Original: full Lightroom export — bigger file, Reddit will downsize
          it server-side.
        </span>
      </div>

      {/* Subreddit shortcuts */}
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
          <span style={{ fontSize: 12, fontWeight: 600 }}>Submit to subreddit</span>
          <a
            href="/settings/platforms"
            style={{ fontSize: 11, color: "var(--text-dim)", textDecoration: "none" }}
          >
            Edit list →
          </a>
        </div>
        {data.subreddits.length === 0 ? (
          <div
            style={{
              padding: 12,
              border: "0.5px dashed var(--border-strong)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--text-dim)",
              textAlign: "center",
            }}
          >
            No subreddits configured. Add some in Settings → Platforms → Reddit.
          </div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {data.subreddits.map((sub) => {
              const url = withOc ? sub.submit_url_with_oc : sub.submit_url;
              return (
                <a
                  key={sub.name}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="fp-btn-ghost"
                  style={{
                    padding: "8px 14px",
                    fontSize: 13,
                    textDecoration: "none",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  title={`Open r/${sub.name} submit page in a new tab`}
                >
                  <span style={{ color: "var(--text-dim)" }}>r/</span>
                  <strong style={{ fontWeight: 500 }}>{sub.name}</strong>
                  <span style={{ color: "var(--text-fade)", fontSize: 11 }}>↗</span>
                </a>
              );
            })}
          </div>
        )}
        <div style={{ fontSize: 11, color: "var(--text-fade)", lineHeight: 1.5 }}>
          Each link opens that subreddit's submit page in a new tab with the title pre-filled.
          Drag the downloaded image onto the form, review the rules in the sidebar, and post.
        </div>
      </div>
    </div>
  );
}
