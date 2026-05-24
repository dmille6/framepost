import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchUsedTags } from "../api/client";

type Props = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
};

export default function TagsInput({ value, onChange, placeholder }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const { data: usedTags = [] } = useQuery({ queryKey: ["tags-used"], queryFn: fetchUsedTags });

  // Existing tags in the current value, lowercased, used to avoid re-suggesting them.
  const existingLower = useMemo(
    () => new Set(value.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean)),
    [value],
  );

  // What's the user currently typing? Take the substring after the last comma.
  const cursor = ref.current?.selectionStart ?? value.length;
  const before = value.slice(0, cursor);
  const lastComma = before.lastIndexOf(",");
  const currentFragment = before.slice(lastComma + 1).trimStart();

  const matches = useMemo(() => {
    if (!currentFragment) return [];
    const q = currentFragment.toLowerCase();
    return usedTags
      .filter((t) => !existingLower.has(t.tag) && t.tag.startsWith(q) && t.tag !== q)
      .slice(0, 8);
  }, [currentFragment, usedTags, existingLower]);

  useEffect(() => {
    setHighlighted(0);
  }, [currentFragment]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function insert(tag: string) {
    if (!ref.current) return;
    const pos = ref.current.selectionStart ?? value.length;
    const start = value.slice(0, pos);
    const end = value.slice(pos);
    const lc = start.lastIndexOf(",");
    const before = start.slice(0, lc + 1);
    const padding = before && !before.endsWith(", ") && !before.endsWith(",") ? "" : (before.endsWith(", ") ? "" : (before.endsWith(",") ? " " : ""));
    const next = `${before}${padding}${tag}, ${end.trimStart()}`;
    onChange(next);
    setOpen(false);
    requestAnimationFrame(() => {
      if (ref.current) {
        const cursor = (before + padding + tag + ", ").length;
        ref.current.focus();
        ref.current.setSelectionRange(cursor, cursor);
      }
    });
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (!open || matches.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => (h + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => (h - 1 + matches.length) % matches.length);
    } else if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
      e.preventDefault();
      insert(matches[highlighted].tag);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  const showDropdown = open && matches.length > 0 && currentFragment.length > 0;

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <textarea
        ref={ref}
        className="fp-textarea"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        rows={3}
        placeholder={placeholder}
        style={{ minHeight: 72, fontFamily: "inherit", lineHeight: 1.5 }}
      />
      {showDropdown && (
        <div
          style={{
            position: "absolute",
            zIndex: 20,
            top: "100%",
            left: 0,
            right: 0,
            background: "var(--card)",
            border: "0.5px solid var(--border-strong)",
            borderRadius: 8,
            marginTop: 4,
            maxHeight: 240,
            overflow: "auto",
            boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
          }}
        >
          {matches.map((m, i) => (
            <button
              key={m.tag}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => insert(m.tag)}
              onMouseEnter={() => setHighlighted(i)}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                width: "100%",
                padding: "6px 12px",
                background: i === highlighted ? "var(--hover)" : "transparent",
                border: 0,
                borderBottom: "0.5px solid var(--border)",
                cursor: "pointer",
                color: "inherit",
                fontSize: 13,
                textAlign: "left",
              }}
            >
              <span>
                <span style={{ color: "var(--text)" }}>{m.tag.slice(0, currentFragment.length)}</span>
                <span style={{ color: "var(--text-dim)" }}>{m.tag.slice(currentFragment.length)}</span>
              </span>
              <span style={{ fontSize: 11, color: "var(--text-fade)" }}>×{m.count}</span>
            </button>
          ))}
          <div style={{ padding: "4px 12px", fontSize: 10, color: "var(--text-fade)" }}>
            Tab or ↵ to accept · ↑↓ to navigate
          </div>
        </div>
      )}
    </div>
  );
}
