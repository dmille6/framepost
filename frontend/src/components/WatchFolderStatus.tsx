import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchWatchConfig } from "../api/client";

export default function WatchFolderStatus() {
  const { data } = useQuery({
    queryKey: ["watch-config"],
    queryFn: fetchWatchConfig,
    refetchInterval: 30_000,
  });

  if (!data || !data.enabled) {
    return (
      <div
        className="fp-card"
        style={{ padding: 14, fontSize: 12, color: "var(--text-dim)" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Dot color="var(--text-fade)" />
          <span style={{ flex: 1 }}>Watch folder off</span>
          <Link to="/settings/import" style={{ fontSize: 12 }}>Configure</Link>
        </div>
      </div>
    );
  }

  const colour = data.status === "active" ? "var(--teal)" : "var(--danger)";
  const last = data.last_imported_at
    ? new Date(data.last_imported_at).toLocaleString()
    : "no imports yet";

  return (
    <div className="fp-card" style={{ padding: 14, fontSize: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Dot color={colour} />
        <span style={{ flex: 1, color: "var(--text-dim)" }}>
          Watch folder <span style={{ color: "var(--text)" }}>{data.status}</span>
        </span>
        <Link to="/settings/import" style={{ fontSize: 12 }}>Settings</Link>
      </div>
      <div style={{ marginTop: 6, color: "var(--text-fade)", fontSize: 11, wordBreak: "break-all" }}>
        {data.path}
      </div>
      <div style={{ marginTop: 4, color: "var(--text-fade)", fontSize: 11 }}>
        Last import: {last}
      </div>
      {data.last_error && (
        <div style={{ marginTop: 4, color: "var(--danger)", fontSize: 11 }}>
          {data.last_error}
        </div>
      )}
    </div>
  );
}

function Dot({ color }: { color: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: 999,
        background: color,
      }}
    />
  );
}
