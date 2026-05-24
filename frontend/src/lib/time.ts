// Format an ISO timestamp (treated as UTC if no zone) as a relative time, e.g. "2h ago", "in 3d".
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const stamp = iso.includes("Z") || /[+\-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const then = new Date(stamp).getTime();
  if (!Number.isFinite(then)) return "";
  const diffMs = then - now.getTime();
  const past = diffMs < 0;
  const seconds = Math.abs(diffMs) / 1000;
  const minutes = seconds / 60;
  const hours = minutes / 60;
  const days = hours / 24;

  let value: string;
  if (seconds < 45) value = "just now";
  else if (minutes < 1.5) value = "1m";
  else if (minutes < 60) value = `${Math.round(minutes)}m`;
  else if (hours < 24) value = `${Math.round(hours)}h`;
  else if (days < 7) value = `${Math.round(days)}d`;
  else if (days < 30) value = `${Math.round(days / 7)}w`;
  else if (days < 365) value = `${Math.round(days / 30)}mo`;
  else value = `${Math.round(days / 365)}y`;

  if (value === "just now") return value;
  return past ? `${value} ago` : `in ${value}`;
}

export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const stamp = iso.includes("Z") || /[+\-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z";
  const d = new Date(stamp);
  if (!Number.isFinite(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined,
    hour: "numeric",
    minute: "2-digit",
  });
}
