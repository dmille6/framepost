import { useMemo, useState, type DragEvent } from "react";

import type { ScheduledItem } from "../api/client";
import { thumbnailUrl } from "../api/client";

const DRAG_MIME = "application/x-framepost-post-id";

type Props = {
  month: Date;                       // first day of month, local
  items: ScheduledItem[];
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
  onPick: (item: ScheduledItem) => void;
  onDayDrop?: (date: Date, postId: string) => void;
};

export default function Calendar({ month, items, onPrev, onNext, onToday, onPick, onDayDrop }: Props) {
  const days = useMemo(() => buildMonthGrid(month), [month]);
  const itemsByDay = useMemo(() => groupByDay(items), [items]);
  const [hoverDayKey, setHoverDayKey] = useState<string | null>(null);

  const monthLabel = month.toLocaleString(undefined, { month: "long", year: "numeric" });
  const today = new Date();
  const todayKey = dayKey(today);
  const todayWeekday = today.getDay(); // 0=Sun..6=Sat
  const dayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  return (
    <div className="fp-card" style={{ padding: 0, overflow: "hidden" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 20px",
          borderBottom: "0.5px solid var(--border)",
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 500 }}>{monthLabel}</div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="fp-btn-ghost" onClick={onPrev}>‹</button>
          <button className="fp-btn-ghost" onClick={onToday}>Today</button>
          <button className="fp-btn-ghost" onClick={onNext}>›</button>
        </div>
      </div>

      {/*
        Single grid for both header labels (7 cells) and the 42 day cells. Sharing one grid
        container guarantees pixel-perfect column alignment — separate grids could drift
        even with identical `repeat(7, 1fr)` if their content boxes differ in width.
      */}
      <div
        style={{
          display: "grid",
          // minmax(0, 1fr) prevents 128-px thumbnails inside cells from forcing the grid wider
          // than its container — without this, narrow viewports clip the rightmost column.
          gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
        }}
      >
        {dayLabels.map((d, idx) => {
          const isTodayCol = idx === todayWeekday;
          return (
            <div
              key={`h-${d}`}
              style={{
                fontSize: 11,
                padding: "8px 0 6px 10px",
                background: "var(--bg)",
                color: isTodayCol ? "var(--teal)" : "var(--text-dim)",
                fontWeight: isTodayCol ? 600 : 400,
                borderBottom: "0.5px solid var(--border)",
              }}
            >
              {d}
            </div>
          );
        })}
        {days.map((d, i) => {
          const isThisMonth = d.getMonth() === month.getMonth();
          const k = dayKey(d);
          const dayItems = itemsByDay.get(k) ?? [];
          const visible = dayItems.slice(0, 4);
          const overflow = dayItems.length - visible.length;
          const isHover = hoverDayKey === k;
          const dropHandlers = onDayDrop
            ? {
                onDragOver: (e: DragEvent<HTMLDivElement>) => {
                  if (e.dataTransfer.types.includes(DRAG_MIME)) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = "move";
                    setHoverDayKey(k);
                  }
                },
                onDragLeave: () => setHoverDayKey(null),
                onDrop: (e: DragEvent<HTMLDivElement>) => {
                  e.preventDefault();
                  setHoverDayKey(null);
                  const postId = e.dataTransfer.getData(DRAG_MIME);
                  if (postId) onDayDrop(d, postId);
                },
              }
            : {};
          const isToday = k === todayKey;
          return (
            <div
              key={i}
              {...dropHandlers}
              style={{
                minHeight: 280,
                padding: 10,
                borderRight: i % 7 === 6 ? "none" : "0.5px solid var(--border)",
                borderBottom: "0.5px solid var(--border)",
                background: isHover
                  ? "rgba(93,202,165,0.08)"
                  : isToday
                    ? "rgba(93,202,165,0.05)"
                    : isThisMonth
                      ? "transparent"
                      : "var(--bg)",
                opacity: isThisMonth ? 1 : 0.5,
                outline: isHover
                  ? "1.5px dashed var(--teal)"
                  : isToday
                    ? "1px solid rgba(93,202,165,0.4)"
                    : "none",
                outlineOffset: -1,
                position: "relative",
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  marginBottom: 8,
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  color: isToday ? "var(--teal)" : "var(--text-dim)",
                  fontWeight: isToday ? 600 : 400,
                }}
              >
                {isToday ? (
                  <span
                    style={{
                      display: "inline-grid",
                      placeItems: "center",
                      width: 22,
                      height: 22,
                      borderRadius: 999,
                      background: "var(--teal)",
                      color: "#0a1f17",
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    {d.getDate()}
                  </span>
                ) : (
                  <span>{d.getDate()}</span>
                )}
                {isToday && (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 600,
                      letterSpacing: "0.06em",
                      textTransform: "uppercase",
                      color: "var(--teal)",
                    }}
                  >
                    Today · {dayLabels[d.getDay()]}
                  </span>
                )}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {visible.map((it) => (
                  <button
                    key={it.id}
                    onClick={() => onPick(it)}
                    title={`${formatLocalTime(it.scheduled_at)} · ${it.title || it.original_filename || "(untitled)"}`}
                    style={{
                      // Thumbnails fluid up to 128px so narrow columns shrink them rather
                      // than overflow. The aspect ratio + objectFit:cover keep them square.
                      width: "100%",
                      maxWidth: 128,
                      aspectRatio: "1 / 1",
                      padding: 0,
                      border: `2px solid ${ringColor(it.status)}`,
                      borderRadius: 10,
                      cursor: "pointer",
                      overflow: "hidden",
                      background: "#0a0a0a",
                    }}
                  >
                    <img
                      src={thumbnailUrl(it.id)}
                      alt=""
                      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                    />
                  </button>
                ))}
                {overflow > 0 && (
                  <div
                    style={{
                      width: "100%",
                      maxWidth: 128,
                      aspectRatio: "1 / 1",
                      borderRadius: 10,
                      background: "var(--hover)",
                      color: "var(--text-dim)",
                      display: "grid",
                      placeItems: "center",
                      fontSize: 16,
                    }}
                  >
                    +{overflow}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function buildMonthGrid(month: Date): Date[] {
  const start = new Date(month.getFullYear(), month.getMonth(), 1);
  const startWeekday = start.getDay();
  const first = new Date(start);
  first.setDate(1 - startWeekday);
  const out: Date[] = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(first);
    d.setDate(first.getDate() + i);
    out.push(d);
  }
  return out;
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function groupByDay(items: ScheduledItem[]): Map<string, ScheduledItem[]> {
  const m = new Map<string, ScheduledItem[]>();
  for (const it of items) {
    if (!it.scheduled_at) continue;
    const local = new Date(it.scheduled_at + "Z");
    const k = dayKey(local);
    const arr = m.get(k) ?? [];
    arr.push(it);
    m.set(k, arr);
  }
  return m;
}

function formatLocalTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso + "Z").toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export const CALENDAR_DRAG_MIME = DRAG_MIME;

function ringColor(status: string): string {
  switch (status) {
    case "posted": return "rgba(122,220,177,0.7)";  // teal
    case "late": return "rgba(240,201,122,0.7)";    // amber
    case "missed":
    case "failed": return "rgba(245,156,156,0.7)";  // red
    default: return "rgba(255,255,255,0.15)";       // pending — subtle
  }
}
