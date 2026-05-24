import { useState, useRef, useEffect } from "react";

type Option = { id: string; label: string; sublabel?: string };

type Props = {
  label: string;
  options: Option[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  maxSelected?: number;
  warnThreshold?: number;
  emptyMessage?: string;
};

export default function MultiSelectChips({
  label,
  options,
  selected,
  onChange,
  maxSelected,
  warnThreshold,
  emptyMessage = "Nothing to pick yet.",
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  function toggle(id: string) {
    const next = new Set(selected);
    if (next.has(id)) {
      next.delete(id);
    } else {
      if (maxSelected && next.size >= maxSelected) {
        if (!warnThreshold || next.size + 1 > warnThreshold) {
          if (!confirm(`Add a ${next.size + 1}th selection? Suggested max is ${maxSelected}.`)) {
            return;
          }
        }
      }
      next.add(id);
    }
    onChange(next);
  }

  const counter = maxSelected
    ? `${selected.size} / ${maxSelected}`
    : `${selected.size}`;

  return (
    <div ref={ref} style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{label}</span>
        <span
          style={{
            fontSize: 11,
            color: warnThreshold && selected.size >= warnThreshold ? "var(--danger)" : "var(--text-fade)",
          }}
        >
          {counter}
        </span>
      </div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="fp-input"
        style={{ textAlign: "left", cursor: "pointer", display: "flex", flexWrap: "wrap", gap: 4, minHeight: 38 }}
      >
        {selected.size === 0 ? (
          <span style={{ color: "var(--text-fade)" }}>None selected</span>
        ) : (
          [...selected].map((id) => {
            const opt = options.find((o) => o.id === id);
            return (
              <span
                key={id}
                style={{
                  background: "var(--hover)",
                  padding: "2px 8px",
                  borderRadius: 999,
                  fontSize: 12,
                }}
              >
                {opt?.label ?? id}
              </span>
            );
          })
        )}
      </button>
      {open && (
        <div
          className="fp-card"
          style={{
            padding: 0,
            maxHeight: 240,
            overflow: "auto",
            position: "relative",
          }}
        >
          {options.length === 0 ? (
            <div style={{ padding: 12, color: "var(--text-dim)", fontSize: 13 }}>{emptyMessage}</div>
          ) : (
            options.map((o) => (
              <button
                type="button"
                key={o.id}
                onClick={() => toggle(o.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 12px",
                  width: "100%",
                  background: "transparent",
                  color: "inherit",
                  border: 0,
                  borderBottom: "0.5px solid var(--border)",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <input
                  type="checkbox"
                  checked={selected.has(o.id)}
                  onChange={() => {}}
                  style={{ pointerEvents: "none" }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13 }}>{o.label}</div>
                  {o.sublabel && (
                    <div style={{ fontSize: 11, color: "var(--text-fade)" }}>{o.sublabel}</div>
                  )}
                </div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
