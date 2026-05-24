import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchHealth, type HealthPayload } from "../api/client";

const DISMISS_KEY = "framepost_health_dismissed";

function reasonsFor(h: HealthPayload): { text: string; href?: string }[] {
  const out: { text: string; href?: string }[] = [];
  if (!h.db_writable) out.push({ text: "Database is not writable." });
  if (!h.photo_volume_writable) out.push({ text: "Photo volume is not writable." });
  if (!h.worker_alive) out.push({ text: "Worker is offline — scheduled posts won't fire." });
  if (h.photo_volume_free_gb < 5) {
    out.push({
      text: `Photo volume only has ${h.photo_volume_free_gb.toFixed(2)} GB free — below the hard-stop. New imports refused.`,
      href: "/settings/system",
    });
  }
  if (h.last_backup) {
    const last = new Date(h.last_backup).getTime();
    const ageDays = (Date.now() - last) / (1000 * 60 * 60 * 24);
    if (ageDays > 2) {
      out.push({
        text: `Last backup was ${Math.floor(ageDays)} days ago.`,
        href: "/settings/system",
      });
    }
  }
  return out;
}

export default function StatusBanner() {
  const [dismissed, setDismissed] = useState<string | null>(() =>
    sessionStorage.getItem(DISMISS_KEY),
  );
  const { data } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
  });

  if (!data || data.status === "ok") return null;

  // Dismissal key encodes the current health "shape" so a state change re-shows the banner.
  const key = JSON.stringify({
    s: data.status,
    w: data.worker_alive,
    d: data.db_writable,
    p: data.photo_volume_writable,
    f: Math.floor(data.photo_volume_free_gb),
  });
  if (dismissed === key) return null;

  const palette =
    data.status === "down"
      ? { bg: "#3a1818", fg: "#f59c9c", border: "rgba(245,156,156,0.4)" }
      : { bg: "#3a2b13", fg: "#f0c97a", border: "rgba(240,201,122,0.4)" };

  const reasons = reasonsFor(data);

  return (
    <div
      style={{
        background: palette.bg,
        color: palette.fg,
        borderBottom: `0.5px solid ${palette.border}`,
        padding: "10px 24px",
        fontSize: 13,
        display: "flex",
        gap: 12,
        alignItems: "flex-start",
      }}
    >
      <div style={{ flex: 1 }}>
        <strong>{data.status === "down" ? "System down" : "System degraded"}.</strong>{" "}
        {reasons.length === 0 ? (
          <>Health endpoint reports {data.status}.</>
        ) : (
          reasons.map((r, i) => (
            <span key={i}>
              {r.href ? <Link to={r.href} style={{ color: palette.fg }}>{r.text}</Link> : r.text}
              {i < reasons.length - 1 ? " " : ""}
            </span>
          ))
        )}
      </div>
      <button
        onClick={() => {
          sessionStorage.setItem(DISMISS_KEY, key);
          setDismissed(key);
        }}
        style={{
          background: "transparent",
          color: palette.fg,
          border: 0,
          cursor: "pointer",
          fontSize: 16,
          lineHeight: 1,
          padding: 0,
        }}
        title="Dismiss for this session"
      >
        ×
      </button>
    </div>
  );
}
