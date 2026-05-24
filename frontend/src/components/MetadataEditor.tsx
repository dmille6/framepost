import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { useMutation } from "@tanstack/react-query";

import {
  aiSuggestForPost,
  fetchAIStatus,
  getMergedTags,
  getPostAlbums,
  getPostGroups,
  getPostPerformers,
  getPostProfiles,
  listAlbums,
  listConnectedPlatforms,
  listGroups,
  listProfiles,
  thumbnailUrl,
  type Performer,
  type Post,
} from "../api/client";
import AISuggestPanel from "./AISuggestPanel";
import ApplyTemplateDialog from "./ApplyTemplateDialog";
import Lightbox from "./Lightbox";
import MultiSelectChips from "./MultiSelectChips";
import PerformersField from "./PerformersField";
import TagsInput from "./TagsInput";
import TrendingPanel from "./TrendingPanel";

export type EditorChanges = {
  title: string | null;
  description: string | null;
  tags: string | null;
  privacy: string;
  safety_level: string;
  content_type: string;
  album_ids: string[];
  group_ids: string[];
  profile_ids: string[];
  performer_ids: string[];
  target_platforms: string[] | null;
};

type Props = {
  post: Post;
  onSave: (changes: EditorChanges) => Promise<void>;
  onSchedule: () => void;
  onDelete?: () => void;
  scheduleLabel?: string;
  saving: boolean;
};

const MAX_GROUPS = 5;
const WARN_GROUPS = 8;

export default function MetadataEditor({ post, onSave, onSchedule, onDelete, scheduleLabel, saving }: Props) {
  const [title, setTitle] = useState(post.title ?? "");
  const [description, setDescription] = useState(post.description ?? "");
  const [tags, setTags] = useState(post.tags ?? "");
  const [privacy, setPrivacy] = useState(post.privacy ?? "private");
  const [safety, setSafety] = useState(post.safety_level ?? "safe");
  const [contentType, setContentType] = useState(post.content_type ?? "photo");

  const { data: albums = [] } = useQuery({ queryKey: ["albums"], queryFn: listAlbums });
  const { data: groups = [] } = useQuery({ queryKey: ["groups"], queryFn: listGroups });
  const { data: profiles = [] } = useQuery({ queryKey: ["profiles"], queryFn: listProfiles });
  const { data: postAlbums = [] } = useQuery({
    queryKey: ["post-albums", post.id],
    queryFn: () => getPostAlbums(post.id),
  });
  const { data: postGroups = [] } = useQuery({
    queryKey: ["post-groups", post.id],
    queryFn: () => getPostGroups(post.id),
  });
  const { data: postProfiles = [] } = useQuery({
    queryKey: ["post-profiles", post.id],
    queryFn: () => getPostProfiles(post.id),
  });
  const { data: postPerformers = [] } = useQuery({
    queryKey: ["post-performers", post.id],
    queryFn: () => getPostPerformers(post.id),
  });
  const { data: merged } = useQuery({
    queryKey: ["merged-tags", post.id],
    queryFn: () => getMergedTags(post.id),
    refetchInterval: 30_000,
  });
  const { data: aiStatus } = useQuery({ queryKey: ["ai-status"], queryFn: fetchAIStatus });

  const [albumIds, setAlbumIds] = useState<Set<string>>(new Set());
  const [groupIds, setGroupIds] = useState<Set<string>>(new Set());
  const [profileIds, setProfileIds] = useState<Set<string>>(new Set());
  const [performers, setPerformers] = useState<Performer[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [templateOpen, setTemplateOpen] = useState(false);
  const [targetSet, setTargetSet] = useState<Set<string>>(new Set());

  const { data: connectedPlatforms = [] } = useQuery({
    queryKey: ["connected-platforms"],
    queryFn: listConnectedPlatforms,
  });

  useEffect(() => {
    setTitle(post.title ?? "");
    setDescription(post.description ?? "");
    setTags(post.tags ?? "");
    setPrivacy(post.privacy ?? "private");
    setSafety(post.safety_level ?? "safe");
    setContentType(post.content_type ?? "photo");
  }, [post.id]);

  useEffect(() => { setAlbumIds(new Set(postAlbums)); }, [post.id, postAlbums.join(",")]);
  useEffect(() => { setGroupIds(new Set(postGroups)); }, [post.id, postGroups.join(",")]);
  useEffect(() => { setProfileIds(new Set(postProfiles)); }, [post.id, postProfiles.join(",")]);
  useEffect(() => { setPerformers(postPerformers); }, [post.id, postPerformers.map((p) => p.id).join(",")]);

  // Default for target_platforms: if post has an explicit list, use it; otherwise default
  // to all connected platforms with default_target=on (which the user already configured
  // in Settings → Platforms).
  useEffect(() => {
    if (post.target_platforms !== null && post.target_platforms !== undefined) {
      setTargetSet(new Set(post.target_platforms));
    } else {
      setTargetSet(
        new Set(connectedPlatforms.filter((p) => p.default_target).map((p) => p.platform)),
      );
    }
  }, [post.id, post.target_platforms, connectedPlatforms.map((p) => p.platform).join(",")]);

  const defaultTargetSet = new Set(
    connectedPlatforms.filter((p) => p.default_target).map((p) => p.platform),
  );
  const expectedTargetSet =
    post.target_platforms !== null && post.target_platforms !== undefined
      ? new Set(post.target_platforms)
      : defaultTargetSet;

  const dirty =
    (title || "") !== (post.title ?? "") ||
    (description || "") !== (post.description ?? "") ||
    (tags || "") !== (post.tags ?? "") ||
    privacy !== (post.privacy ?? "private") ||
    safety !== (post.safety_level ?? "safe") ||
    contentType !== (post.content_type ?? "photo") ||
    !setsEqual(albumIds, new Set(postAlbums)) ||
    !setsEqual(groupIds, new Set(postGroups)) ||
    !setsEqual(profileIds, new Set(postProfiles)) ||
    performers.map((p) => p.id).join(",") !== postPerformers.map((p) => p.id).join(",") ||
    !setsEqual(targetSet, expectedTargetSet);

  function handleSave() {
    // If user's selection matches what defaults would produce, send null to mean "use defaults".
    // This way newly-added platforms automatically apply to future posts.
    const targetsToSave =
      setsEqual(targetSet, defaultTargetSet) ? null : [...targetSet];
    void onSave({
      title: title.trim() || null,
      description: description.trim() || null,
      tags: tags.trim() || null,
      privacy,
      safety_level: safety,
      content_type: contentType,
      album_ids: [...albumIds],
      group_ids: [...groupIds],
      profile_ids: [...profileIds],
      performer_ids: performers.map((p) => p.id),
      target_platforms: targetsToSave,
    });
  }

  const mp = post.width && post.height ? ((post.width * post.height) / 1_000_000).toFixed(1) : null;
  const captured = post.captured_at ? new Date(post.captured_at).toLocaleString() : "—";

  function addTagInline(tag: string) {
    const parts = tags.split(",").map((t) => t.trim()).filter(Boolean);
    if (!parts.some((p) => p.toLowerCase() === tag.toLowerCase())) {
      setTags(parts.length ? `${tags.trim().replace(/,\s*$/, "")}, ${tag}` : tag);
    }
  }

  function addTagsInline(toAdd: string[]) {
    if (toAdd.length === 0) return;
    const parts = tags.split(",").map((t) => t.trim()).filter(Boolean);
    const existing = new Set(parts.map((p) => p.toLowerCase()));
    const fresh = toAdd.filter((t) => !existing.has(t.toLowerCase()));
    if (fresh.length === 0) return;
    setTags(parts.length ? `${parts.join(", ")}, ${fresh.join(", ")}` : fresh.join(", "));
  }

  // Default profile is always applied; non-default profiles are togglable.
  const togglableProfiles = profiles.filter((p) => !p.is_default);
  const defaultProfile = profiles.find((p) => p.is_default);

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16 }}>
      <button
        type="button"
        onClick={() => setLightboxOpen(true)}
        title="Click to view full size"
        style={{
          aspectRatio: "4 / 3",
          background: "#0a0a0a",
          borderRadius: 8,
          overflow: "hidden",
          padding: 0,
          border: 0,
          cursor: "zoom-in",
          position: "relative",
        }}
      >
        <img
          src={thumbnailUrl(post.id)}
          alt={post.title ?? ""}
          style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
        />
        <div
          style={{
            position: "absolute",
            bottom: 8,
            right: 8,
            background: "rgba(0,0,0,0.6)",
            color: "rgba(255,255,255,0.85)",
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 4,
            pointerEvents: "none",
          }}
        >
          ⤢ View full size
        </div>
      </button>

      <ReadOnlyExif
        rows={[
          ["Filename", post.original_filename ?? "—"],
          ["Dimensions", post.width && post.height ? `${post.width} × ${post.height}` : "—"],
          ["Megapixels", mp ? `${mp} MP` : "—"],
          ["File size", post.file_size_bytes ? `${(post.file_size_bytes / 1024 / 1024).toFixed(2)} MB` : "—"],
          ["Captured", captured],
          ["Camera", [post.camera_make, post.camera_model].filter(Boolean).join(" ") || "—"],
          ["Lens", post.lens ?? "—"],
          ["Exposure", [
            post.focal_length ? `${post.focal_length}mm` : null,
            post.aperture ? `f/${post.aperture}` : null,
            post.shutter_speed,
            post.iso ? `ISO ${post.iso}` : null,
          ].filter(Boolean).join(" · ") || "—"],
        ]}
      />

      <Field
        label="Title"
        hint={
          <button
            type="button"
            onClick={() => setTemplateOpen(true)}
            className="fp-link"
            style={{ fontSize: 11 }}
          >
            Apply template →
          </button>
        }
      >
        <input className="fp-input" value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <DescriptionField
        post={post}
        description={description}
        setDescription={setDescription}
        aiEnabled={aiStatus?.enabled ?? false}
        hintTitle={title}
        hintTags={tags}
      />

      <PerformersField selected={performers} onChange={setPerformers} />

      <Field label="Tags" hint="Comma-separated · Tab to autocomplete from past tags">
        <TagsInput value={tags} onChange={setTags} />
        {merged && (
          <FinalTagsPreview
            userTags={tags}
            mergedFromBackend={merged.merged}
            defaultName={defaultProfile?.name}
            stackedNames={togglableProfiles.filter((p) => profileIds.has(p.id)).map((p) => p.name)}
          />
        )}
      </Field>

      <AISuggestPanel
        postId={post.id}
        enabled={aiStatus?.enabled ?? false}
        currentTags={tags}
        currentDescription={description}
        currentTitle={title}
        onAddTag={addTagInline}
        onAddTags={addTagsInline}
        onUseDescription={(text) => setDescription(text)}
      />

      <TrendingPanel
        currentTags={tags}
        onAddTag={addTagInline}
        onAddTags={addTagsInline}
      />

      <MultiSelectChips
        label="Tag profiles"
        options={togglableProfiles.map((p) => ({
          id: p.id,
          label: p.name,
          sublabel: p.tags ? `${p.tags.split(",").length} tags` : "no tags",
        }))}
        selected={profileIds}
        onChange={setProfileIds}
        emptyMessage="No additional profiles. Add some in Settings → Tag Profiles."
      />

      <MultiSelectChips
        label="Albums"
        options={albums.map((a) => ({ id: a.id, label: a.name, sublabel: `${a.photo_count} photos` }))}
        selected={albumIds}
        onChange={setAlbumIds}
        emptyMessage="Sync albums in Settings → Albums first."
      />

      <MultiSelectChips
        label="Groups"
        options={groups.map((g) => ({ id: g.id, label: g.name, sublabel: g.category ?? undefined }))}
        selected={groupIds}
        onChange={setGroupIds}
        maxSelected={MAX_GROUPS}
        warnThreshold={WARN_GROUPS}
        emptyMessage="Add groups in Settings → Groups."
      />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <Field label="Privacy">
          <select className="fp-select" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
            <option value="private">Private</option>
            <option value="friends_family">Friends &amp; Family</option>
            <option value="public">Public</option>
          </select>
        </Field>
        <Field label="Safety">
          <select className="fp-select" value={safety} onChange={(e) => setSafety(e.target.value)}>
            <option value="safe">Safe</option>
            <option value="moderate">Moderate</option>
            <option value="restricted">Restricted</option>
          </select>
        </Field>
        <Field label="Type">
          <select className="fp-select" value={contentType} onChange={(e) => setContentType(e.target.value)}>
            <option value="photo">Photo</option>
            <option value="screenshot">Screenshot</option>
            <option value="other">Other</option>
          </select>
        </Field>
      </div>

      {connectedPlatforms.length > 0 && (
        <Field
          label="Publish targets"
          hint={
            targetSet.size === 0
              ? "Nothing checked — this post won't be published anywhere."
              : `Posts to: ${[...targetSet]
                  .map((p) => connectedPlatforms.find((c) => c.platform === p)?.label || p)
                  .join(" + ")}`
          }
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
                    cursor: "pointer",
                    userSelect: "none",
                    transition: "background 120ms ease, border-color 120ms ease",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={checked}
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
        </Field>
      )}

      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        {onDelete && (
          <button
            onClick={onDelete}
            style={{
              background: "transparent",
              color: "var(--danger)",
              border: 0,
              cursor: "pointer",
              fontSize: 13,
              padding: "8px 0",
            }}
            title="Delete this draft (does not touch Flickr)"
          >
            Delete
          </button>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 12 }}>
          <button className="fp-btn-ghost" onClick={onSchedule} disabled={dirty || saving}>
            {scheduleLabel ?? "Schedule on Flickr"}
          </button>
          <button className="fp-btn" disabled={!dirty || saving} onClick={handleSave}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
      {dirty && (
        <div style={{ fontSize: 11, color: "var(--text-fade)", textAlign: "right", marginTop: -8 }}>
          Save before scheduling.
        </div>
      )}

      {lightboxOpen && (
        <Lightbox
          postId={post.id}
          caption={post.title || post.original_filename || undefined}
          meta={[
            post.width && post.height ? `${post.width} × ${post.height}` : null,
            mp ? `${mp} MP` : null,
            [post.camera_make, post.camera_model].filter(Boolean).join(" ") || null,
            post.lens,
          ].filter(Boolean).join(" · ") || undefined}
          onClose={() => setLightboxOpen(false)}
        />
      )}

      {templateOpen && (
        <ApplyTemplateDialog
          initialTitle={title}
          initialDescription={description}
          onCancel={() => setTemplateOpen(false)}
          onApply={({ title: t, description: d }) => {
            setTitle(t);
            if (d !== null) setDescription(d);
            setTemplateOpen(false);
          }}
        />
      )}
    </div>
  );
}

function DescriptionField({
  post,
  description,
  setDescription,
  aiEnabled,
  hintTitle,
  hintTags,
}: {
  post: Post;
  description: string;
  setDescription: (v: string) => void;
  aiEnabled: boolean;
  hintTitle: string;
  hintTags: string;
}) {
  const isPolish = description.trim().length > 0;

  const aiMutation = useMutation({
    mutationFn: () =>
      aiSuggestForPost(post.id, {
        hint_title: hintTitle.trim() || null,
        hint_tags: hintTags.trim() || null,
        hint_description: description.trim() || null,
      }),
    onSuccess: (result) => {
      if (result.description) setDescription(result.description);
    },
  });

  return (
    <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span>Description</span>
        <button
          type="button"
          onClick={() => aiMutation.mutate()}
          disabled={!aiEnabled || aiMutation.isPending}
          title={
            !aiEnabled
              ? "Enable AI tagging in Settings to use this"
              : isPolish
                ? "Polish the existing description (tighten language, weave in title context)"
                : "Draft a description from the image + title"
          }
          style={{
            background: "transparent",
            color: aiEnabled ? "var(--teal)" : "var(--text-fade)",
            border: 0,
            cursor: aiEnabled ? "pointer" : "default",
            fontSize: 11,
            padding: 0,
          }}
        >
          {aiMutation.isPending
            ? (isPolish ? "Polishing…" : "Drafting…")
            : (isPolish ? "✨ Polish with AI" : "✨ Draft with AI")}
        </button>
      </div>
      <textarea
        className="fp-textarea"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={4}
      />
      {aiMutation.error && (
        <div style={{ color: "var(--danger)", fontSize: 11 }}>
          {aiMutation.error instanceof Error ? aiMutation.error.message : "AI draft failed"}
        </div>
      )}
    </label>
  );
}

function FinalTagsPreview({
  userTags,
  mergedFromBackend,
  defaultName,
  stackedNames,
}: {
  userTags: string;
  mergedFromBackend: string[];
  defaultName?: string;
  stackedNames: string[];
}) {
  // Optimistic preview: fold the user's *current* (possibly unsaved) tags into the merged list.
  const userParts = userTags.split(",").map((t) => t.trim()).filter(Boolean);
  const seen = new Set<string>();
  const final: string[] = [];
  for (const t of [...userParts, ...mergedFromBackend]) {
    const key = t.toLowerCase();
    if (key && !seen.has(key)) {
      seen.add(key);
      final.push(t);
    }
  }
  const sources = [defaultName, ...stackedNames].filter(Boolean) as string[];
  return (
    <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-fade)", lineHeight: 1.5 }}>
      <span>Final on Flickr ({final.length}):</span>{" "}
      <span style={{ color: "var(--text-dim)" }}>{final.join(", ") || "—"}</span>
      {sources.length > 0 && (
        <div>
          Stacking: <span style={{ color: "var(--text-dim)" }}>{sources.join(" + ")}</span>
        </div>
      )}
    </div>
  );
}

function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
      <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span>{label}</span>
        {hint && <span style={{ color: "var(--text-fade)" }}>{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function ReadOnlyExif({ rows }: { rows: [string, string][] }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "auto 1fr",
        rowGap: 4,
        columnGap: 16,
        fontSize: 12,
        background: "var(--bg)",
        padding: 12,
        borderRadius: 8,
      }}
    >
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <div style={{ color: "var(--text-fade)" }}>{k}</div>
          <div style={{ color: "var(--text-dim)" }}>{v}</div>
        </div>
      ))}
    </div>
  );
}
