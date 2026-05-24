import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { listTitleTemplates } from "../api/client";

type Props = {
  initialTitle?: string | null;
  initialDescription?: string | null;
  onCancel: () => void;
  onApply: (next: { title: string; description: string | null }) => void;
};

const LS_KEY = "framepost.title_template_values.v1";

type StoredValues = Record<string, Record<string, string>>;
//                              templateId          fieldKey -> value

function loadStored(): StoredValues {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveStored(s: StoredValues) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(s));
  } catch {
    /* localStorage full or disabled — ignore */
  }
}

function renderTemplate(template: string, values: Record<string, string>): string {
  return template.replace(/\{([a-z][a-z0-9_]*)\}/g, (_, key) => values[key] ?? "");
}

export default function ApplyTemplateDialog({
  initialTitle,
  initialDescription,
  onCancel,
  onApply,
}: Props) {
  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["title-templates"],
    queryFn: listTitleTemplates,
  });

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [includeDescription, setIncludeDescription] = useState(true);

  const selected = useMemo(
    () => templates.find((t) => t.id === selectedId) ?? null,
    [templates, selectedId],
  );

  // Auto-pick the first template when the list loads.
  useEffect(() => {
    if (!selectedId && templates.length > 0) {
      setSelectedId(templates[0].id);
    }
  }, [templates, selectedId]);

  // When the selected template changes, prefill values from localStorage (if we've used this
  // template before) so repeat-shoot photos are nearly one-click.
  useEffect(() => {
    if (!selected) {
      setValues({});
      return;
    }
    const stored = loadStored()[selected.id] ?? {};
    const initial: Record<string, string> = {};
    for (const f of selected.fields) {
      initial[f.key] = stored[f.key] ?? "";
    }
    setValues(initial);
  }, [selected?.id, selected?.fields.length]);

  // Esc closes
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const titlePreview = selected ? renderTemplate(selected.title_template, values) : "";
  const descPreview =
    selected && selected.description_template
      ? renderTemplate(selected.description_template, values)
      : "";

  const allFilled = selected
    ? selected.fields.every((f) => (values[f.key] || "").trim().length > 0)
    : false;

  function handleApply() {
    if (!selected) return;
    // Persist values for next time on this template.
    const stored = loadStored();
    stored[selected.id] = { ...values };
    saveStored(stored);

    onApply({
      title: titlePreview,
      description: includeDescription && descPreview ? descPreview : null,
    });
  }

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24 }}
      onClick={onCancel}
    >
      <div
        className="fp-card fp-fade"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(560px, 100%)",
          maxHeight: "92vh",
          overflow: "auto",
          padding: 0,
          boxShadow: "var(--shadow-lg)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            padding: "14px 20px",
            borderBottom: "0.5px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Apply title template
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              Fills title{selected?.description_template ? " + description" : ""} for this post. Existing values are replaced.
            </div>
          </div>
          <button
            onClick={onCancel}
            aria-label="Close"
            style={{
              background: "transparent",
              border: "0.5px solid var(--border-strong)",
              borderRadius: 8,
              width: 32,
              height: 32,
              color: "var(--text-dim)",
              cursor: "pointer",
              fontSize: 16,
            }}
          >
            ×
          </button>
        </div>

        <div style={{ padding: 20, display: "grid", gap: 14 }}>
          {isLoading ? (
            <div style={{ color: "var(--text-dim)", fontSize: 13 }}>Loading templates…</div>
          ) : templates.length === 0 ? (
            <div
              style={{
                fontSize: 13,
                color: "var(--text-dim)",
                padding: 16,
                border: "0.5px dashed var(--border-strong)",
                borderRadius: 8,
                textAlign: "center",
              }}
            >
              No templates yet. Add one in <strong>Settings → Title Templates</strong>.
            </div>
          ) : (
            <>
              {/* Template picker */}
              <div style={{ display: "grid", gap: 6 }}>
                <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
                  Template
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {templates.map((t) => {
                    const active = t.id === selectedId;
                    return (
                      <button
                        key={t.id}
                        onClick={() => setSelectedId(t.id)}
                        style={{
                          background: active ? "var(--teal-tint)" : "transparent",
                          color: active ? "var(--text)" : "var(--text-dim)",
                          border: `0.5px solid ${active ? "rgba(93,202,165,0.3)" : "var(--border-strong)"}`,
                          borderRadius: 999,
                          padding: "6px 12px",
                          fontSize: 12,
                          fontWeight: active ? 500 : 400,
                          cursor: "pointer",
                        }}
                      >
                        {t.name}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Fields */}
              {selected?.fields.map((f) => (
                <label key={f.key} style={{ display: "grid", gap: 4 }}>
                  <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
                    {f.label}
                  </span>
                  <input
                    className="fp-input"
                    value={values[f.key] ?? ""}
                    placeholder={f.placeholder ?? ""}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [f.key]: e.target.value }))
                    }
                  />
                </label>
              ))}

              {selected?.description_template && (
                <label
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    fontSize: 12,
                    color: "var(--text-dim)",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={includeDescription}
                    onChange={(e) => setIncludeDescription(e.target.checked)}
                    style={{ accentColor: "var(--teal)", margin: 0 }}
                  />
                  Also fill description
                </label>
              )}

              {/* Preview */}
              {selected && (
                <div
                  style={{
                    background: "var(--bg)",
                    border: "0.5px solid var(--border)",
                    borderRadius: 8,
                    padding: 12,
                    display: "grid",
                    gap: 8,
                  }}
                >
                  <div style={{ fontSize: 11, color: "var(--text-fade)", fontWeight: 500 }}>
                    PREVIEW
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>
                    {titlePreview || <span style={{ color: "var(--text-fade)" }}>(empty)</span>}
                  </div>
                  {includeDescription && descPreview && (
                    <div style={{ fontSize: 12, color: "var(--text-dim)" }}>{descPreview}</div>
                  )}
                </div>
              )}

              {(initialTitle || initialDescription) && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--amber)",
                    background: "var(--amber-tint)",
                    border: "0.5px solid rgba(240,201,122,0.2)",
                    borderRadius: 8,
                    padding: "8px 12px",
                  }}
                >
                  Heads up: this will replace the post's existing
                  {initialTitle ? " title" : ""}
                  {initialTitle && initialDescription && includeDescription ? " and" : ""}
                  {initialDescription && includeDescription ? " description" : ""}.
                </div>
              )}
            </>
          )}
        </div>

        <div
          style={{
            padding: "14px 20px",
            borderTop: "0.5px solid var(--border)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button className="fp-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="fp-btn"
            onClick={handleApply}
            disabled={!selected || !allFilled}
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}
