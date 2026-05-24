import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  deletePost,
  listDrafts,
  listHistory,
  listScheduled,
  type Post,
  type PostUpdate,
  schedulePost,
  setPostAlbums,
  setPostGroups,
  setPostProfiles,
  updatePost,
  uploadFileWithProgress,
} from "../api/client";
import type { EditorChanges } from "../components/MetadataEditor";
import BulkEditDialog from "../components/BulkEditDialog";
import DraftCard from "../components/DraftCard";
import EmptyState from "../components/EmptyState";
import MetadataEditor from "../components/MetadataEditor";
import PageHeader from "../components/PageHeader";
import ScheduleDialog from "../components/ScheduleDialog";
import { SkeletonGrid } from "../components/Skeleton";
import SmartFillDialog from "../components/SmartFillDialog";
import StatsRow from "../components/StatsRow";
import Topbar from "../components/Topbar";
import UploadZone, { type UploadItem } from "../components/UploadZone";
import WatchFolderStatus from "../components/WatchFolderStatus";
import { usePageTitle } from "../hooks/usePageTitle";

export default function DraftQueue() {
  usePageTitle("Drafts");
  const qc = useQueryClient();
  const draftsQuery = useQuery({ queryKey: ["drafts"], queryFn: listDrafts });
  const drafts = draftsQuery.data ?? [];

  // Pull all upcoming scheduled posts (no range filter = everything pending) so the dashboard
  // counter reflects what's actually on the calendar. We refresh on a 60s interval so the
  // count stays current as posts fire.
  const { data: scheduled = [] } = useQuery({
    queryKey: ["schedule", "all"],
    queryFn: () => listScheduled(),
    refetchInterval: 60_000,
  });
  const { data: published = [] } = useQuery({
    queryKey: ["published", "all"],
    queryFn: () => listHistory(undefined, ["posted", "late"]),
    refetchInterval: 60_000,
  });

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Selected post — prefer the matching one from the (filtered) visible list, fall back to
  // the unfiltered drafts list, then the first visible if anything remains.
  const selected = useMemo(
    () =>
      drafts.find((d) => d.id === selectedId) ?? null,
    [drafts, selectedId],
  );

  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [scheduling, setScheduling] = useState<Post | null>(null);
  const [multiSelect, setMultiSelect] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [smartFillOpen, setSmartFillOpen] = useState(false);
  const [bulkEditOpen, setBulkEditOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<"newest" | "oldest" | "captured" | "largest" | "ready">("newest");
  const [filterReady, setFilterReady] = useState(false);

  const visibleDrafts = useMemo(() => {
    let list = drafts;
    if (filterReady) {
      list = list.filter((p) => !!p.title && !!p.tags);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((p) =>
        (p.title || "").toLowerCase().includes(q) ||
        (p.original_filename || "").toLowerCase().includes(q) ||
        (p.tags || "").toLowerCase().includes(q) ||
        (p.camera_model || "").toLowerCase().includes(q) ||
        (p.lens || "").toLowerCase().includes(q),
      );
    }
    const arr = [...list];
    arr.sort((a, b) => {
      switch (sortKey) {
        case "oldest":
          return a.created_at.localeCompare(b.created_at);
        case "captured":
          return (b.captured_at || "").localeCompare(a.captured_at || "");
        case "largest":
          return (b.file_size_bytes ?? 0) - (a.file_size_bytes ?? 0);
        case "ready": {
          const aReady = !!a.title && !!a.tags ? 1 : 0;
          const bReady = !!b.title && !!b.tags ? 1 : 0;
          if (aReady !== bReady) return bReady - aReady;
          return b.created_at.localeCompare(a.created_at);
        }
        default:
          return b.created_at.localeCompare(a.created_at);
      }
    });
    return arr;
  }, [drafts, search, sortKey, filterReady]);

  function toggleCheck(id: string) {
    setCheckedIds((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  function exitMultiSelect() {
    setMultiSelect(false);
    setCheckedIds(new Set());
  }

  const updateMutation = useMutation({
    mutationFn: async ({ id, changes }: { id: string; changes: EditorChanges }) => {
      const body: PostUpdate = {
        title: changes.title,
        description: changes.description,
        tags: changes.tags,
        privacy: changes.privacy,
        safety_level: changes.safety_level,
        content_type: changes.content_type,
      };
      const saved = await updatePost(id, body);
      await setPostAlbums(id, changes.album_ids);
      await setPostGroups(id, changes.group_ids);
      await setPostProfiles(id, changes.profile_ids);
      return saved;
    },
    onSuccess: (saved) => {
      qc.setQueryData<Post[]>(["drafts"], (old) =>
        (old ?? []).map((p) => (p.id === saved.id ? saved : p)),
      );
      void qc.invalidateQueries({ queryKey: ["post-albums", saved.id] });
      void qc.invalidateQueries({ queryKey: ["post-groups", saved.id] });
      void qc.invalidateQueries({ queryKey: ["post-profiles", saved.id] });
      void qc.invalidateQueries({ queryKey: ["merged-tags", saved.id] });
    },
  });

  const scheduleMutation = useMutation({
    mutationFn: ({ id, iso }: { id: string; iso: string }) => schedulePost(id, iso),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      setScheduling(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deletePost(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      void qc.invalidateQueries({ queryKey: ["published"] });
      if (selectedId === id) setSelectedId(null);
      setCheckedIds((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
    },
  });

  async function deleteSelected() {
    const ids = [...checkedIds];
    if (ids.length === 0) return;
    if (!confirm(`Delete ${ids.length} draft${ids.length === 1 ? "" : "s"}? This removes original + thumbnail files. Photos already on Flickr stay there.`)) return;
    for (const id of ids) {
      try {
        await deleteMutation.mutateAsync(id);
      } catch (e) {
        console.error("delete failed for", id, e);
      }
    }
    exitMultiSelect();
  }

  async function runUpload(item: UploadItem, allowDuplicate: boolean) {
    setUploads((prev) =>
      prev.map((u) => (u.id === item.id ? { ...u, state: "uploading", progress: 0 } : u)),
    );
    try {
      await uploadFileWithProgress(item.file, {
        allowDuplicate,
        onProgress: (stage, fraction) => {
          setUploads((prev) =>
            prev.map((u) =>
              u.id === item.id
                ? {
                    ...u,
                    state: stage === "done" ? "success" : stage,
                    progress: fraction,
                  }
                : u,
            ),
          );
        },
      });
      void qc.invalidateQueries({ queryKey: ["drafts"] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const detail = (e.payload as { detail?: { duplicate_of?: string } })?.detail;
        setUploads((prev) =>
          prev.map((u) =>
            u.id === item.id
              ? { ...u, state: "duplicate", duplicateOf: detail?.duplicate_of }
              : u,
          ),
        );
      } else {
        setUploads((prev) =>
          prev.map((u) =>
            u.id === item.id
              ? { ...u, state: "error", message: e instanceof Error ? e.message : "failed" }
              : u,
          ),
        );
      }
    }
  }

  function onAddFiles(files: File[]) {
    const next: UploadItem[] = files.map((file) => ({
      id: `${file.name}-${file.size}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      file,
      state: "queued",
    }));
    setUploads((prev) => [...prev, ...next]);
    next.forEach((it) => void runUpload(it, false));
  }

  function onRetryDuplicate(id: string) {
    const it = uploads.find((u) => u.id === id);
    if (it) void runUpload(it, true);
  }

  function onDismiss(id: string) {
    setUploads((prev) => prev.filter((u) => u.id !== id));
  }

  // Calendar week (Sun→Sat) starting today's Sunday — matches the calendar view layout.
  const now = new Date();
  const startOfWeek = new Date(now.getFullYear(), now.getMonth(), now.getDate() - now.getDay());
  const endOfWeek = new Date(startOfWeek);
  endOfWeek.setDate(endOfWeek.getDate() + 7);

  const scheduledPending = scheduled.filter((s) => s.status === "pending");
  const scheduledThisWeek = scheduledPending.filter((s) => {
    if (!s.scheduled_at) return false;
    const t = new Date(s.scheduled_at + "Z").getTime();
    return t >= startOfWeek.getTime() && t < endOfWeek.getTime();
  });

  const stats = [
    { label: "Drafts", value: drafts.length },
    { label: "Ready to schedule", value: drafts.filter((p) => p.title && p.tags).length },
    { label: "Scheduled this week", value: scheduledThisWeek.length },
    { label: "Scheduled total", value: scheduledPending.length },
    { label: "Published", value: published.length },
  ];

  return (
    <>
      <Topbar />
      <div className="fp-page fp-fade-in">
        <PageHeader
          title="Draft Queue"
          subtitle="Lightroom export → import pipeline → review → schedule. The pipeline pre-fills title, description, and tags from any IPTC metadata it finds."
        />

        <StatsRow stats={stats} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: 16, alignItems: "start", marginBottom: 24 }}>
          <UploadZone
            items={uploads}
            onAdd={onAddFiles}
            onRetryDuplicate={onRetryDuplicate}
            onDismiss={onDismiss}
          />
          <WatchFolderStatus />
        </div>

        {draftsQuery.isLoading ? (
          <SkeletonGrid count={8} cardHeight={220} />
        ) : drafts.length === 0 ? (
          <EmptyState />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) 380px",
              gap: 24,
              alignItems: "start",
            }}
          >
            <div style={{ display: "grid", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                {!multiSelect ? (
                  <>
                    <button
                      className="fp-btn-ghost"
                      onClick={() => setMultiSelect(true)}
                      style={{ padding: "6px 12px", fontSize: 13 }}
                    >
                      Select multiple
                    </button>
                    <input
                      className="fp-input"
                      placeholder="Search title / filename / tag / camera"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      style={{ flex: 1, minWidth: 260, padding: "6px 12px", fontSize: 13 }}
                    />
                    <button
                      onClick={() => setFilterReady((v) => !v)}
                      title="Show only drafts with both a title and tags"
                      style={{
                        background: filterReady ? "var(--teal)" : "transparent",
                        color: filterReady ? "#0a1f17" : "var(--text-dim)",
                        border: filterReady ? "0" : "0.5px solid var(--border-strong)",
                        borderRadius: 8,
                        padding: "6px 12px",
                        fontSize: 13,
                        fontWeight: filterReady ? 500 : 400,
                        cursor: "pointer",
                      }}
                    >
                      Ready only
                    </button>
                    <select
                      className="fp-select"
                      value={sortKey}
                      onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
                      style={{ width: 180, padding: "6px 12px", fontSize: 13 }}
                    >
                      <option value="newest">Newest first</option>
                      <option value="oldest">Oldest first</option>
                      <option value="captured">Capture date (recent)</option>
                      <option value="largest">Largest file</option>
                      <option value="ready">Ready first</option>
                    </select>
                    <span style={{ fontSize: 12, color: "var(--text-fade)" }}>
                      {visibleDrafts.length}
                      {visibleDrafts.length !== drafts.length && ` of ${drafts.length}`}
                    </span>
                  </>
                ) : (
                  <>
                    <button
                      className="fp-btn-ghost"
                      onClick={exitMultiSelect}
                      style={{ padding: "6px 12px", fontSize: 13 }}
                    >
                      Cancel
                    </button>
                    <span style={{ fontSize: 13, color: "var(--text-dim)" }}>
                      {checkedIds.size} selected
                    </span>
                    <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
                      <button
                        className="fp-link"
                        style={{ fontSize: 13 }}
                        onClick={() => setCheckedIds(new Set(visibleDrafts.map((d) => d.id)))}
                      >
                        Select all visible
                      </button>
                      <button
                        onClick={() => void deleteSelected()}
                        disabled={checkedIds.size === 0 || deleteMutation.isPending}
                        style={{
                          background: "transparent",
                          color: "var(--danger)",
                          border: "0.5px solid rgba(245,156,156,0.4)",
                          borderRadius: 8,
                          padding: "6px 12px",
                          fontSize: 13,
                          cursor: checkedIds.size > 0 ? "pointer" : "not-allowed",
                          opacity: checkedIds.size > 0 ? 1 : 0.5,
                        }}
                      >
                        Delete ({checkedIds.size})
                      </button>
                      <button
                        className="fp-btn-ghost"
                        disabled={checkedIds.size === 0}
                        onClick={() => setBulkEditOpen(true)}
                        style={{ padding: "6px 14px", fontSize: 13 }}
                      >
                        Bulk Edit ({checkedIds.size})
                      </button>
                      <button
                        className="fp-btn"
                        disabled={checkedIds.size === 0}
                        onClick={() => setSmartFillOpen(true)}
                        style={{ padding: "6px 14px", fontSize: 13 }}
                      >
                        Smart Fill ({checkedIds.size})
                      </button>
                    </div>
                  </>
                )}
              </div>
              <div className="fp-grid-cards">
                {visibleDrafts.map((p) => (
                  <DraftCard
                    key={p.id}
                    post={p}
                    selected={selected?.id === p.id}
                    onSelect={() => setSelectedId(p.id)}
                    onDelete={() => deleteMutation.mutate(p.id)}
                    multiSelectMode={multiSelect}
                    isChecked={checkedIds.has(p.id)}
                    onToggleCheck={() => toggleCheck(p.id)}
                  />
                ))}
                {visibleDrafts.length === 0 && (
                  <div style={{ gridColumn: "1 / -1", padding: 40, textAlign: "center", color: "var(--text-fade)", fontSize: 13 }}>
                    No drafts match the current filters.
                  </div>
                )}
              </div>
            </div>

            {selected && (
              <div style={{ position: "sticky", top: 80 }}>
                <MetadataEditor
                  key={selected.id}
                  post={selected}
                  saving={updateMutation.isPending}
                  onSave={async (changes) => {
                    await updateMutation.mutateAsync({ id: selected.id, changes });
                  }}
                  onSchedule={() => setScheduling(selected)}
                  onDelete={() => {
                    const label = selected.title || selected.original_filename || "this draft";
                    if (confirm(`Delete "${label}"? Removes original + thumbnail files. If it's already on Flickr, the Flickr photo stays.`)) {
                      void deleteMutation.mutateAsync(selected.id);
                    }
                  }}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {scheduling && (
        <ScheduleDialog
          postTitle={scheduling.title || scheduling.original_filename || "(untitled)"}
          onCancel={() => setScheduling(null)}
          onSubmit={async (iso) => {
            await scheduleMutation.mutateAsync({ id: scheduling.id, iso });
          }}
        />
      )}

      {smartFillOpen && (
        <SmartFillDialog
          postIds={[...checkedIds]}
          onCancel={() => setSmartFillOpen(false)}
          onConfirmed={() => {
            setSmartFillOpen(false);
            exitMultiSelect();
            void qc.invalidateQueries({ queryKey: ["drafts"] });
            void qc.invalidateQueries({ queryKey: ["schedule"] });
          }}
        />
      )}

      {bulkEditOpen && (
        <BulkEditDialog
          postIds={[...checkedIds]}
          onCancel={() => setBulkEditOpen(false)}
          onApplied={() => {
            setBulkEditOpen(false);
            void qc.invalidateQueries({ queryKey: ["drafts"] });
            // Per-post derived data needs refresh too
            for (const id of checkedIds) {
              void qc.invalidateQueries({ queryKey: ["post-albums", id] });
              void qc.invalidateQueries({ queryKey: ["post-groups", id] });
              void qc.invalidateQueries({ queryKey: ["post-profiles", id] });
              void qc.invalidateQueries({ queryKey: ["merged-tags", id] });
              void qc.invalidateQueries({ queryKey: ["post", id] });
            }
          }}
        />
      )}
    </>
  );
}
