import { useState, type MouseEvent } from "react";

import type { Post } from "../api/client";
import { thumbnailUrl } from "../api/client";

type Props = {
  post: Post;
  selected: boolean;
  onSelect: () => void;
  onDelete?: () => void;
  multiSelectMode?: boolean;
  isChecked?: boolean;
  onToggleCheck?: () => void;
};

export default function DraftCard({
  post,
  selected,
  onSelect,
  onDelete,
  multiSelectMode = false,
  isChecked = false,
  onToggleCheck,
}: Props) {
  const [hover, setHover] = useState(false);
  const mp = post.width && post.height ? ((post.width * post.height) / 1_000_000).toFixed(1) : null;
  const captured = post.captured_at ? new Date(post.captured_at).toLocaleDateString() : null;

  function handleDelete(e: MouseEvent) {
    e.stopPropagation();
    e.preventDefault();
    if (!onDelete) return;
    onDelete();
  }

  return (
    <button
      onClick={() => (multiSelectMode && onToggleCheck ? onToggleCheck() : onSelect())}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "block",
        textAlign: "left",
        background: "var(--card)",
        border: `0.5px solid ${selected || isChecked ? "var(--teal)" : "var(--border)"}`,
        borderRadius: 12,
        padding: 0,
        overflow: "hidden",
        cursor: "pointer",
        color: "inherit",
        transition: "border-color 80ms ease",
        position: "relative",
      }}
    >
      {multiSelectMode && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            zIndex: 1,
            background: "rgba(0,0,0,0.6)",
            borderRadius: 999,
            width: 24,
            height: 24,
            display: "grid",
            placeItems: "center",
            border: `1px solid ${isChecked ? "var(--teal)" : "var(--border-strong)"}`,
            color: isChecked ? "var(--teal)" : "transparent",
            fontSize: 14,
            fontWeight: 500,
          }}
        >
          {isChecked ? "✓" : ""}
        </div>
      )}
      {onDelete && !multiSelectMode && (
        <button
          type="button"
          onClick={handleDelete}
          aria-label="Delete draft"
          title="Delete draft"
          style={{
            position: "absolute",
            top: 8,
            right: 8,
            zIndex: 2,
            background: "rgba(0,0,0,0.65)",
            backdropFilter: "blur(4px)",
            WebkitBackdropFilter: "blur(4px)",
            border: "0.5px solid rgba(255,255,255,0.18)",
            borderRadius: 999,
            width: 28,
            height: 28,
            display: "grid",
            placeItems: "center",
            cursor: "pointer",
            color: "var(--danger)",
            opacity: hover ? 1 : 0,
            transform: hover ? "scale(1)" : "scale(0.9)",
            transition: "opacity 120ms ease, transform 120ms ease, background 120ms ease",
            padding: 0,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "var(--danger)";
            e.currentTarget.style.color = "#0a0a0a";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "rgba(0,0,0,0.65)";
            e.currentTarget.style.color = "var(--danger)";
          }}
        >
          <TrashIcon />
        </button>
      )}
      <div
        style={{
          aspectRatio: "4 / 3",
          background: "#0a0a0a",
          overflow: "hidden",
          display: "grid",
          placeItems: "center",
        }}
      >
        <img
          src={thumbnailUrl(post.id)}
          alt={post.title ?? post.original_filename ?? ""}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </div>
      <div style={{ padding: "10px 12px" }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {post.title || post.original_filename || "(untitled)"}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
          {[
            mp ? `${mp} MP` : null,
            post.width && post.height ? `${post.width}×${post.height}` : null,
            captured,
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        <div style={{ marginTop: 6 }}>
          <span className={`fp-pill fp-pill-${post.status}`}>{post.status}</span>
        </div>
      </div>
    </button>
  );
}

function TrashIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <line x1="10" y1="11" x2="10" y2="17" />
      <line x1="14" y1="11" x2="14" y2="17" />
    </svg>
  );
}
