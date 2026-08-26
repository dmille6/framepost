import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAccountTrend,
  fetchAnalyticsOverview,
  fetchCollabLift,
  type CollabLift,
  fetchLeaderboard,
  fetchPlatformSummaries,
  fetchTopPostsV2,
  type AccountPoint,
  fetchBestTimes,
  fetchGroupStats,
  fetchTagStats,
  fetchTopPosts,
  thumbnailUrl,
  triggerEngagementSync,
  type TimeSlot,
} from "../api/client";
import PageHeader, { CardHeader } from "../components/PageHeader";
import Topbar from "../components/Topbar";
import { usePageTitle } from "../hooks/usePageTitle";
import { absoluteTime, relativeTime } from "../lib/time";

const DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function Analytics() {
  usePageTitle("Analytics");
  const qc = useQueryClient();
  // Cross-platform controls. `window` compares posts at the same AGE (a January post
  // has had six more months to accumulate than a July one), `platform` narrows the lens.
  const [platform, setPlatform] = useState<string | null>(null);
  const [window_, setWindow] = useState<string | null>(null);
  const [dimension, setDimension] = useState("performer");

  const { data: platforms = [] } = useQuery({
    queryKey: ["analytics-platforms", window_],
    queryFn: () => fetchPlatformSummaries(window_),
  });
  const { data: board = [] } = useQuery({
    queryKey: ["analytics-leaderboard", dimension, platform, window_],
    queryFn: () => fetchLeaderboard(dimension, platform, window_),
  });
  // Lift only makes sense at a fixed post age, so fall back to 7d when the page is
  // showing lifetime numbers.
  const { data: collab } = useQuery({
    queryKey: ["collab-lift", window_],
    queryFn: () => fetchCollabLift(window_ && window_ !== "lifetime" ? window_ : "7d"),
  });

  const { data: trend = [] } = useQuery({
    queryKey: ["analytics-account-trend"],
    queryFn: () => fetchAccountTrend("instagram", 90),
  });
  const { data: topV2 = [] } = useQuery({
    queryKey: ["analytics-top-v2", platform, window_],
    queryFn: () => fetchTopPostsV2(platform, window_),
  });

  const { data: overview } = useQuery({ queryKey: ["analytics-overview"], queryFn: fetchAnalyticsOverview });
  const { data: bestTimes = [] } = useQuery({ queryKey: ["analytics-best-times"], queryFn: fetchBestTimes });
  const { data: groupStats = [] } = useQuery({ queryKey: ["analytics-groups"], queryFn: fetchGroupStats });
  const { data: tagStats = [] } = useQuery({ queryKey: ["analytics-tags"], queryFn: () => fetchTagStats(1, 30) });
  const [topSort, setTopSort] = useState<"faves" | "views" | "comments">("faves");
  const { data: topPosts = [] } = useQuery({
    queryKey: ["analytics-top", topSort],
    queryFn: () => fetchTopPosts(topSort, 10),
  });

  const sync = useMutation({
    mutationFn: triggerEngagementSync,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["analytics-overview"] });
      void qc.invalidateQueries({ queryKey: ["analytics-best-times"] });
      void qc.invalidateQueries({ queryKey: ["analytics-groups"] });
      void qc.invalidateQueries({ queryKey: ["analytics-tags"] });
      void qc.invalidateQueries({ queryKey: ["analytics-top", topSort] });
    },
  });

  return (
    <>
      <Topbar />
      <div className="fp-page fp-fade-in">
        <PageHeader
          title="Analytics"
          subtitle={
            overview?.last_sync ? (
              <span title={absoluteTime(overview.last_sync)}>
                Last synced {relativeTime(overview.last_sync)}
              </span>
            ) : "Engagement data updates daily — or sync on demand"
          }
          actions={
            <button
              className="fp-btn-ghost"
              onClick={() => sync.mutate()}
              disabled={sync.isPending}
            >
              {sync.isPending && <span className="fp-spinner" />}
              {sync.isPending ? "Syncing" : "Sync now"}
            </button>
          }
        />

        {/* ---- Cross-platform view (engagement_snapshots) ---------------------- */}
        <div className="fp-card" style={{ marginBottom: 16 }}>
          <CardHeader
            title="All platforms"
            subtitle="Medians, not totals — one viral frame shouldn't set the bar. A dash means the platform doesn't report that metric."
            action={
              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <Segmented
                  value={window_ ?? "lifetime"}
                  onChange={(v) => setWindow(v === "lifetime" ? null : v)}
                  options={[
                    { value: "lifetime", label: "Lifetime" },
                    { value: "24h", label: "24h" },
                    { value: "48h", label: "48h" },
                    { value: "7d", label: "7d" },
                  ]}
                />
              </div>
            }
          />
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 620 }}>
              <thead>
                <tr>
                  {["Platform", "Posts", "Median score", "Likes", "Comments", "Reach", "Saves/1k", "Visits/1k"].map((h, i) => (
                    <th key={h} style={{ textAlign: i === 0 ? "left" : "right", padding: "6px 10px", fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-fade)", borderBottom: "0.5px solid var(--border)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {platforms.map((p) => (
                  <tr key={p.platform} style={{ cursor: "pointer", background: platform === p.platform ? "var(--teal-tint)" : "transparent" }}
                      onClick={() => setPlatform(platform === p.platform ? null : p.platform)}
                      title="Click to filter the sections below to this platform">
                    <td style={{ padding: "8px 10px", borderBottom: "0.5px solid var(--border)", fontWeight: 500, textTransform: "capitalize" }}>
                      {p.platform}
                      {p.low_sample && p.posts > 0 && (
                        <span title="Fewer than 5 posts — treat as provisional" style={{ marginLeft: 6, fontSize: 10, color: "var(--amber, #e0b268)" }}>· thin data</span>
                      )}
                    </td>
                    <Num v={p.posts} />
                    <Num v={p.median_quality} strong />
                    <Num v={p.median_likes} />
                    <Num v={p.median_comments} />
                    <Num v={p.median_reach} />
                    <Num v={p.saves_per_1k} />
                    <Num v={p.visits_per_1k} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {platform && (
            <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 8 }}>
              Filtered to <strong style={{ textTransform: "capitalize" }}>{platform}</strong> —{" "}
              <button className="fp-link" style={{ fontSize: 11.5 }} onClick={() => setPlatform(null)}>show all</button>
            </div>
          )}
        </div>

        {/* ---- Who / where earns engagement ------------------------------------ */}
        <div className="fp-card" style={{ marginBottom: 16 }}>
          <CardHeader
            title="What earns engagement"
            subtitle="Lift compares each group's median post against a typical post — 1.4 means 40% better. Thin rows sort last."
            action={
              <Segmented
                value={dimension}
                onChange={setDimension}
                options={[
                  { value: "performer", label: "Performers" },
                  { value: "venue", label: "Venues" },
                  { value: "show", label: "Shows" },
                  { value: "city", label: "Cities" },
                ]}
              />
            }
          />
          {board.length === 0 ? (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-fade)", fontSize: 13 }}>
              No tagged {dimension}s with engagement yet.
            </div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 560 }}>
                <thead>
                  <tr>
                    {["", "Posts", "Median score", "Lift", "Saves/1k", "Comments/1k"].map((h, i) => (
                      <th key={h + i} style={{ textAlign: i === 0 ? "left" : "right", padding: "6px 10px", fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-fade)", borderBottom: "0.5px solid var(--border)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {board.map((r) => (
                    <tr key={r.key} style={{ opacity: r.low_sample ? 0.6 : 1 }}>
                      <td style={{ padding: "8px 10px", borderBottom: "0.5px solid var(--border)" }}>
                        {r.label}
                        {r.low_sample && <span title="Fewer than 5 posts" style={{ marginLeft: 6, fontSize: 10, color: "var(--text-fade)" }}>· thin</span>}
                      </td>
                      <Num v={r.posts} />
                      <Num v={r.median_quality} strong />
                      <td style={{ padding: "8px 10px", borderBottom: "0.5px solid var(--border)", textAlign: "right", fontVariantNumeric: "tabular-nums",
                                   color: r.lift == null ? "var(--text-fade)" : r.lift >= 1.2 ? "var(--teal)" : r.lift < 0.8 ? "var(--text-dim)" : "inherit" }}>
                        {r.lift == null ? "—" : `${r.lift}x`}
                      </td>
                      <Num v={r.saves_per_1k} />
                      <Num v={r.comments_per_1k} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ---- What worked ------------------------------------------------------ */}
        {topV2.length > 0 && (
          <div className="fp-card" style={{ marginBottom: 16 }}>
            <CardHeader
              title="What worked"
              subtitle="Ranked by the platform's own quality score — saves, shares and follows outweigh likes, because they cost the viewer something."
            />
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 660 }}>
                <thead>
                  <tr>
                    {["Post", "Platform", "Score", "Likes", "Comments", "Reach", "Saves", "Visits"].map((h, i) => (
                      <th key={h} style={{ textAlign: i < 2 ? "left" : "right", padding: "6px 10px", fontSize: 10.5, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--text-fade)", borderBottom: "0.5px solid var(--border)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {topV2.map((p) => (
                    <tr key={`${p.post_id}-${p.platform}`}>
                      <td style={{ padding: "8px 10px", borderBottom: "0.5px solid var(--border)", maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          title={p.title ?? ""}>
                        {p.flickr_url ? (
                          <a href={p.flickr_url} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>{p.title || "(untitled)"}</a>
                        ) : (p.title || "(untitled)")}
                      </td>
                      <td style={{ padding: "8px 10px", borderBottom: "0.5px solid var(--border)", textTransform: "capitalize", color: "var(--text-dim)" }}>{p.platform}</td>
                      <Num v={p.quality} strong />
                      <Num v={p.likes} />
                      <Num v={p.comments} />
                      <Num v={p.reach} />
                      <Num v={p.saves} />
                      <Num v={p.profile_visits} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ---- Instagram audience ---------------------------------------------- */}
        {trend.length > 0 && <AccountTrendCard points={trend} />}
        {collab && collab.collab_posts > 0 && <CollabLiftCard data={collab} />}

        {overview && (
          <div className="fp-card" style={{ marginBottom: 16 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
              {[
                { l: "Posts tracked", v: overview.posts_with_engagement.toLocaleString() },
                { l: "Total views", v: overview.total_views.toLocaleString() },
                { l: "Total faves", v: overview.total_faves.toLocaleString() },
                { l: "Total comments", v: overview.total_comments.toLocaleString() },
              ].map((c) => (
                <div key={c.l} style={{ background: "var(--bg)", padding: "14px 16px", borderRadius: 10, border: "0.5px solid var(--border)" }}>
                  <div style={{ fontSize: 11, color: "var(--text-fade)", textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 500 }}>{c.l}</div>
                  <div style={{ fontSize: 24, fontWeight: 600, marginTop: 6, letterSpacing: "-0.02em" }}>{c.v}</div>
                </div>
              ))}
            </div>
            {overview.posts_with_engagement === 0 && (
              <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-fade)" }}>
                No engagement data yet — happens automatically on the daily sync (04:00 UTC), or
                click "Sync now" once you have posted photos showing on Flickr.
              </div>
            )}
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <BestTimesHeatmap slots={bestTimes} />
          <DayOfWeekChart slots={bestTimes} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          <TagsTable rows={tagStats} />
          <GroupsTable rows={groupStats} />
        </div>

        <TopPostsCard
          rows={topPosts}
          sort={topSort}
          onSort={setTopSort}
        />
      </div>
    </>
  );
}

function BestTimesHeatmap({ slots }: { slots: TimeSlot[] }) {
  const grid = new Map<string, TimeSlot>();
  for (const s of slots) grid.set(`${s.dow}-${s.hour}`, s);
  const maxFaves = Math.max(0.1, ...slots.map((s) => s.avg_faves));

  return (
    <div className="fp-card">
      <CardHeader
        title="Best times to post"
        subtitle="Average faves per post, by hour-of-day × day-of-week. Sparser cells = fewer historical posts."
      />
      <div style={{ overflow: "auto" }}>
        <div style={{ display: "grid", gridTemplateColumns: "auto repeat(24, 1fr)", gap: 1, fontSize: 9 }}>
          <div />
          {Array.from({ length: 24 }, (_, h) => (
            <div key={h} style={{ color: "var(--text-fade)", textAlign: "center" }}>
              {h % 3 === 0 ? h : ""}
            </div>
          ))}
          {DOW_LABELS.map((label, dow) => (
            <div key={dow} style={{ display: "contents" }}>
              <div style={{ color: "var(--text-fade)", paddingRight: 6 }}>{label}</div>
              {Array.from({ length: 24 }, (_, hour) => {
                const slot = grid.get(`${dow}-${hour}`);
                const intensity = slot ? slot.avg_faves / maxFaves : 0;
                const bg = slot
                  ? `rgba(93,202,165,${0.15 + intensity * 0.85})`
                  : "rgba(255,255,255,0.03)";
                return (
                  <div
                    key={hour}
                    title={
                      slot
                        ? `${DOW_LABELS[dow]} ${hour}:00 · ${slot.posts} post${slot.posts === 1 ? "" : "s"} · avg ${slot.avg_faves.toFixed(1)} faves, ${slot.avg_views.toFixed(0)} views`
                        : `${DOW_LABELS[dow]} ${hour}:00 · no posts`
                    }
                    style={{ height: 22, background: bg, borderRadius: 2 }}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function DayOfWeekChart({ slots }: { slots: TimeSlot[] }) {
  const byDow = new Map<number, { posts: number; faves: number }>();
  for (const s of slots) {
    const b = byDow.get(s.dow) ?? { posts: 0, faves: 0 };
    b.posts += s.posts;
    b.faves += s.avg_faves * s.posts;
    byDow.set(s.dow, b);
  }
  const data = DOW_LABELS.map((label, dow) => {
    const b = byDow.get(dow);
    return {
      label,
      posts: b?.posts ?? 0,
      avg: b && b.posts > 0 ? b.faves / b.posts : 0,
    };
  });
  const maxAvg = Math.max(0.1, ...data.map((d) => d.avg));
  return (
    <div className="fp-card">
      <CardHeader
        title="By day of week"
        subtitle="Average faves across all posts that day, regardless of hour."
      />
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", rowGap: 6, columnGap: 8, alignItems: "center", fontSize: 12 }}>
        {data.map((d) => (
          <div key={d.label} style={{ display: "contents" }}>
            <div style={{ color: "var(--text-dim)" }}>{d.label}</div>
            <div style={{ height: 14, background: "rgba(255,255,255,0.04)", borderRadius: 3, position: "relative" }}>
              <div
                style={{
                  width: `${(d.avg / maxAvg) * 100}%`,
                  height: "100%",
                  background: "var(--teal)",
                  borderRadius: 3,
                  opacity: d.posts > 0 ? 1 : 0.3,
                }}
              />
            </div>
            <div style={{ color: "var(--text-fade)", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
              {d.posts > 0 ? `${d.avg.toFixed(1)} (${d.posts})` : "—"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function TagsTable({ rows }: { rows: { tag: string; posts: number; avg_views: number; avg_faves: number; avg_comments: number }[] }) {
  return (
    <div className="fp-card">
      <CardHeader title="Tags by avg faves" />
      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-fade)" }}>No tag data yet.</div>
      ) : (
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "var(--text-fade)", textAlign: "left" }}>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)" }}>Tag</th>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right" }}>Posts</th>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right" }}>Avg faves</th>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right" }}>Avg views</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 20).map((r) => (
              <tr key={r.tag}>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)" }}>{r.tag}</td>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right", color: "var(--text-dim)" }}>{r.posts}</td>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right", color: "var(--teal)", fontVariantNumeric: "tabular-nums" }}>{r.avg_faves.toFixed(1)}</td>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right", color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>{r.avg_views.toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function GroupsTable({ rows }: { rows: { group_id: string; name: string; category: string | null; submissions: number; accepted: number; failed: number; avg_faves: number; avg_views: number }[] }) {
  return (
    <div className="fp-card">
      <CardHeader title="Groups by avg faves" />
      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-fade)" }}>No group submissions yet.</div>
      ) : (
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ color: "var(--text-fade)", textAlign: "left" }}>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)" }}>Group</th>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right" }}>Sent</th>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right" }}>OK</th>
              <th style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right" }}>Avg faves</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.group_id}>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)" }}>
                  {r.name}
                  {r.category && (
                    <div style={{ fontSize: 10, color: "var(--text-fade)" }}>{r.category}</div>
                  )}
                </td>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right", color: "var(--text-dim)" }}>{r.submissions}</td>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right", color: r.accepted ? "var(--teal)" : "var(--text-dim)" }}>{r.accepted}</td>
                <td style={{ padding: "6px 0", borderBottom: "0.5px solid var(--border)", textAlign: "right", color: "var(--teal)", fontVariantNumeric: "tabular-nums" }}>{r.avg_faves.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TopPostsCard({
  rows,
  sort,
  onSort,
}: {
  rows: { post_id: string; title: string | null; flickr_url: string | null; posted_at: string | null; views: number; faves: number; comments: number }[];
  sort: "views" | "faves" | "comments";
  onSort: (s: "views" | "faves" | "comments") => void;
}) {
  return (
    <div className="fp-card">
      <CardHeader
        title="Top performers"
        action={
          <select
            className="fp-select"
            value={sort}
            onChange={(e) => onSort(e.target.value as "views" | "faves" | "comments")}
            style={{ width: 160 }}
          >
            <option value="faves">By faves</option>
            <option value="views">By views</option>
            <option value="comments">By comments</option>
          </select>
        }
      />
      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-fade)" }}>No engagement data yet.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 12 }}>
          {rows.map((r) => (
            <a
              key={r.post_id}
              href={r.flickr_url ?? "#"}
              target="_blank"
              rel="noreferrer"
              style={{ textDecoration: "none", color: "inherit" }}
            >
              <div style={{ background: "var(--bg)", borderRadius: 8, overflow: "hidden", border: "0.5px solid var(--border)" }}>
                <div style={{ aspectRatio: "1 / 1", background: "#0a0a0a" }}>
                  <img src={thumbnailUrl(r.post_id)} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                </div>
                <div style={{ padding: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.title || "(untitled)"}
                  </div>
                  <div style={{ display: "flex", gap: 8, fontSize: 11, marginTop: 6, color: "var(--text-dim)" }}>
                    <span>♥ {r.faves}</span>
                    <span>👁 {r.views}</span>
                    <span>💬 {r.comments}</span>
                  </div>
                </div>
              </div>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}


/** Right-aligned numeric cell. Renders an em-dash for null so a metric the platform
 *  doesn't report never reads as a zero. */
function Num({ v, strong }: { v: number | null | undefined; strong?: boolean }) {
  return (
    <td
      style={{
        padding: "8px 10px",
        borderBottom: "0.5px solid var(--border)",
        textAlign: "right",
        fontVariantNumeric: "tabular-nums",
        fontWeight: strong ? 600 : 400,
        color: v == null ? "var(--text-fade)" : "inherit",
      }}
    >
      {v == null ? "—" : typeof v === "number" ? v.toLocaleString() : v}
    </td>
  );
}

function Segmented({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div style={{ display: "inline-flex", border: "0.5px solid var(--border-strong)", borderRadius: 8, overflow: "hidden" }}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            style={{
              background: active ? "var(--hover)" : "transparent",
              color: active ? "var(--text)" : "var(--text-dim)",
              border: 0,
              padding: "5px 11px",
              fontSize: 12,
              fontWeight: active ? 500 : 400,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Follower + reach trend. A sparkline rather than a chart library — the shape and
 *  the endpoints are what matter here. */
function AccountTrendCard({ points }: { points: AccountPoint[] }) {
  const followers = points.filter((p) => p.followers != null);
  const first = followers[0]?.followers ?? null;
  const last = followers[followers.length - 1]?.followers ?? null;
  const delta = first != null && last != null ? last - first : null;
  const reach = points.filter((p) => p.reach != null).map((p) => p.reach as number);
  const maxReach = Math.max(1, ...reach);

  return (
    <div className="fp-card" style={{ marginBottom: 16 }}>
      <CardHeader
        title="Instagram audience"
        subtitle={`Daily account stats · ${points.length} day${points.length === 1 ? "" : "s"} recorded`}
      />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 12, marginBottom: 14 }}>
        {[
          { l: "Followers", v: last?.toLocaleString() ?? "—", sub: delta != null ? `${delta >= 0 ? "+" : ""}${delta} in ${points.length}d` : null },
          { l: "Reach (latest day)", v: points[points.length - 1]?.reach?.toLocaleString() ?? "—", sub: null },
          { l: "Profile views", v: points[points.length - 1]?.profile_views?.toLocaleString() ?? "—", sub: null },
          { l: "Accounts engaged", v: points[points.length - 1]?.accounts_engaged?.toLocaleString() ?? "—", sub: null },
        ].map((c) => (
          <div key={c.l} style={{ background: "var(--bg)", padding: "12px 14px", borderRadius: 10, border: "0.5px solid var(--border)" }}>
            <div style={{ fontSize: 10.5, color: "var(--text-fade)", textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 500 }}>{c.l}</div>
            <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4, letterSpacing: "-0.02em" }}>{c.v}</div>
            {c.sub && <div style={{ fontSize: 11, color: "var(--teal)", marginTop: 2 }}>{c.sub}</div>}
          </div>
        ))}
      </div>
      {reach.length > 1 && (
        <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 48 }}>
          {points.map((p) => (
            <div
              key={p.date}
              title={`${p.date} · reach ${p.reach ?? "—"} · profile views ${p.profile_views ?? "—"}`}
              style={{
                flex: 1,
                height: `${((p.reach ?? 0) / maxReach) * 100}%`,
                minHeight: 2,
                background: "var(--teal)",
                opacity: 0.55,
                borderRadius: 2,
              }}
            />
          ))}
        </div>
      )}
      {points.length < 7 && (
        <div style={{ fontSize: 11.5, color: "var(--text-fade)", marginTop: 10 }}>
          Collecting baseline — trends get meaningful after a week or two of daily samples.
        </div>
      )}
    </div>
  );
}


function CollabLiftCard({ data }: { data: CollabLift }) {
  const overall =
    data.solo_median_quality && data.collab_median_quality
      ? data.collab_median_quality / data.solo_median_quality
      : null;

  return (
    <div className="fp-card" style={{ marginBottom: 16 }}>
      <CardHeader
        title="Collaborator lift"
        subtitle={`${data.collab_posts} co-authored vs ${data.solo_posts} solo post${
          data.solo_posts === 1 ? "" : "s"
        } · measured at ${data.window}`}
      />
      {/* Say plainly what this is and isn't. Instagram gives us no accept/decline
          signal, and a number that looks like a measurement invites being read as one. */}
      <div
        style={{
          fontSize: 12, color: "var(--text-dim)", lineHeight: 1.5,
          background: "var(--bg)", border: "0.5px solid var(--border)",
          borderRadius: 8, padding: "10px 12px", marginBottom: 14,
        }}
      >
        {data.basis}
      </div>

      {overall != null && (
        <div style={{ fontSize: 13, marginBottom: 12 }}>
          Co-authored posts run{" "}
          <strong style={{ color: overall >= 1 ? "var(--teal)" : "var(--danger)" }}>
            {overall.toFixed(2)}&times;
          </strong>{" "}
          your solo median.
        </div>
      )}

      {data.performers.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--text-fade)" }}>
          No collaborator posts have engagement readings at this age yet.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-fade)", fontSize: 11 }}>
                <th style={{ padding: "6px 8px" }}>Performer</th>
                <th style={{ padding: "6px 8px" }}>Posts</th>
                <th style={{ padding: "6px 8px" }}>Median reach</th>
                <th style={{ padding: "6px 8px" }}>Lift</th>
              </tr>
            </thead>
            <tbody>
              {data.performers.map((r) => (
                <tr key={r.handle} style={{ borderTop: "0.5px solid var(--border)" }}>
                  <td style={{ padding: "8px" }}>
                    <div>{r.display_name}</div>
                    <div style={{ fontSize: 11, color: "var(--text-fade)" }}>
                      @{r.handle}
                      {r.handle_status === "needs_check" && (
                        <span style={{ color: "var(--warn, #d98324)" }}> &#9888; handle needs checking</span>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: "8px" }}>
                    {r.posts}
                    {r.provisional && (
                      <span
                        style={{ fontSize: 11, color: "var(--text-fade)" }}
                        title={`Fewer than ${data.min_sample} posts — treat as provisional`}
                      >
                        {" "}provisional
                      </span>
                    )}
                  </td>
                  <td style={{ padding: "8px" }}>
                    {r.median_reach != null ? r.median_reach.toLocaleString() : "\u2014"}
                  </td>
                  <td style={{ padding: "8px", fontWeight: 600 }}>
                    {r.lift != null ? (
                      <span style={{ color: r.lift >= 1 ? "var(--teal)" : "var(--text-dim)" }}>
                        {r.lift.toFixed(2)}&times;
                      </span>
                    ) : (
                      <span
                        style={{ color: "var(--text-fade)", fontWeight: 400 }}
                        title="Not enough solo posts to compare against"
                      >
                        &#8212;
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
