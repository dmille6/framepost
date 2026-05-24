import { useQuery } from "@tanstack/react-query";

import { fetchPostPlatforms, type PostPlatformStatus } from "../api/client";
import { absoluteTime } from "../lib/time";

const PLATFORM_LABEL: Record<string, string> = {
  flickr: "Flickr",
  bluesky: "Bluesky",
  pixelfed: "Pixelfed",
  mastodon: "Mastodon",
  instagram: "Instagram",
  reddit: "Reddit",
};

const PLATFORM_TINT: Record<string, { bg: string; fg: string; border: string }> = {
  flickr: { bg: "rgba(255, 0, 132, 0.08)", fg: "#ff5fa6", border: "rgba(255, 95, 166, 0.25)" },
  bluesky: { bg: "rgba(80, 138, 255, 0.1)", fg: "#7ba6ff", border: "rgba(123, 166, 255, 0.25)" },
  pixelfed: { bg: "rgba(184, 132, 245, 0.1)", fg: "#c5a3f5", border: "rgba(197, 163, 245, 0.25)" },
  mastodon: { bg: "rgba(99, 100, 255, 0.1)", fg: "#9899ff", border: "rgba(152, 153, 255, 0.25)" },
  // Instagram: pink/magenta (matches the "IG" chip on the Published tile).
  instagram: { bg: "rgba(228, 163, 210, 0.1)", fg: "#e4a3d2", border: "rgba(228, 163, 210, 0.25)" },
  // Reddit: orange (matches the "Reddit" chip on the Published tile).
  reddit: { bg: "rgba(255, 138, 101, 0.1)", fg: "#ff8a65", border: "rgba(255, 138, 101, 0.25)" },
};

export default function PlatformStatusRow({ postId }: { postId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["post-platforms", postId],
    queryFn: () => fetchPostPlatforms(postId),
  });

  if (isLoading || !data || data.length === 0) return null;

  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
      {data.map((p) => (
        <PlatformChip key={p.platform} entry={p} />
      ))}
    </div>
  );
}

function PlatformChip({ entry }: { entry: PostPlatformStatus }) {
  const tint = PLATFORM_TINT[entry.platform] ?? {
    bg: "rgba(255,255,255,0.04)",
    fg: "var(--text-dim)",
    border: "var(--border)",
  };
  const ok = entry.status === "posted" || entry.status === "late";
  const label = PLATFORM_LABEL[entry.platform] ?? entry.platform;
  const title = entry.error_message
    ? `${label}: ${entry.status} — ${entry.error_message}`
    : entry.posted_at
      ? `${label}: posted ${absoluteTime(entry.posted_at)}`
      : `${label}: ${entry.status}`;

  const chip = (
    <span
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "3px 9px 3px 8px",
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 500,
        color: ok ? tint.fg : "var(--danger)",
        background: ok ? tint.bg : "var(--danger-tint)",
        border: `0.5px solid ${ok ? tint.border : "rgba(245, 156, 156, 0.25)"}`,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 999,
          background: ok ? tint.fg : "var(--danger)",
        }}
      />
      {label}
      {!ok && entry.status !== "pending" && (
        <span style={{ fontSize: 10, opacity: 0.8 }}> · {entry.status}</span>
      )}
    </span>
  );

  if (entry.remote_url) {
    return (
      <a href={entry.remote_url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
        {chip}
      </a>
    );
  }
  return chip;
}
