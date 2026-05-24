import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAnalyticsOverview,
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
