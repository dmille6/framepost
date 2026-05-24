import { useQuery } from "@tanstack/react-query";

import { fetchTrending } from "../api/client";

type Props = {
  currentTags: string;
  onAddTag: (tag: string) => void;
  onAddTags: (tags: string[]) => void;
};

export default function TrendingPanel({ currentTags, onAddTag, onAddTags }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["trending"],
    queryFn: fetchTrending,
    refetchInterval: 5 * 60_000,
  });

  const currentSet = new Set(
    currentTags.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean),
  );

  if (isLoading) return null;
  if (error) {
    return (
      <div
        style={{
          background: "var(--bg)",
          border: "0.5px solid var(--border)",
          borderRadius: 8,
          padding: 12,
          fontSize: 12,
          color: "var(--danger)",
        }}
      >
        Trending fetch failed: {(error as Error).message}
      </div>
    );
  }
  if (!data || data.seeds.length === 0) {
    return (
      <div
        style={{
          background: "var(--bg)",
          border: "0.5px solid var(--border)",
          borderRadius: 8,
          padding: 12,
          fontSize: 12,
          color: "var(--text-fade)",
        }}
      >
        Trending tags are off. Set seed tags in{" "}
        <a href="/settings/profiles">Settings → Tag Profiles</a> to enable.
      </div>
    );
  }
  if (data.tags.length === 0) {
    return (
      <div
        style={{
          background: "var(--bg)",
          border: "0.5px solid var(--border)",
          borderRadius: 8,
          padding: 12,
          fontSize: 12,
          color: "var(--text-fade)",
        }}
      >
        No trending data yet — run a refresh in Settings → Tag Profiles.
      </div>
    );
  }

  // Top half-or-so of tags by score
  const visibleCount = Math.min(data.tags.length, 24);
  const visible = data.tags.slice(0, visibleCount);

  return (
    <div
      style={{
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        padding: 12,
        display: "grid",
        gap: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <div>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Trending on Flickr</span>
          <span style={{ fontSize: 11, color: "var(--text-fade)", marginLeft: 8 }}>
            from: {data.seeds.join(" · ")}
          </span>
        </div>
        {data.last_refresh && (
          <div style={{ fontSize: 11, color: "var(--text-fade)" }}>
            synced {timeAgo(data.last_refresh)}
          </div>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {visible.map((t) => {
          const already = currentSet.has(t.tag.toLowerCase());
          return (
            <button
              key={t.tag}
              onClick={() => !already && onAddTag(t.tag)}
              disabled={already}
              title={`score ${t.score.toFixed(0)} · seeds: ${t.seeds.join(", ")}`}
              style={{
                background: already ? "transparent" : "rgba(120,180,255,0.10)",
                color: already ? "var(--text-fade)" : "var(--text)",
                border: already
                  ? "0.5px dashed var(--border-strong)"
                  : "0.5px solid transparent",
                borderRadius: 999,
                padding: "3px 10px",
                fontSize: 12,
                cursor: already ? "default" : "pointer",
                textDecoration: already ? "line-through" : "none",
              }}
            >
              {t.tag}
              <span style={{ marginLeft: 6, color: "var(--text-fade)", fontSize: 10 }}>
                {Math.round(t.score)}
              </span>
            </button>
          );
        })}
      </div>

      <div>
        <button
          className="fp-link"
          style={{ fontSize: 12 }}
          onClick={() =>
            onAddTags(
              visible
                .filter((t) => !currentSet.has(t.tag.toLowerCase()))
                .slice(0, 5)
                .map((t) => t.tag),
            )
          }
        >
          Add top 5 new
        </button>
      </div>
    </div>
  );
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  return `${d}d ago`;
}
