import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createGroup,
  deleteGroup,
  type Group,
  type GroupInput,
  listGroups,
  updateGroup,
} from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

const EMPTY: GroupInput = {
  flickr_group_id: "",
  name: "",
  category: "",
  daily_limit: null,
  content_notes: "",
  no_watermark: false,
  default_enabled: false,
};

export default function SettingsGroups() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({ queryKey: ["groups"], queryFn: listGroups });
  const [editing, setEditing] = useState<{ form: GroupInput; id: string | null } | null>(null);

  const saveMutation = useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: GroupInput }) =>
      id ? updateGroup(id, body) : createGroup(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["groups"] });
      setEditing(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteGroup,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["groups"] }),
  });

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 800 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Flickr groups</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            Curated list of groups you submit to. Groups with a default-enabled flag are
            pre-selected for new posts in their category. Selection is capped at 5 by default
            and warns above 8.
          </div>
        </div>
        <button className="fp-btn" onClick={() => setEditing({ form: { ...EMPTY }, id: null })}>
          Add group
        </button>
      </div>

      {isLoading ? (
        <SkeletonRows count={3} />
      ) : data.length === 0 ? (
        <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
          No groups configured. Add one with its Flickr group ID (find it on the group page URL).
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-fade)", fontSize: 11 }}>
              <Th>Group</Th>
              <Th>Category</Th>
              <Th>Daily limit</Th>
              <Th>Default</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {data.map((g) => (
              <tr key={g.id}>
                <Td>
                  {g.name}
                  {g.flickr_group_id && (
                    <div style={{ fontSize: 11, color: "var(--text-fade)" }}>{g.flickr_group_id}</div>
                  )}
                </Td>
                <Td>{g.category ?? "—"}</Td>
                <Td>{g.daily_limit ?? "—"}</Td>
                <Td>{g.default_enabled ? "yes" : "—"}</Td>
                <Td>
                  <button className="fp-link" onClick={() => setEditing({ form: groupToInput(g), id: g.id })}>
                    Edit
                  </button>
                  <button
                    className="fp-link"
                    style={{ marginLeft: 12, color: "var(--danger)" }}
                    onClick={() => {
                      if (confirm(`Delete group "${g.name}"? Pending submissions will be lost.`)) {
                        deleteMutation.mutate(g.id);
                      }
                    }}
                  >
                    Delete
                  </button>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <GroupForm
          initial={editing.form}
          isEdit={editing.id !== null}
          onCancel={() => setEditing(null)}
          onSubmit={(body) => saveMutation.mutate({ id: editing.id, body })}
          submitting={saveMutation.isPending}
        />
      )}
    </div>
  );
}

function groupToInput(g: Group): GroupInput {
  return {
    flickr_group_id: g.flickr_group_id ?? "",
    name: g.name,
    category: g.category ?? "",
    daily_limit: g.daily_limit,
    content_notes: g.content_notes ?? "",
    no_watermark: g.no_watermark,
    default_enabled: g.default_enabled,
  };
}

function GroupForm({
  initial,
  isEdit,
  onCancel,
  onSubmit,
  submitting,
}: {
  initial: GroupInput;
  isEdit: boolean;
  onCancel: () => void;
  onSubmit: (body: GroupInput) => void;
  submitting: boolean;
}) {
  const [form, setForm] = useState<GroupInput>(initial);

  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit(form);
  }

  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "grid", placeItems: "center", zIndex: 100 }} onClick={onCancel}>
      <form className="fp-card" style={{ width: 520, display: "grid", gap: 12 }} onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div style={{ fontSize: 16, fontWeight: 500 }}>{isEdit ? "Edit group" : "Add group"}</div>

        <Field label="Name">
          <input className="fp-input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Flickr group ID" hint="From the group's Flickr URL">
          <input className="fp-input" value={form.flickr_group_id ?? ""} onChange={(e) => setForm({ ...form, flickr_group_id: e.target.value })} />
        </Field>
        <Field label="Category">
          <input className="fp-input" value={form.category ?? ""} onChange={(e) => setForm({ ...form, category: e.target.value })} placeholder="Burlesque/Stage, Live Music, Portrait…" />
        </Field>
        <Field label="Daily submission limit (optional)">
          <input className="fp-input" type="number" min={0} value={form.daily_limit ?? ""} onChange={(e) => setForm({ ...form, daily_limit: e.target.value === "" ? null : Number(e.target.value) })} />
        </Field>
        <Field label="Content notes / rules">
          <textarea className="fp-textarea" rows={2} value={form.content_notes ?? ""} onChange={(e) => setForm({ ...form, content_notes: e.target.value })} />
        </Field>
        <label style={{ display: "flex", gap: 8, fontSize: 13, color: "var(--text-dim)" }}>
          <input type="checkbox" checked={form.no_watermark} onChange={(e) => setForm({ ...form, no_watermark: e.target.checked })} />
          No watermark allowed
        </label>
        <label style={{ display: "flex", gap: 8, fontSize: 13, color: "var(--text-dim)" }}>
          <input type="checkbox" checked={form.default_enabled} onChange={(e) => setForm({ ...form, default_enabled: e.target.checked })} />
          Pre-select on new posts in this category
        </label>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
          <button type="button" className="fp-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="fp-btn" disabled={submitting || !form.name}>
            {submitting ? "Saving…" : isEdit ? "Save" : "Add"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>{children}</th>;
}

function Td({ children }: { children?: React.ReactNode }) {
  return <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>{children}</td>;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
      <span>
        {label}
        {hint && <span style={{ marginLeft: 8, color: "var(--text-fade)" }}>{hint}</span>}
      </span>
      {children}
    </label>
  );
}
