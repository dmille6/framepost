import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  getPost,
  getPostPerformers,
  listAlbums,
  listConnectedPlatforms,
  listGroups,
  listProfiles,
  setPostAlbums,
  setPostGroups,
  setPostPerformers,
  setPostProfiles,
  updatePost,
  type Performer,
  type PostUpdate,
} from "../api/client";
import MultiSelectChips from "./MultiSelectChips";
import PerformersField from "./PerformersField";

type Props = {
  postIds: string[];
  onCancel: () => void;
  onApplied: () => void;
};

type Apply = "off" | "on";  // per-section toggle

export default function BulkEditDialog({ postIds, onCancel, onApplied }: Props) {
  // Field values
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [tagsMode, setTagsMode] = useState<"append" | "replace">("append");
  const [privacy, setPrivacy] = useState("private");
  const [safety, setSafety] = useState("safe");
  const [contentType, setContentType] = useState("photo");
  const [albumIds, setAlbumIds] = useState<Set<string>>(new Set());
  const [groupIds, setGroupIds] = useState<Set<string>>(new Set());
  const [profileIds, setProfileIds] = useState<Set<string>>(new Set());
  const [targetSet, setTargetSet] = useState<Set<string>>(new Set());
  const [bulkPerformers, setBulkPerformers] = useState<Performer[]>([]);
  const [performersMode, setPerformersMode] = useState<"append" | "replace">("append");

  // Per-section apply toggles. Selects + multi-select sections need explicit opt-in
  // because "empty" is ambiguous (vs text fields where empty == don't change).
  const [applyPrivacy, setApplyPrivacy] = useState<Apply>("off");
  const [applySafety, setApplySafety] = useState<Apply>("off");
  const [applyType, setApplyType] = useState<Apply>("off");
  const [applyAlbums, setApplyAlbums] = useState<Apply>("off");
  const [applyGroups, setApplyGroups] = useState<Apply>("off");
  const [applyProfiles, setApplyProfiles] = useState<Apply>("off");
  const [applyTargets, setApplyTargets] = useState<Apply>("off");

  const { data: albums = [] } = useQuery({ queryKey: ["albums"], queryFn: listAlbums });
  const { data: groups = [] } = useQuery({ queryKey: ["groups"], queryFn: listGroups });
  const { data: profiles = [] } = useQuery({ queryKey: ["profiles"], queryFn: listProfiles });
  const { data: connectedPlatforms = [] } = useQuery({
    queryKey: ["connected-platforms"],
    queryFn: listConnectedPlatforms,
  });

  const [progress, setProgress] = useState<{ done: number; failed: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const apply = useMutation({
    mutationFn: async () => {
      const total = postIds.length;
      setProgress({ done: 0, failed: 0, total });
      setError(null);

      // Build the base PATCH body — only fields the user actually filled in.
      const baseBody: PostUpdate = {};
      if (title.trim()) baseBody.title = title.trim();
      if (description.trim()) baseBody.description = description.trim();
      if (applyPrivacy === "on") baseBody.privacy = privacy;
      if (applySafety === "on") baseBody.safety_level = safety;
      if (applyType === "on") baseBody.content_type = contentType;
      if (applyTargets === "on") baseBody.target_platforms = [...targetSet];

      let done = 0;
      let failed = 0;

      // Iterate sequentially so the backend isn't slammed and progress is accurate.
      for (const postId of postIds) {
        try {
          // Tags handling: append (merge with existing, dedup) vs replace.
          let body: PostUpdate = { ...baseBody };
          if (tags.trim()) {
            if (tagsMode === "replace") {
              body.tags = tags.trim();
            } else {
              const existing = await getPost(postId);
              body.tags = mergeTags(existing.tags, tags);
            }
          }

          if (Object.keys(body).length > 0) {
            await updatePost(postId, body);
          }
          if (applyAlbums === "on") await setPostAlbums(postId, [...albumIds]);
          if (applyGroups === "on") await setPostGroups(postId, [...groupIds]);
          if (applyProfiles === "on") await setPostProfiles(postId, [...profileIds]);

          // Performers: append (union by performer.id) or replace.
          if (bulkPerformers.length > 0) {
            let finalIds: string[];
            if (performersMode === "replace") {
              finalIds = bulkPerformers.map((p) => p.id);
            } else {
              const existing = await getPostPerformers(postId);
              const seen = new Set(existing.map((p) => p.id));
              const additions = bulkPerformers.filter((p) => !seen.has(p.id));
              finalIds = [...existing.map((p) => p.id), ...additions.map((p) => p.id)];
            }
            await setPostPerformers(postId, finalIds);
          }

          done += 1;
        } catch (e) {
          failed += 1;
          console.error(`bulk edit failed for post ${postId}`, e);
        }
        setProgress({ done: done + failed, failed, total });
      }

      if (failed > 0) {
        setError(`${failed} of ${total} draft${total === 1 ? "" : "s"} failed to update.`);
      }
    },
    onSuccess: () => {
      // Small settle delay so the user can see "10 of 10" before the dialog closes.
      setTimeout(() => onApplied(), 300);
    },
  });

  const willChangeAnything =
    !!title.trim() ||
    !!description.trim() ||
    !!tags.trim() ||
    bulkPerformers.length > 0 ||
    applyPrivacy === "on" ||
    applySafety === "on" ||
    applyType === "on" ||
    applyAlbums === "on" ||
    applyGroups === "on" ||
    applyProfiles === "on" ||
    applyTargets === "on";

  const busy = apply.isPending;

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24 }}
      onClick={busy ? undefined : onCancel}
    >
      <div
        className="fp-card fp-fade"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(680px, 100%)",
          maxHeight: "92vh",
          overflow: "auto",
          padding: 0,
          boxShadow: "var(--shadow-lg)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "14px 20px",
            borderBottom: "0.5px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            position: "sticky",
            top: 0,
            background: "var(--card)",
            zIndex: 1,
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Bulk edit
            </div>
            <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 2 }}>
              Apply to {postIds.length} draft{postIds.length === 1 ? "" : "s"} — empty fields stay untouched on each draft.
            </div>
          </div>
          <button
            onClick={onCancel}
            disabled={busy}
            aria-label="Close"
            style={{
              background: "transparent",
              border: "0.5px solid var(--border-strong)",
              borderRadius: 8,
              width: 32,
              height: 32,
              color: "var(--text-dim)",
              cursor: busy ? "not-allowed" : "pointer",
              display: "grid",
              placeItems: "center",
              fontSize: 16,
            }}
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: 20, display: "grid", gap: 16, overflow: "auto" }}>
          <Field label="Title" hint="Replaces the title on all selected drafts. Leave blank to keep each draft's existing title.">
            <input
              className="fp-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="(don't change)"
              disabled={busy}
            />
          </Field>

          <Field label="Description" hint="Replaces. Leave blank to keep existing.">
            <textarea
              className="fp-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="(don't change)"
              rows={3}
              disabled={busy}
            />
          </Field>

          <Field
            label="Tags"
            hint={
              tagsMode === "append"
                ? "Comma-separated. Added to each draft's existing tags, deduplicated."
                : "Comma-separated. Replaces each draft's existing tags."
            }
          >
            <input
              className="fp-input"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder={
                tagsMode === "append"
                  ? "burlesque, performer-name (added to existing)"
                  : "burlesque, performer-name (replaces existing)"
              }
              disabled={busy}
            />
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <ModeChip
                active={tagsMode === "append"}
                onClick={() => setTagsMode("append")}
                disabled={busy}
              >
                Append (recommended)
              </ModeChip>
              <ModeChip
                active={tagsMode === "replace"}
                onClick={() => setTagsMode("replace")}
                disabled={busy}
              >
                Replace
              </ModeChip>
            </div>
          </Field>

          <Field
            label="Performers"
            hint={
              performersMode === "append"
                ? "Adds these performers to each draft's existing list (deduplicated)."
                : "Replaces each draft's performer list with these."
            }
          >
            <PerformersField selected={bulkPerformers} onChange={setBulkPerformers} label="" />
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <ModeChip
                active={performersMode === "append"}
                onClick={() => setPerformersMode("append")}
                disabled={busy}
              >
                Append (recommended)
              </ModeChip>
              <ModeChip
                active={performersMode === "replace"}
                onClick={() => setPerformersMode("replace")}
                disabled={busy}
              >
                Replace
              </ModeChip>
            </div>
          </Field>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            <ToggleField
              label="Privacy"
              applied={applyPrivacy === "on"}
              onToggle={(on) => setApplyPrivacy(on ? "on" : "off")}
              disabled={busy}
            >
              <select
                className="fp-select"
                value={privacy}
                onChange={(e) => setPrivacy(e.target.value)}
                disabled={busy || applyPrivacy === "off"}
              >
                <option value="private">Private</option>
                <option value="friends_family">Friends &amp; Family</option>
                <option value="public">Public</option>
              </select>
            </ToggleField>
            <ToggleField
              label="Safety"
              applied={applySafety === "on"}
              onToggle={(on) => setApplySafety(on ? "on" : "off")}
              disabled={busy}
            >
              <select
                className="fp-select"
                value={safety}
                onChange={(e) => setSafety(e.target.value)}
                disabled={busy || applySafety === "off"}
              >
                <option value="safe">Safe</option>
                <option value="moderate">Moderate</option>
                <option value="restricted">Restricted</option>
              </select>
            </ToggleField>
            <ToggleField
              label="Type"
              applied={applyType === "on"}
              onToggle={(on) => setApplyType(on ? "on" : "off")}
              disabled={busy}
            >
              <select
                className="fp-select"
                value={contentType}
                onChange={(e) => setContentType(e.target.value)}
                disabled={busy || applyType === "off"}
              >
                <option value="photo">Photo</option>
                <option value="screenshot">Screenshot</option>
                <option value="other">Other</option>
              </select>
            </ToggleField>
          </div>

          <SectionToggle
            label="Albums"
            note="Replaces each draft's album selections."
            applied={applyAlbums === "on"}
            onToggle={(on) => setApplyAlbums(on ? "on" : "off")}
            disabled={busy}
          >
            <MultiSelectChips
              label="Albums"
              options={albums.map((a) => ({ id: a.id, label: a.name }))}
              selected={albumIds}
              onChange={setAlbumIds}
              emptyMessage="No albums synced yet."
            />
          </SectionToggle>

          <SectionToggle
            label="Groups"
            note="Replaces each draft's group selections."
            applied={applyGroups === "on"}
            onToggle={(on) => setApplyGroups(on ? "on" : "off")}
            disabled={busy}
          >
            <MultiSelectChips
              label="Groups"
              options={groups.map((g) => ({
                id: g.id,
                label: g.name,
                sublabel: g.category ?? undefined,
              }))}
              selected={groupIds}
              onChange={setGroupIds}
              emptyMessage="Add groups in Settings → Groups."
            />
          </SectionToggle>

          <SectionToggle
            label="Tag profiles"
            note="Replaces each draft's profile selections."
            applied={applyProfiles === "on"}
            onToggle={(on) => setApplyProfiles(on ? "on" : "off")}
            disabled={busy}
          >
            <MultiSelectChips
              label="Profiles"
              options={profiles.filter((p) => !p.is_default).map((p) => ({ id: p.id, label: p.name }))}
              selected={profileIds}
              onChange={setProfileIds}
              emptyMessage="Add profiles in Settings → Tag Profiles."
            />
          </SectionToggle>

          {connectedPlatforms.length > 0 && (
            <SectionToggle
              label="Publish targets"
              note="Replaces each draft's target-platforms list."
              applied={applyTargets === "on"}
              onToggle={(on) => setApplyTargets(on ? "on" : "off")}
              disabled={busy}
            >
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {connectedPlatforms.map((p) => {
                  const checked = targetSet.has(p.platform);
                  return (
                    <label
                      key={p.platform}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "6px 12px",
                        border: `0.5px solid ${checked ? "rgba(93,202,165,0.3)" : "var(--border-strong)"}`,
                        background: checked ? "var(--teal-tint)" : "transparent",
                        color: checked ? "var(--text)" : "var(--text-dim)",
                        borderRadius: 999,
                        fontSize: 12,
                        fontWeight: checked ? 500 : 400,
                        cursor: busy ? "not-allowed" : "pointer",
                        opacity: applyTargets === "off" ? 0.5 : 1,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={busy || applyTargets === "off"}
                        onChange={(e) => {
                          const next = new Set(targetSet);
                          if (e.target.checked) next.add(p.platform);
                          else next.delete(p.platform);
                          setTargetSet(next);
                        }}
                        style={{ accentColor: "var(--teal)", margin: 0 }}
                      />
                      {p.label}
                    </label>
                  );
                })}
              </div>
            </SectionToggle>
          )}

          {error && (
            <div
              style={{
                padding: "10px 12px",
                borderRadius: 8,
                background: "var(--danger-tint)",
                color: "var(--danger)",
                fontSize: 12,
                border: "0.5px solid rgba(245,156,156,0.2)",
              }}
            >
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "14px 20px",
            borderTop: "0.5px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            position: "sticky",
            bottom: 0,
            background: "var(--card)",
          }}
        >
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {progress
              ? `${progress.done} of ${progress.total}${progress.failed > 0 ? ` (${progress.failed} failed)` : ""}`
              : willChangeAnything
                ? `Will update ${postIds.length} draft${postIds.length === 1 ? "" : "s"}`
                : "Fill in at least one field"}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="fp-btn-ghost" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
            <button
              className="fp-btn"
              onClick={() => apply.mutate()}
              disabled={busy || !willChangeAnything}
            >
              {busy && <span className="fp-spinner" />}
              {busy ? "Applying" : `Apply to ${postIds.length}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Field with a label + optional hint, no apply toggle (text fields use empty == skip).
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 11, color: "var(--text-fade)" }}>{hint}</span>}
    </label>
  );
}

// Field with explicit apply checkbox — for selects/multi-selects where "blank" is ambiguous.
function ToggleField({
  label,
  applied,
  onToggle,
  disabled,
  children,
}: {
  label: string;
  applied: boolean;
  onToggle: (on: boolean) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          fontSize: 12,
          color: "var(--text-dim)",
          fontWeight: 500,
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        <span>{label}</span>
        <input
          type="checkbox"
          checked={applied}
          onChange={(e) => onToggle(e.target.checked)}
          disabled={disabled}
          style={{ accentColor: "var(--teal)", margin: 0 }}
          title="Apply this field to all selected drafts"
        />
      </label>
      <div style={{ opacity: applied ? 1 : 0.5 }}>{children}</div>
    </div>
  );
}

function SectionToggle({
  label,
  note,
  applied,
  onToggle,
  disabled,
  children,
}: {
  label: string;
  note: string;
  applied: boolean;
  onToggle: (on: boolean) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          fontSize: 13,
          color: "var(--text)",
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        <input
          type="checkbox"
          checked={applied}
          onChange={(e) => onToggle(e.target.checked)}
          disabled={disabled}
          style={{ width: 16, height: 16, accentColor: "var(--teal)", margin: 0 }}
        />
        <span style={{ fontWeight: 500 }}>{label}</span>
        <span style={{ fontSize: 11, color: "var(--text-fade)", fontWeight: 400 }}>
          {note}
        </span>
      </label>
      {applied && <div style={{ paddingLeft: 26 }}>{children}</div>}
    </div>
  );
}

function ModeChip({
  active,
  onClick,
  disabled,
  children,
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        background: active ? "var(--teal-tint)" : "transparent",
        color: active ? "var(--teal)" : "var(--text-dim)",
        border: `0.5px solid ${active ? "rgba(93,202,165,0.3)" : "var(--border-strong)"}`,
        borderRadius: 999,
        padding: "4px 10px",
        fontSize: 11,
        fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      {children}
    </button>
  );
}

function mergeTags(existing: string | null | undefined, incoming: string): string {
  const parse = (s: string) =>
    s.split(",").map((t) => t.trim()).filter(Boolean);
  const have = parse(existing || "");
  const haveLower = new Set(have.map((t) => t.toLowerCase()));
  const additions = parse(incoming).filter((t) => !haveLower.has(t.toLowerCase()));
  if (additions.length === 0) return have.join(", ");
  return [...have, ...additions].join(", ");
}
