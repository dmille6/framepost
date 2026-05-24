import { useQuery } from "@tanstack/react-query";
import { Link, NavLink } from "react-router-dom";

import { fetchActivityUnreadCount, fetchFlickrStatus, listHistory } from "../api/client";
import { useAuth } from "../auth";
import StatusBanner from "./StatusBanner";

export default function Topbar() {
  const { user, logout } = useAuth();
  const { data: flickr } = useQuery({
    queryKey: ["flickr-status"],
    queryFn: fetchFlickrStatus,
    refetchInterval: 60_000,
    enabled: !!user,
  });
  const connected = flickr?.connected ?? false;

  const { data: missedPosts = [] } = useQuery({
    queryKey: ["history-missed"],
    queryFn: () => listHistory(undefined, ["missed"]),
    refetchInterval: 60_000,
    enabled: !!user,
  });
  const missedCount = missedPosts.length;

  const { data: unreadActivity } = useQuery({
    queryKey: ["activity-unread-count"],
    queryFn: fetchActivityUnreadCount,
    refetchInterval: 60_000,
    enabled: !!user,
  });
  const unreadCount = unreadActivity?.unread ?? 0;

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        background: "rgba(10, 10, 10, 0.85)",
        backdropFilter: "saturate(180%) blur(8px)",
        WebkitBackdropFilter: "saturate(180%) blur(8px)",
        borderBottom: "0.5px solid var(--border)",
      }}
    >
      <StatusBanner />
      <div
        className="fp-page"
        style={{
          paddingTop: 12,
          paddingBottom: 12,
          display: "flex",
          alignItems: "center",
          gap: 24,
        }}
      >
        <Link
          to="/drafts"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 8,
            color: "var(--text)",
            textDecoration: "none",
          }}
          aria-label="FramePost home"
        >
          <Logomark />
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>
            frame<span style={{ color: "var(--teal)" }}>post</span>
          </span>
        </Link>

        <Link
          to="/settings/flickr"
          title={connected ? `Connected as ${flickr?.account_name ?? "Flickr account"}` : "Click to connect Flickr"}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11.5,
            padding: "3px 9px 3px 8px",
            border: `0.5px solid ${connected ? "rgba(93, 202, 165, 0.2)" : "var(--border)"}`,
            background: connected ? "var(--teal-tint)" : "rgba(255,255,255,0.04)",
            borderRadius: 999,
            color: connected ? "var(--teal)" : "var(--text-dim)",
            textDecoration: "none",
            fontWeight: 500,
            transition: "background 120ms ease",
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background: connected ? "var(--teal)" : "var(--text-fade)",
              boxShadow: connected ? "0 0 6px rgba(93,202,165,0.6)" : "none",
            }}
          />
          {connected ? "Flickr connected" : "Flickr disconnected"}
        </Link>

        <nav style={{ display: "flex", gap: 4, marginLeft: 4 }}>
          {[
            { to: "/drafts", label: "Drafts" },
            { to: "/scheduled", label: "Scheduled" },
            { to: "/published", label: "Published" },
            { to: "/activity", label: "Activity" },
            { to: "/analytics", label: "Analytics" },
            { to: "/settings", label: "Settings" },
          ].map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              style={({ isActive }) => ({
                fontSize: 13,
                color: isActive ? "var(--text)" : "var(--text-dim)",
                fontWeight: isActive ? 500 : 400,
                textDecoration: "none",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 10px",
                borderRadius: 6,
                background: isActive ? "var(--hover)" : "transparent",
                transition: "background 120ms ease, color 120ms ease",
              })}
            >
              {l.label}
              {l.to === "/published" && missedCount > 0 && (
                <span
                  title={`${missedCount} missed post${missedCount === 1 ? "" : "s"}`}
                  style={{
                    background: "rgba(245, 156, 156, 0.18)",
                    color: "var(--danger)",
                    border: "0.5px solid rgba(245, 156, 156, 0.3)",
                    fontSize: 10,
                    fontWeight: 600,
                    padding: "0 6px",
                    minWidth: 18,
                    height: 16,
                    lineHeight: "14px",
                    borderRadius: 999,
                    textAlign: "center",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {missedCount}
                </span>
              )}
              {l.to === "/activity" && unreadCount > 0 && (
                <span
                  title={`${unreadCount} unread comment${unreadCount === 1 ? "" : "s"}`}
                  style={{
                    background: "var(--teal-tint)",
                    color: "var(--teal)",
                    border: "0.5px solid rgba(93, 202, 165, 0.3)",
                    fontSize: 10,
                    fontWeight: 600,
                    padding: "0 6px",
                    minWidth: 18,
                    height: 16,
                    lineHeight: "14px",
                    borderRadius: 999,
                    textAlign: "center",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {unreadCount > 99 ? "99+" : unreadCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div
          style={{
            marginLeft: "auto",
            fontSize: 13,
            color: "var(--text-dim)",
            display: "inline-flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <span>{user?.username}</span>
          <button
            onClick={() => logout()}
            className="fp-link"
            style={{ fontSize: 13, color: "var(--text-dim)" }}
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}

function Logomark() {
  return (
    <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden focusable="false">
      <rect width="32" height="32" rx="7" fill="#0f0f0f" stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
      <circle cx="16" cy="16" r="9" fill="none" stroke="#5dcaa5" strokeWidth="1.6" />
      <circle cx="16" cy="16" r="2.6" fill="#5dcaa5" />
      <path d="M16 7.4 L18.6 11.6 L13.4 11.6 Z" fill="#5dcaa5" opacity="0.85" />
      <path d="M24.6 16 L20.4 18.6 L20.4 13.4 Z" fill="#5dcaa5" opacity="0.85" />
      <path d="M16 24.6 L13.4 20.4 L18.6 20.4 Z" fill="#5dcaa5" opacity="0.85" />
      <path d="M7.4 16 L11.6 13.4 L11.6 18.6 Z" fill="#5dcaa5" opacity="0.85" />
    </svg>
  );
}
