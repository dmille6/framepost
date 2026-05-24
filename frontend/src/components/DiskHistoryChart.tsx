import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { type DiskSamplePoint, type DiskUsage, fetchDiskHistory } from "../api/client";

type WindowKey = "24h" | "7d" | "30d";
const WINDOWS: Record<WindowKey, { hours: number; label: string }> = {
  "24h": { hours: 24, label: "24h" },
  "7d": { hours: 24 * 7, label: "7d" },
  "30d": { hours: 24 * 30, label: "30d" },
};

const W = 720;
const H = 140;
const PAD_L = 56;
const PAD_R = 12;
const PAD_T = 12;
const PAD_B = 22;

export default function DiskHistoryChart({ current }: { current: DiskUsage }) {
  const [win, setWin] = useState<WindowKey>("7d");
  const { data: samples = [] } = useQuery({
    queryKey: ["disk-history", win],
    queryFn: () => fetchDiskHistory(WINDOWS[win].hours),
    refetchInterval: 60_000,
  });

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>Free space over time</div>
        <div style={{ display: "flex", gap: 4 }}>
          {(Object.keys(WINDOWS) as WindowKey[]).map((k) => (
            <button
              key={k}
              className={win === k ? "fp-btn" : "fp-btn-ghost"}
              onClick={() => setWin(k)}
              style={{ padding: "4px 10px", fontSize: 12 }}
            >
              {WINDOWS[k].label}
            </button>
          ))}
        </div>
      </div>
      {samples.length < 2 ? (
        <div
          style={{
            color: "var(--text-fade)",
            fontSize: 12,
            padding: 16,
            background: "var(--bg)",
            borderRadius: 8,
            textAlign: "center",
          }}
        >
          Collecting data — the worker samples every 5 min. Come back shortly.
        </div>
      ) : (
        <Chart samples={samples} current={current} />
      )}
    </div>
  );
}

function Chart({ samples, current }: { samples: DiskSamplePoint[]; current: DiskUsage }) {
  const [hover, setHover] = useState<{ p: DiskSamplePoint; x: number; y: number } | null>(null);

  const series = useMemo(
    () => samples.map((s) => ({ ...s, t: new Date(s.sampled_at + "Z").getTime(), gb: s.free_bytes / 1024 ** 3 })),
    [samples],
  );

  const tMin = series[0].t;
  const tMax = series[series.length - 1].t;
  const totalGB = current.total_bytes / 1024 ** 3;
  // Y axis: 0 to total disk capacity, but clamp the bottom of the visible area to 0.
  const yMin = 0;
  const yMax = totalGB;

  function xOf(t: number) {
    if (tMax === tMin) return PAD_L + (W - PAD_L - PAD_R) / 2;
    return PAD_L + ((t - tMin) / (tMax - tMin)) * (W - PAD_L - PAD_R);
  }
  function yOf(gb: number) {
    return PAD_T + (1 - (gb - yMin) / (yMax - yMin)) * (H - PAD_T - PAD_B);
  }

  const points = series.map((s) => `${xOf(s.t).toFixed(1)},${yOf(s.gb).toFixed(1)}`).join(" ");
  const area =
    `M${xOf(series[0].t).toFixed(1)},${yOf(yMin).toFixed(1)} ` +
    series.map((s) => `L${xOf(s.t).toFixed(1)},${yOf(s.gb).toFixed(1)}`).join(" ") +
    ` L${xOf(series[series.length - 1].t).toFixed(1)},${yOf(yMin).toFixed(1)} Z`;

  // Reference line: hard-stop. Above the line = above the threshold (safe).
  const hardstopY = yOf(current.hardstop_gb);

  // Y-axis ticks at 0, 25, 50, 75, 100% of total.
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * totalGB);

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const xPx = ((e.clientX - rect.left) / rect.width) * W;
    if (xPx < PAD_L || xPx > W - PAD_R) {
      setHover(null);
      return;
    }
    // Find nearest sample
    let best = series[0];
    let bestDx = Math.abs(xOf(series[0].t) - xPx);
    for (const s of series) {
      const dx = Math.abs(xOf(s.t) - xPx);
      if (dx < bestDx) {
        bestDx = dx;
        best = s;
      }
    }
    setHover({ p: best, x: xOf(best.t), y: yOf(best.gb) });
  }

  return (
    <div style={{ position: "relative", background: "var(--bg)", borderRadius: 8, padding: 8 }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="none"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {/* Y grid + labels */}
        {yTicks.map((gb, i) => (
          <g key={i}>
            <line
              x1={PAD_L}
              x2={W - PAD_R}
              y1={yOf(gb)}
              y2={yOf(gb)}
              stroke="rgba(255,255,255,0.06)"
              strokeWidth={1}
            />
            <text
              x={PAD_L - 6}
              y={yOf(gb) + 3}
              textAnchor="end"
              fontSize="10"
              fill="rgba(255,255,255,0.4)"
            >
              {gb.toFixed(0)} GB
            </text>
          </g>
        ))}
        {/* Hard-stop reference line */}
        {current.hardstop_gb >= yMin && current.hardstop_gb <= yMax && (
          <g>
            <line
              x1={PAD_L}
              x2={W - PAD_R}
              y1={hardstopY}
              y2={hardstopY}
              stroke="rgba(245,156,156,0.6)"
              strokeDasharray="3,3"
              strokeWidth={1}
            />
            <text
              x={W - PAD_R - 4}
              y={hardstopY - 4}
              textAnchor="end"
              fontSize="10"
              fill="rgba(245,156,156,0.7)"
            >
              hard-stop {current.hardstop_gb} GB
            </text>
          </g>
        )}
        {/* Area + line */}
        <path d={area} fill="rgba(93,202,165,0.18)" />
        <polyline points={points} fill="none" stroke="var(--teal)" strokeWidth={1.5} />
        {/* Hover */}
        {hover && (
          <g pointerEvents="none">
            <line
              x1={hover.x}
              x2={hover.x}
              y1={PAD_T}
              y2={H - PAD_B}
              stroke="rgba(255,255,255,0.2)"
              strokeWidth={1}
            />
            <circle cx={hover.x} cy={hover.y} r={3.5} fill="var(--teal)" />
          </g>
        )}
      </svg>
      {hover && (
        <div
          style={{
            position: "absolute",
            left: `${(hover.x / W) * 100}%`,
            top: 8,
            transform: "translateX(-50%)",
            background: "var(--card)",
            border: "0.5px solid var(--border-strong)",
            padding: "4px 8px",
            borderRadius: 6,
            fontSize: 11,
            color: "var(--text-dim)",
            pointerEvents: "none",
            whiteSpace: "nowrap",
          }}
        >
          {new Date(hover.p.sampled_at + "Z").toLocaleString()} ·{" "}
          <span style={{ color: "var(--teal)" }}>
            {(hover.p.free_bytes / 1024 ** 3).toFixed(2)} GB free
          </span>
        </div>
      )}
    </div>
  );
}
