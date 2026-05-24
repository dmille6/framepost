import { useRef, useState, type DragEvent } from "react";

export type UploadItem = {
  id: string;
  file: File;
  state: "queued" | "uploading" | "processing" | "success" | "duplicate" | "error";
  progress?: number; // 0..1 during uploading/processing
  message?: string;
  duplicateOf?: string;
};

type Props = {
  items: UploadItem[];
  onAdd: (files: File[]) => void;
  onRetryDuplicate: (id: string) => void;
  onDismiss: (id: string) => void;
};

export default function UploadZone({ items, onAdd, onRetryDuplicate, onDismiss }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith("image/"));
    if (files.length) onAdd(files);
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        style={{
          background: dragOver ? "var(--hover)" : "var(--card)",
          border: `1px dashed ${dragOver ? "var(--teal)" : "var(--border-strong)"}`,
          borderRadius: 12,
          padding: 28,
          textAlign: "center",
          cursor: "pointer",
          transition: "background 80ms ease, border-color 80ms ease",
        }}
      >
        <div style={{ fontSize: 14, marginBottom: 4 }}>
          Drop photos here, or <span style={{ color: "var(--teal)" }}>browse</span> to upload
        </div>
        <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
          JPEG / PNG · up to 200 MB · IPTC title, caption, and keywords are imported automatically
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) onAdd(files);
            e.target.value = "";
          }}
        />
      </div>

      {items.length > 0 && (
        <div style={{ marginTop: 12, display: "grid", gap: 6 }}>
          {items.map((it) => (
            <div
              key={it.id}
              style={{
                position: "relative",
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "8px 12px",
                background: "var(--card)",
                border: "0.5px solid var(--border)",
                borderRadius: 8,
                fontSize: 13,
                overflow: "hidden",
              }}
            >
              {(it.state === "uploading" || it.state === "processing") && (
                <div
                  style={{
                    position: "absolute",
                    left: 0,
                    bottom: 0,
                    height: 2,
                    width: `${Math.round((it.progress ?? 0) * 100)}%`,
                    background: it.state === "processing" ? "rgba(240,201,122,0.7)" : "var(--teal)",
                    transition: "width 120ms ease",
                  }}
                />
              )}
              <div style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {it.file.name}
                <span style={{ color: "var(--text-fade)", marginLeft: 8 }}>
                  {(it.file.size / 1024 / 1024).toFixed(1)} MB
                </span>
              </div>
              <div style={{ color: stateColor(it.state) }}>{stateLabel(it)}</div>
              {it.state === "duplicate" && (
                <>
                  <button className="fp-link" onClick={() => onRetryDuplicate(it.id)}>
                    Upload anyway
                  </button>
                  <button className="fp-link" onClick={() => onDismiss(it.id)}>
                    Dismiss
                  </button>
                </>
              )}
              {(it.state === "error" || it.state === "success") && (
                <button className="fp-link" onClick={() => onDismiss(it.id)}>
                  Clear
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function stateLabel(it: UploadItem): string {
  switch (it.state) {
    case "queued": return "Queued";
    case "uploading":
      return `Uploading… ${Math.round((it.progress ?? 0) * 100)}%`;
    case "processing":
      return "Processing…";
    case "success": return "Imported";
    case "duplicate": return "Duplicate";
    case "error": return it.message ?? "Failed";
  }
}

function stateColor(s: UploadItem["state"]): string {
  switch (s) {
    case "uploading":
    case "queued": return "var(--text-dim)";
    case "processing": return "#f0c97a";
    case "success": return "var(--teal)";
    case "duplicate": return "#FAEEDA";
    case "error": return "var(--danger)";
  }
}
