import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createTitleTemplate,
  deleteTitleTemplate,
  listTitleTemplates,
  updateTitleTemplate,
  type TemplateField,
  type TitleTemplate,
  type TitleTemplateInput,
} from "../api/client";
import { CardHeader } from "../components/PageHeader";
import { SkeletonRows } from "../components/Skeleton";

const PLACEHOLDER_RE = /\{([a-z][a-z0-9_]*)\}/g;

const EMPTY: TitleTemplateInput = {
  name: "",
  title_template: "",
  description_template: "",
  fields: [],
  sort_order: 0,
};

export default function SettingsTitleTemplates() {
  const qc = useQueryClient();
  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["title-templates"],
    queryFn: listTitleTemplates,
  });
  const [editing, setEditing] = useState<{ form: TitleTemplateInput; id: string | null } | null>(null);

  const save = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: TitleTemplateInput }) =>
      id ? updateTitleTemplate(id, body) : createTitleTemplate(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["title-templates"] });
      setEditing(null);
    },
  });

  const del = useMutation({
    mutationFn: (id: string) => deleteTitleTemplate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["title-templates"] }),
  });

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 800 }}>
      <CardHeader
        title="Title templates"
        subtitle="Reusable patterns with named slots — applied from the metadata editor's 'Apply template' link. Use {placeholders} like {performer} or {venue}; declare each placeholder as a field below."
        action={
          <button
            className="fp-btn"
            onClick={() => setEditing({ form: { ...EMPTY }, id: null })}
            style={{ padding: "7px 12px", fontSize: 13 }}
          >
            Add template
          </button>
        }
      />

      {isLoading ? (
        <SkeletonRows count={3} />
      ) : templates.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--text-dim)" }}>
          No templates yet — add one to get started.
        </div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {templates.map((t) => (
            <TemplateRow
              key={t.id}
              template={t}
              onEdit={() =>
                setEditing({
                  form: {
                    name: t.name,
                    title_template: t.title_template,
                    description_template: t.description_template ?? "",
                    fields: t.fields,
                    sort_order: t.sort_order,
                  },
                  id: t.id,
                })
              }
              onDelete={() => del.mutate(t.id)}
            />
          ))}
        </div>
      )}

      {editing && (
        <TemplateEditorModal
          initial={editing.form}
          isNew={editing.id === null}
          onCancel={() => setEditing(null)}
          onSave={(form) => save.mutate({ id: editing.id, body: form })}
          saving={save.isPending}
          error={save.error instanceof ApiError ? save.error.message : null}
        />
      )}
    </div>
  );
}

function TemplateRow({
  template,
  onEdit,
  onDelete,
}: {
  template: TitleTemplate;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 12,
        padding: "12px 14px",
        background: "var(--bg)",
        border: "0.5px solid var(--border)",
        borderRadius: 8,
        alignItems: "center",
      }}
    >
      <div>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{template.name}</div>
        <div
          style={{
            fontSize: 11,
            color: "var(--text-fade)",
            marginTop: 2,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          }}
        >
          {template.title_template}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
          {template.fields.length} field{template.fields.length === 1 ? "" : "s"}: {" "}
          {template.fields.map((f) => f.label).join(", ")}
        </div>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          className="fp-btn-ghost"
          onClick={onEdit}
          style={{ padding: "5px 10px", fontSize: 12 }}
        >
          Edit
        </button>
        <button
          className="fp-btn-danger"
          onClick={onDelete}
          style={{ padding: "5px 10px", fontSize: 12 }}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

function TemplateEditorModal({
  initial,
  isNew,
  onCancel,
  onSave,
  saving,
  error,
}: {
  initial: TitleTemplateInput;
  isNew: boolean;
  onCancel: () => void;
  onSave: (form: TitleTemplateInput) => void;
  saving: boolean;
  error: string | null;
}) {
  const [form, setForm] = useState<TitleTemplateInput>(initial);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // Auto-generate fields list from {placeholders} found in title + description templates.
  // Preserves any labels/placeholders the user already filled in for matching keys.
  function syncFieldsFromTemplates() {
    const found = new Set<string>();
    for (const tpl of [form.title_template, form.description_template ?? ""]) {
      for (const m of tpl.matchAll(PLACEHOLDER_RE)) {
        found.add(m[1]);
      }
    }
    const existingByKey = new Map(form.fields.map((f) => [f.key, f]));
    const next: TemplateField[] = [];
    for (const key of found) {
      const prev = existingByKey.get(key);
      next.push(
        prev ?? {
          key,
          label: key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " "),
          placeholder: null,
        },
      );
    }
    setForm((f) => ({ ...f, fields: next }));
  }

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24 }}
      onClick={saving ? undefined : onCancel}
    >
      <div
        className="fp-card fp-fade"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(640px, 100%)",
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
          <div style={{ fontSize: 15, fontWeight: 600 }}>
            {isNew ? "New template" : "Edit template"}
          </div>
          <button
            onClick={onCancel}
            disabled={saving}
            aria-label="Close"
            style={{
              background: "transparent",
              border: "0.5px solid var(--border-strong)",
              borderRadius: 8,
              width: 32,
              height: 32,
              color: "var(--text-dim)",
              cursor: saving ? "not-allowed" : "pointer",
              fontSize: 16,
            }}
          >
            ×
          </button>
        </div>

        <div style={{ padding: 20, display: "grid", gap: 14 }}>
          <Label label="Name">
            <input
              className="fp-input"
              value={form.name}
              placeholder="Performance"
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            />
          </Label>

          <Label
            label="Title template"
            hint={
              <>
                Use <code>{"{placeholder}"}</code> for slots. Example:{" "}
                <code>{`{performer} performing at "{event}" at {venue} {city} / {date}`}</code>
              </>
            }
          >
            <input
              className="fp-input"
              value={form.title_template}
              onChange={(e) => setForm((f) => ({ ...f, title_template: e.target.value }))}
              style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
            />
          </Label>

          <Label
            label="Description template (optional)"
            hint="Leave blank to skip the description on apply."
          >
            <textarea
              className="fp-textarea"
              value={form.description_template ?? ""}
              onChange={(e) =>
                setForm((f) => ({ ...f, description_template: e.target.value }))
              }
              rows={2}
              style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }}
            />
          </Label>

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>
              Fields
            </span>
            <button
              type="button"
              onClick={syncFieldsFromTemplates}
              className="fp-btn-ghost"
              style={{ padding: "4px 10px", fontSize: 11 }}
              title="Detect placeholders in the templates above and create matching fields"
            >
              Detect from templates
            </button>
          </div>
          {form.fields.length === 0 ? (
            <div
              style={{
                fontSize: 12,
                color: "var(--text-fade)",
                textAlign: "center",
                padding: 12,
                border: "0.5px dashed var(--border-strong)",
                borderRadius: 8,
              }}
            >
              Add placeholders like <code>{"{performer}"}</code> to your template, then click
              "Detect from templates".
            </div>
          ) : (
            <div style={{ display: "grid", gap: 8 }}>
              {form.fields.map((f, i) => (
                <div
                  key={i}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "120px 1fr 1fr auto",
                    gap: 8,
                    alignItems: "center",
                  }}
                >
                  <input
                    className="fp-input"
                    value={f.key}
                    placeholder="key"
                    onChange={(e) => {
                      const next = [...form.fields];
                      next[i] = { ...f, key: e.target.value.toLowerCase() };
                      setForm((s) => ({ ...s, fields: next }));
                    }}
                    style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }}
                  />
                  <input
                    className="fp-input"
                    value={f.label}
                    placeholder="Label"
                    onChange={(e) => {
                      const next = [...form.fields];
                      next[i] = { ...f, label: e.target.value };
                      setForm((s) => ({ ...s, fields: next }));
                    }}
                    style={{ fontSize: 12 }}
                  />
                  <input
                    className="fp-input"
                    value={f.placeholder ?? ""}
                    placeholder="Placeholder hint"
                    onChange={(e) => {
                      const next = [...form.fields];
                      next[i] = { ...f, placeholder: e.target.value || null };
                      setForm((s) => ({ ...s, fields: next }));
                    }}
                    style={{ fontSize: 12 }}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const next = form.fields.filter((_, j) => j !== i);
                      setForm((s) => ({ ...s, fields: next }));
                    }}
                    className="fp-btn-danger"
                    style={{ padding: "4px 8px", fontSize: 11 }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}

          {error && (
            <div
              style={{
                fontSize: 12,
                color: "var(--danger)",
                padding: "8px 12px",
                background: "var(--danger-tint)",
                border: "0.5px solid rgba(245,156,156,0.2)",
                borderRadius: 8,
              }}
            >
              {error}
            </div>
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
          <button className="fp-btn-ghost" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button
            className="fp-btn"
            onClick={() => onSave(form)}
            disabled={saving || !form.name.trim() || !form.title_template.trim()}
          >
            {saving && <span className="fp-spinner" />}
            {saving ? "Saving" : "Save template"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Label({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "grid", gap: 4 }}>
      <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>{label}</span>
      {children}
      {hint && (
        <span style={{ fontSize: 11, color: "var(--text-fade)", lineHeight: 1.4 }}>{hint}</span>
      )}
    </label>
  );
}
