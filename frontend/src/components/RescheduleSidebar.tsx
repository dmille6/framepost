import { useQuery } from "@tanstack/react-query";

import { listDrafts, thumbnailUrl } from "../api/client";
import { CALENDAR_DRAG_MIME } from "./Calendar";

export default function RescheduleSidebar() {
  const { data: drafts = [] } = useQuery({ queryKey: ["drafts"], queryFn: listDrafts });

  const ready = drafts.filter((d) => !!d.title && !!d.tags);
  const others = drafts.filter((d) => !ready.includes(d));

  return (
    <div className="fp-card" style={{ padding: 0, overflow: "hidden", display: "grid", maxHeight: "80vh" }}>
      <div style={{ padding: "12px 14px", borderBottom: "0.5px solid var(--border)" }}>
        <div style={{ fontSize: 14, fontWeight: 500 }}>Drag to schedule</div>
        <div style={{ fontSize: 11, color: "var(--text-fade)", marginTop: 4 }}>
          Drop a draft onto a day to schedule it. Ready drafts have title + tags filled in.
        </div>
      </div>
      <div style={{ overflow: "auto", padding: "8px 0" }}>
        {drafts.length === 0 && (
          <div style={{ padding: 16, fontSize: 12, color: "var(--text-fade)" }}>
            No drafts to schedule.
          </div>
        )}
        {ready.length > 0 && <SectionLabel>Ready</SectionLabel>}
        {ready.map((d) => <DraggableDraft key={d.id} id={d.id} title={d.title} filename={d.original_filename} />)}
        {others.length > 0 && <SectionLabel dim>Needs metadata</SectionLabel>}
        {others.map((d) => <DraggableDraft key={d.id} id={d.id} title={d.title} filename={d.original_filename} dim />)}
      </div>
    </div>
  );
}

function SectionLabel({ children, dim }: { children: React.ReactNode; dim?: boolean }) {
  return (
    <div
      style={{
        padding: "8px 14px 4px",
        fontSize: 10,
        textTransform: "uppercase",
        letterSpacing: 0.05,
        color: dim ? "var(--text-fade)" : "var(--teal)",
      }}
    >
      {children}
    </div>
  );
}

function DraggableDraft({
  id,
  title,
  filename,
  dim,
}: {
  id: string;
  title: string | null;
  filename: string | null;
  dim?: boolean;
}) {
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(CALENDAR_DRAG_MIME, id);
        e.dataTransfer.effectAllowed = "move";
      }}
      style={{
        display: "grid",
        gridTemplateColumns: "88px 1fr",
        gap: 12,
        padding: "8px 14px",
        fontSize: 12,
        cursor: "grab",
        opacity: dim ? 0.55 : 1,
        transition: "background 120ms ease",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--hover)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      <img
        src={thumbnailUrl(id)}
        alt=""
        style={{ width: 88, height: 88, borderRadius: 6, objectFit: "cover", background: "#0a0a0a" }}
        draggable={false}
      />
      <div style={{ overflow: "hidden", alignSelf: "center" }}>
        <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title || "(untitled)"}
        </div>
        <div style={{ color: "var(--text-fade)", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {filename ?? ""}
        </div>
      </div>
    </div>
  );
}
