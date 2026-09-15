/**
 * Drafts and Scheduled, presented as two views of one queue.
 *
 * They were two top-level destinations, which made them feel like two places and put a
 * nav round-trip in the middle of the only loop that runs every week: pick a photo,
 * fill it in, schedule it, check it landed where you meant. They are the same pile of
 * work at two stages.
 *
 * This is deliberately a link strip and not a merged page. Every page here renders its
 * own <Topbar>, so genuinely hosting both under one route means lifting the chrome out
 * of all six pages -- a lot of churn for the same felt result. Two routes that read as
 * one section gets the nav hop down to a tab click without touching how either page
 * works.
 */
import { NavLink } from "react-router-dom";

const TABS = [
  { to: "/drafts", label: "Drafts" },
  { to: "/scheduled", label: "Scheduled" },
];

export default function QueueTabs({
  draftCount,
  scheduledCount,
}: {
  draftCount?: number;
  scheduledCount?: number;
}) {
  const counts: Record<string, number | undefined> = {
    "/drafts": draftCount,
    "/scheduled": scheduledCount,
  };

  return (
    <div
      style={{
        display: "flex",
        gap: 4,
        marginBottom: 16,
        borderBottom: "0.5px solid var(--border)",
      }}
    >
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          style={({ isActive }) => ({
            fontSize: 13,
            textDecoration: "none",
            padding: "8px 14px",
            color: isActive ? "var(--text)" : "var(--text-dim)",
            fontWeight: isActive ? 500 : 400,
            // Sits on top of the container's own border so the active tab reads as
            // continuous with the panel below it.
            borderBottom: `2px solid ${isActive ? "var(--teal)" : "transparent"}`,
            marginBottom: -1,
            display: "inline-flex",
            alignItems: "center",
            gap: 7,
          })}
        >
          {t.label}
          {counts[t.to] !== undefined && (
            <span style={{ fontSize: 11, color: "var(--text-fade)", fontVariantNumeric: "tabular-nums" }}>
              {counts[t.to]}
            </span>
          )}
        </NavLink>
      ))}
    </div>
  );
}
