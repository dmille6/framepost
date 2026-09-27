import { useMemo, useState } from "react";

import type { ScheduledItem } from "../api/client";
import ScheduledList from "./ScheduledList";
import { groupIntoShoots, postCount, shootDateRange } from "../lib/shoots";

type Props = {
  items: ScheduledItem[];
  onPick: (item: ScheduledItem) => void;
};

/**
 * The queue grouped by shoot instead of by date.
 *
 * The problem this solves: a decrowded queue sits at roughly one post per day, so a
 * 354-post queue spans thirteen months. The calendar shows about thirty of them at a time
 * and the current month, being nearly over, shows two or three; the list shows all 354 as
 * one flat scroll. Neither lets you see a shoot as a shoot.
 *
 * Everything starts collapsed. The point is to replace one 354-row scroll with a page of
 * headings you can read in a glance, so opening a shoot has to be a deliberate act.
 * Expanded groups render through ScheduledList, so rows look and behave exactly as they do
 * in the flat view — one rendering path, and carousels stay folded into a single row.
 */
export default function ScheduledShoots({ items, onPick }: Props) {
  const shoots = useMemo(() => groupIntoShoots(items), [items]);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (items.length === 0) {
    return (
      <div className="fp-card" style={{ padding: 60, textAlign: "center", color: "var(--text-dim)" }}>
        Nothing scheduled in this window.
      </div>
    );
  }

  const totalPosts = postCount(items);
  const allOpen = open.size === shoots.length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
          {shoots.length} {shoots.length === 1 ? "shoot" : "shoots"} · {totalPosts}{" "}
          {totalPosts === 1 ? "post" : "posts"} scheduled
        </div>
        <button
          type="button"
          className="fp-btn fp-btn-ghost"
          onClick={() => setOpen(allOpen ? new Set() : new Set(shoots.map((s) => s.key)))}
        >
          {allOpen ? "Collapse all" : "Expand all"}
        </button>
      </div>

      {shoots.map((s) => {
        const isOpen = open.has(s.key);
        const n = postCount(s.items);
        return (
          <div key={s.key} className="fp-card" style={{ padding: 0, overflow: "hidden" }}>
            <button
              type="button"
              onClick={() => toggle(s.key)}
              aria-expanded={isOpen}
              style={{
                width: "100%",
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "12px 14px",
                background: "none",
                border: "none",
                color: "inherit",
                font: "inherit",
                textAlign: "left",
                cursor: "pointer",
              }}
            >
              <span
                aria-hidden
                style={{
                  display: "inline-block",
                  width: 12,
                  flex: "0 0 12px",
                  color: "var(--text-dim)",
                  transform: isOpen ? "rotate(90deg)" : "none",
                  transition: "transform 120ms",
                }}
              >
                ▸
              </span>
              <span style={{ minWidth: 0, flex: 1 }}>
                <span style={{ display: "block", fontWeight: 600, overflowWrap: "anywhere" }}>
                  {s.label}
                </span>
                {s.subtitle && (
                  <span
                    style={{
                      display: "block",
                      fontSize: 12,
                      color: "var(--text-dim)",
                      overflowWrap: "anywhere",
                    }}
                  >
                    {s.subtitle}
                  </span>
                )}
              </span>
              <span style={{ fontSize: 12, color: "var(--text-dim)", whiteSpace: "nowrap" }}>
                {shootDateRange(s)}
              </span>
              <span
                className="fp-badge"
                title={`${n} ${n === 1 ? "post" : "posts"} from this shoot`}
                style={{ whiteSpace: "nowrap" }}
              >
                {n}
              </span>
            </button>

            {isOpen && (
              <div style={{ padding: "0 10px 10px" }}>
                <ScheduledList items={s.items} onPick={onPick} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
