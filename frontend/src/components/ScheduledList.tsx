import type { ScheduledItem } from "../api/client";
import { thumbnailUrl } from "../api/client";

type Props = {
  items: ScheduledItem[];
  onPick: (item: ScheduledItem) => void;
};

export default function ScheduledList({ items, onPick }: Props) {
  if (items.length === 0) {
    return (
      <div
        className="fp-card"
        style={{ padding: 60, textAlign: "center", color: "var(--text-dim)" }}
      >
        Nothing scheduled in this window.
      </div>
    );
  }

  // Group by local-date for visual breaks.
  const groups = new Map<string, ScheduledItem[]>();
  for (const item of items) {
    if (!item.scheduled_at) continue;
    const local = new Date(item.scheduled_at + "Z");
    const key = local.toDateString();
    const arr = groups.get(key) ?? [];
    arr.push(item);
    groups.set(key, arr);
  }

  return (
    <div className="fp-card" style={{ padding: 0, overflow: "hidden" }}>
      {[...groups.entries()].map(([day, group]) => (
        <div key={day}>
          <div
            style={{
              padding: "8px 16px",
              fontSize: 12,
              color: "var(--text-dim)",
              borderBottom: "0.5px solid var(--border)",
              background: "var(--bg)",
              fontWeight: 500,
            }}
          >
            {day}
          </div>
          {group.map((item) => (
            <Row key={item.id} item={item} onClick={() => onPick(item)} />
          ))}
        </div>
      ))}
    </div>
  );
}

function Row({ item, onClick }: { item: ScheduledItem; onClick: () => void }) {
  const time = item.scheduled_at
    ? new Date(item.scheduled_at + "Z").toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      })
    : "—";

  return (
    <button
      onClick={onClick}
      style={{
        width: "100%",
        display: "grid",
        gridTemplateColumns: "84px 1fr auto",
        gap: 16,
        padding: "12px 16px",
        background: "transparent",
        border: 0,
        borderBottom: "0.5px solid var(--border)",
        color: "inherit",
        textAlign: "left",
        cursor: "pointer",
        alignItems: "center",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <img
        src={thumbnailUrl(item.id)}
        alt=""
        style={{
          width: 84,
          height: 64,
          objectFit: "cover",
          borderRadius: 6,
          background: "#0a0a0a",
        }}
      />
      <div style={{ overflow: "hidden" }}>
        <div
          style={{
            fontSize: 14,
            fontWeight: 500,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {item.title || item.original_filename || "(untitled)"}
        </div>
        {item.description && (
          <div
            style={{
              fontSize: 12,
              color: "var(--text-dim)",
              marginTop: 4,
              overflow: "hidden",
              textOverflow: "ellipsis",
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
            }}
          >
            {item.description}
          </div>
        )}
      </div>
      <div style={{ textAlign: "right", display: "grid", gap: 4 }}>
        <div style={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}>{time}</div>
        <span className={`fp-pill fp-pill-${item.status}`} style={{ justifySelf: "end" }}>
          {item.status}
        </span>
      </div>
    </button>
  );
}
