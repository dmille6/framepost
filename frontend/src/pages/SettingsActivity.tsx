import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  type ActivityRow,
  fetchActivity,
  fetchMetrics,
  type MetricsResponse,
} from "../api/client";

const EVENT_TYPES = [
  "imported",
  "edited",
  "scheduled",
  "rescheduled",
  "flickr_uploading",
  "flickr_uploaded",
  "flickr_failed",
  "marked_late",
  "marked_missed",
  "group_submitted",
  "group_rejected",
  "original_purged",
] as const;

export default function SettingsActivity() {
  const [windowDays, setWindowDays] = useState(30);
  const [eventFilter, setEventFilter] = useState<string>("");

  const { data: metrics } = useQuery({
    queryKey: ["metrics", windowDays],
    queryFn: () => fetchMetrics(windowDays),
    refetchInterval: 60_000,
  });
  const { data: activity = [] } = useQuery({
    queryKey: ["activity", eventFilter],
    queryFn: () => fetchActivity({ limit: 100, event_type: eventFilter || undefined }),
    refetchInterval: 30_000,
  });

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <MetricsCards metrics={metrics} windowDays={windowDays} onWindow={setWindowDays} />
      {metrics && metrics.daily.length > 0 && <DailyChart daily={metrics.daily} />}
      <ActivityFeed
        rows={activity}
        eventFilter={eventFilter}
        onFilter={setEventFilter}
      />
    </div>
  );
}

function MetricsCards({
  metrics,
  windowDays,
  onWindow,
}: {
  metrics: MetricsResponse | undefined;
  windowDays: number;
  onWindow: (n: number) => void;
}) {
  const cards = [
    { label: "Imports", value: metrics?.totals.imported ?? 0 },
    { label: "Posted", value: metrics?.totals.posted ?? 0 },
    { label: "Failed", value: metrics?.totals.failed ?? 0 },
    {
      label: "Success rate",
      value: metrics ? `${(metrics.success_rate * 100).toFixed(0)}%` : "—",
      hint:
        metrics && metrics.totals.posted + metrics.totals.failed > 0
          ? `${metrics.totals.posted} / ${metrics.totals.posted + metrics.totals.failed}`
          : "no upload attempts yet",
    },
    {
      label: "Avg upload",
      value:
        metrics && metrics.avg_upload_seconds != null
          ? `${metrics.avg_upload_seconds.toFixed(1)}s`
          : "—",
    },
    {
      label: "Now pending",
      value: metrics?.counts_now.pending ?? 0,
    },
  ];

  return (
    <div className="fp-card" style={{ display: "grid", gap: 12, maxWidth: 960 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Activity overview</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            Rolled-up stats over the last {windowDays} day{windowDays === 1 ? "" : "s"}.
          </div>
        </div>
        <select
          className="fp-select"
          value={windowDays}
          onChange={(e) => onWindow(Number(e.target.value))}
          style={{ width: 140 }}
        >
          <option value={7}>last 7 days</option>
          <option value={30}>last 30 days</option>
          <option value={90}>last 90 days</option>
          <option value={365}>last year</option>
        </select>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 12 }}>
        {cards.map((c) => (
          <div
            key={c.label}
            style={{
              background: "var(--bg)",
              border: "0.5px solid var(--border)",
              borderRadius: 8,
              padding: 12,
            }}
          >
            <div style={{ fontSize: 11, color: "var(--text-fade)", letterSpacing: 0.02 }}>
              {c.label}
            </div>
            <div style={{ fontSize: 22, fontWeight: 500, marginTop: 4 }}>{c.value}</div>
            {c.hint && (
              <div style={{ fontSize: 10, color: "var(--text-fade)", marginTop: 2 }}>{c.hint}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function DailyChart({ daily }: { daily: { day: string; imported: number; posted: number; failed: number }[] }) {
  const W = 960;
  const H = 160;
  const PAD_L = 36;
  const PAD_R = 12;
  const PAD_T = 10;
  const PAD_B = 24;

  const maxY = Math.max(
    1,
    ...daily.map((d) => Math.max(d.imported, d.posted, d.failed)),
  );
  const points = daily.length;
  function xOf(i: number) {
    if (points <= 1) return PAD_L + (W - PAD_L - PAD_R) / 2;
    return PAD_L + (i / (points - 1)) * (W - PAD_L - PAD_R);
  }
  function yOf(v: number) {
    return PAD_T + (1 - v / maxY) * (H - PAD_T - PAD_B);
  }
  const series: { name: string; key: keyof typeof daily[0]; color: string }[] = [
    { name: "Imported", key: "imported", color: "var(--text-dim)" },
    { name: "Posted", key: "posted", color: "var(--teal)" },
    { name: "Failed", key: "failed", color: "var(--danger)" },
  ];

  return (
    <div className="fp-card" style={{ display: "grid", gap: 8, maxWidth: 960 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <div style={{ fontSize: 14, fontWeight: 500 }}>Daily activity</div>
        <div style={{ display: "flex", gap: 12, fontSize: 11 }}>
          {series.map((s) => (
            <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 8, height: 8, background: s.color, borderRadius: 2 }} />
              <span style={{ color: "var(--text-dim)" }}>{s.name}</span>
            </div>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        {[0, 0.5, 1].map((f) => (
          <line
            key={f}
            x1={PAD_L}
            x2={W - PAD_R}
            y1={yOf(maxY * f)}
            y2={yOf(maxY * f)}
            stroke="rgba(255,255,255,0.06)"
            strokeWidth={1}
          />
        ))}
        <text x={PAD_L - 6} y={yOf(maxY) + 3} textAnchor="end" fontSize="9" fill="rgba(255,255,255,0.4)">
          {maxY}
        </text>
        <text x={PAD_L - 6} y={yOf(0) + 3} textAnchor="end" fontSize="9" fill="rgba(255,255,255,0.4)">
          0
        </text>
        {series.map((s) => (
          <polyline
            key={s.name}
            points={daily.map((d, i) => `${xOf(i).toFixed(1)},${yOf(d[s.key] as number).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={s.color}
            strokeWidth={1.5}
          />
        ))}
        {daily.length > 0 && (
          <>
            <text x={PAD_L} y={H - 6} fontSize="9" fill="rgba(255,255,255,0.4)">
              {daily[0].day}
            </text>
            <text x={W - PAD_R} y={H - 6} fontSize="9" fill="rgba(255,255,255,0.4)" textAnchor="end">
              {daily[daily.length - 1].day}
            </text>
          </>
        )}
      </svg>
    </div>
  );
}

const EVENT_LABEL: Record<string, string> = {
  imported: "Imported",
  edited: "Edited",
  scheduled: "Scheduled",
  rescheduled: "Rescheduled",
  flickr_uploading: "Uploading…",
  flickr_uploaded: "Posted to Flickr",
  flickr_failed: "Flickr failure",
  marked_late: "Marked late",
  marked_missed: "Marked missed",
  group_submitted: "Submitted to group",
  group_rejected: "Group rejected",
  original_purged: "Original purged",
};

function ActivityFeed({
  rows,
  eventFilter,
  onFilter,
}: {
  rows: ActivityRow[];
  eventFilter: string;
  onFilter: (s: string) => void;
}) {
  return (
    <div className="fp-card" style={{ display: "grid", gap: 12, maxWidth: 960 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: 14, fontWeight: 500 }}>Recent activity</div>
        <select
          className="fp-select"
          value={eventFilter}
          onChange={(e) => onFilter(e.target.value)}
          style={{ width: 220 }}
        >
          <option value="">All events</option>
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>{EVENT_LABEL[t] ?? t}</option>
          ))}
        </select>
      </div>
      <div
        style={{
          background: "var(--bg)",
          border: "0.5px solid var(--border)",
          borderRadius: 8,
          maxHeight: 480,
          overflow: "auto",
        }}
      >
        {rows.length === 0 ? (
          <div style={{ padding: 24, color: "var(--text-fade)", fontSize: 13, textAlign: "center" }}>
            No events match this filter.
          </div>
        ) : (
          rows.map((r) => <ActivityRowView key={r.id} row={r} />)
        )}
      </div>
    </div>
  );
}

function ActivityRowView({ row }: { row: ActivityRow }) {
  const when = new Date(row.created_at + "Z").toLocaleString();
  const post = row.post_title || row.post_filename || "(unknown)";
  const dotColor =
    row.event_type === "flickr_uploaded"
      ? "var(--teal)"
      : row.event_type === "flickr_failed" || row.event_type === "marked_missed" || row.event_type === "group_rejected"
        ? "var(--danger)"
        : row.event_type === "marked_late"
          ? "#f0c97a"
          : "var(--text-dim)";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr auto",
        gap: 12,
        padding: "8px 14px",
        borderBottom: "0.5px solid var(--border)",
        fontSize: 12,
        alignItems: "baseline",
      }}
    >
      <span style={{ width: 6, height: 6, background: dotColor, borderRadius: 999, marginTop: 6 }} />
      <div>
        <div>
          <span style={{ fontWeight: 500 }}>{EVENT_LABEL[row.event_type] ?? row.event_type}</span>
          <span style={{ color: "var(--text-dim)", marginLeft: 8 }}>· {post}</span>
          <span style={{ color: "var(--text-fade)", marginLeft: 8 }}>by {row.actor}</span>
        </div>
        {row.details && Object.keys(row.details).length > 0 && (
          <div style={{ color: "var(--text-fade)", fontSize: 11, marginTop: 2 }}>
            {Object.entries(row.details).slice(0, 4).map(([k, v]) => (
              <span key={k} style={{ marginRight: 12 }}>
                {k}={typeof v === "object" ? JSON.stringify(v) : String(v)}
              </span>
            ))}
          </div>
        )}
      </div>
      <div style={{ color: "var(--text-fade)", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
        {when}
      </div>
    </div>
  );
}
