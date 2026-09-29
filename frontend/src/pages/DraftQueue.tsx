import { useMemo, useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  deletePost,
  listDrafts,
  listHistory,
  listScheduled,
  type Post,
  uploadFileWithProgress,
} from "../api/client";

import BulkEditDialog from "../components/BulkEditDialog";
import FindReplaceDialog from "../components/FindReplaceDialog";
import CarouselDialog from "../components/CarouselDialog";
import IgCropFilmstrip from "../components/IgCropFilmstrip";
import DraftCard from "../components/DraftCard";
import EmptyState from "../components/EmptyState";
import MetadataEditor from "../components/MetadataEditor";
import PageHeader from "../components/PageHeader";
import QueueTabs from "../components/QueueTabs";
import ReelFromDraftsDialog from "../components/ReelFromDraftsDialog";
import ScheduleDialog from "../components/ScheduleDialog";
import { SkeletonGrid } from "../components/Skeleton";
import SmartFillDialog from "../components/SmartFillDialog";
import StatsRow from "../components/StatsRow";
import Topbar from "../components/Topbar";
import UploadZone, { type UploadItem } from "../components/UploadZone";
import WatchFolderStatus from "../components/WatchFolderStatus";
import { useDraftAutosaves } from "../hooks/useDraftAutosaves";
import { usePageTitle } from "../hooks/usePageTitle";
import { batchKey } from "../lib/shoots";

// --- Show/batch grouping -----------------------------------------------------
// batchKey now lives in lib/shoots.ts, shared with the Scheduled page's Shoots view so
// both pages group the same photos into the same shoots. The derivation is unchanged.

// Readiness is decided on the server (services/preflight.py) and arrives with each
// draft. It used to be computed here from title/tags/alt_text, which measured metadata
// completeness rather than whether the post could actually be delivered: a draft showed
// green while Instagram was disconnected, the original file had moved, or a carousel had
// lost a frame. It also counted blank alt text as done, because it asked whether the AI
// sweep had run rather than whether there was any alt text.
function isReady(p: Post): boolean {
  return p.preflight?.ready ?? false;
}

function readyMissing(p: Post): string[] {
  const pf = p.preflight;
  if (!pf) return ["checking…"];
  return [...pf.blockers, ...pf.warnings].map((f) =>
    f.destination ? `${f.destination}: ${f.message}` : f.message,
  );
}

const UNMATCHED = "\u0000unmatched";

/** Every show in the drafts, biggest first. Unlike the old chip row this hides nothing:
 *  the chips capped at eight batches of three-or-more, which was fine when clicking one
 *  only SELECTED it, but a filter you cannot reach is a filter that does not exist —
 *  on the live queue two shows were invisible under that rule. */
function computeBatches(drafts: Post[]): { key: string; label: string; ids: string[] }[] {
  const map = new Map<string, Post[]>();
  for (const p of drafts) {
    const key = batchKey(p) ?? UNMATCHED;
    map.set(key, [...(map.get(key) ?? []), p]);
  }
  return [...map.entries()]
    .map(([key, list]) => ({
      key,
      label: key === UNMATCHED ? "Not part of a show" : key,
      ids: list.map((p) => p.id),
    }))
    .sort((a, b) => b.ids.length - a.ids.length || a.label.localeCompare(b.label));
}

export default function DraftQueue() {
  usePageTitle("Drafts");
  const qc = useQueryClient();
  const saves = useDraftAutosaves(qc);
  const draftsQuery = useQuery({ queryKey: ["drafts"], queryFn: listDrafts });
  const drafts = draftsQuery.data ?? [];

  // Pull all upcoming scheduled posts (no range filter = everything pending) so the dashboard
  // counter reflects what's actually on the calendar. We refresh on a 60s interval so the
  // count stays current as posts fire.
  const { data: scheduled = [] } = useQuery({
    queryKey: ["schedule", "all"],
    // Explicit wide range: the backend's no-arg default window is -7/+60 days, which
    // silently undercounted "Scheduled total" once the scatter went year-wide.
    queryFn: () =>
      listScheduled(
        new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
        new Date(Date.now() + 400 * 86400 * 1000).toISOString(),
      ),
    refetchInterval: 60_000,
  });
  const { data: published = [] } = useQuery({
    queryKey: ["published", "all"],
    queryFn: () => listHistory(undefined, ["posted", "late"]),
    refetchInterval: 60_000,
  });

  const [actionError, setActionError] = useState<string | null>(null);
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
  const [filmstripOpen, setFilmstripOpen] = useState(false);
  const [carouselOpen, setCarouselOpen] = useState(false);
  const [reelOpen, setReelOpen] = useState(false);
  const [findReplaceOpen, setFindReplaceOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<"newest" | "oldest" | "captured" | "largest" | "ready">("newest");
  const [filterReady, setFilterReady] = useState(false);
  // "" = every show. Holds a batch key from computeBatches.
  const [showFilter, setShowFilter] = useState("");

  const batches = useMemo(() => computeBatches(drafts), [drafts]);

  const visibleDrafts = useMemo(() => {
    let list = drafts;
    if (showFilter) {
      list = list.filter((p) => (batchKey(p) ?? UNMATCHED) === showFilter);
    }
    if (filterReady) {
      list = list.filter(isReady);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((p) =>
        (p.title || "").toLowerCase().includes(q) ||
        (p.original_filename || "").toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q) ||
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
          const aReady = isReady(a) ? 1 : 0;
          const bReady = isReady(b) ? 1 : 0;
          if (aReady !== bReady) return bReady - aReady;
          return b.created_at.localeCompare(a.created_at);
        }
        default:
          return b.created_at.localeCompare(a.created_at);
      }
    });
    return arr;
  }, [drafts, search, sortKey, filterReady, showFilter]);

  // A show can vanish under the filter — delete its last draft, or schedule the batch —
  // which would otherwise leave the grid stuck on an empty selection with no obvious cause.
  useEffect(() => {
    if (showFilter && !batches.some((b) => b.key === showFilter)) setShowFilter("");
  }, [batches, showFilter]);

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

  const selectedSave = selected ? saves.get(selected.id) : undefined;
  useEffect(() => {
    if (!selectedSave || !selectedId) return;
    selectedSave.attached = true;
    return () => {
      selectedSave.attached = false;
      saves.release(selectedId, selectedSave);
    };
  }, [saves, selectedId, selectedSave]);

  async function prepareSchedule(id: string) {
    if (!await saves.get(id).flush()) throw new Error("Couldn't save. Retry before scheduling.");
    const post = qc.getQueryData<Post[]>(["drafts"])?.find((p) => p.id === id);
    if (post?.preflight && !post.preflight.deliverable) {
      throw new Error(post.preflight.blockers.map((b) => b.message).join(" · "));
    }
    return post;
  }

  function saveNext() {
    if (!selected || !selectedSave) return;
    // Capture the filtered order before the save changes title/readiness and sorts it.
    const index = visibleDrafts.findIndex((p) => p.id === selected.id);
    const next = index >= 0 ? visibleDrafts[index + 1]?.id : undefined;
    void selectedSave.flush().then((ok) => {
      if (ok && next) setSelectedId((current) => current === selected.id ? next : current);
    });
  }

  async function openDraftAction(open: () => void) {
    // Batch editors and Smart Fill must see the latest saved draft, and must not
    // compete with an older editor's pending PATCH for the same fields.
    const sessions = [...saves.sessions.values()];
    const results = await Promise.all(sessions.map((session) => session.flush()));
    if (results.some((ok) => !ok)) {
      setActionError("Couldn't save a draft. Retry its save before continuing.");
      return;
    }
    setSelectedId(null);
    open();
  }

  const scheduleMutation = useMutation({
    mutationFn: ({ id, iso }: { id: string; iso: string }) => saves.schedule(id, iso),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      setScheduling(null);
      setSelectedId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      if (selectedId && selectedId !== id) void saves.get(selectedId).flush();
      const session = saves.sessions.get(id);
      await session?.cancel();
      try {
        const result = await deletePost(id);
        saves.sessions.delete(id);
        return result;
      } catch (error) {
        session?.resume();
        throw error;
      }
    },
    onError: (error) => {
      setActionError(error instanceof Error ? error.message : "Could not delete the draft.");
    },
    onSuccess: (_data, id) => {
      saves.remove([id]);
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
    setActionError(null);
    let failed = false;
    for (const id of ids) {
      try {
        await deleteMutation.mutateAsync(id);
      } catch {
        failed = true; // onError displays the server's reason; keep failed drafts selected.
      }
    }
    if (!failed) exitMultiSelect();
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
    { label: "Ready to schedule", value: drafts.filter(isReady).length },
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

        {actionError && (
          <div role="alert" style={{ color: "var(--danger)", fontSize: 13, marginBottom: 16 }}>
            {actionError}
            <button className="fp-btn" onClick={() => setActionError(null)} style={{ marginLeft: 12 }}>Dismiss</button>
          </div>
        )}

        {[...saves.sessions].filter(([id, session]) => id !== selectedId && session.error).map(([id, session]) => (
          <div key={id} role="alert" style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>
            Couldn't save {drafts.find((p) => p.id === id)?.title || drafts.find((p) => p.id === id)?.original_filename || id}: {session.error}
            <button className="fp-btn-ghost" onClick={() => void session.flush()} style={{ marginLeft: 8 }}>Retry</button>
          </div>
        ))}

        <QueueTabs draftCount={drafts.length} scheduledCount={scheduledPending.length} />

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
                    {/* Search here only filters the loaded drafts; find & replace runs
                        server-side and also reaches scheduled posts. */}
                    <button
                      className="fp-btn-ghost"
                      onClick={() => void openDraftAction(() => setFindReplaceOpen(true))}
                      title="Fix a typo across many drafts and scheduled posts at once"
                      style={{ padding: "6px 12px", fontSize: 13 }}
                    >
                      Find &amp; replace
                    </button>
                    <input
                      className="fp-input"
                      placeholder="Search title / description / filename / tag / camera"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      style={{ flex: 1, minWidth: 260, padding: "6px 12px", fontSize: 13 }}
                    />
                    <button
                      onClick={() => setFilterReady((v) => !v)}
                      title={`Show only drafts with nothing outstanding — image present, destinations connected, and title, tags and alt text written. Currently ${drafts.filter(isReady).length} of ${drafts.length}.`}
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
                        onClick={() => void openDraftAction(() => setBulkEditOpen(true))}
                        style={{ padding: "6px 14px", fontSize: 13 }}
                      >
                        Bulk Edit ({checkedIds.size})
                      </button>
                      <button
                        className="fp-btn-ghost"
                        disabled={checkedIds.size === 0}
                        onClick={() => void openDraftAction(() => setFilmstripOpen(true))}
                        title="Crop the selection to one Instagram ratio, one frame at a time"
                        style={{ padding: "6px 14px", fontSize: 13 }}
                      >
                        Crop for IG ({checkedIds.size})
                      </button>
                      <button
                        className="fp-btn-ghost"
                        disabled={checkedIds.size < 2}
                        onClick={() => void openDraftAction(() => setReelOpen(true))}
                        title="Build a reel from the selection and schedule it — reels reach people who don't follow you, which a carousel cannot"
                        style={{ padding: "6px 14px", fontSize: 13 }}
                      >
                        Reel ({checkedIds.size})
                      </button>
                      <button
                        className="fp-btn-ghost"
                        disabled={checkedIds.size < 2}
                        onClick={() => void openDraftAction(() => setCarouselOpen(true))}
                        title="Publish the selection as one swipeable Instagram post. Best when the order tells a sequence; otherwise a reel reaches further."
                        style={{ padding: "6px 14px", fontSize: 13, opacity: 0.75 }}
                      >
                        Carousel ({checkedIds.size})
                      </button>
                      <button
                        className="fp-btn"
                        disabled={checkedIds.size === 0}
                        onClick={() => void openDraftAction(() => setSmartFillOpen(true))}
                        style={{ padding: "6px 14px", fontSize: 13 }}
                      >
                        Smart Fill ({checkedIds.size})
                      </button>
                    </div>
                  </>
                )}
              </div>
              {batches.length > 0 && (() => {
                const active = batches.find((b) => b.key === showFilter) ?? null;
                const allSelected =
                  !!active && multiSelect && active.ids.every((id) => checkedIds.has(id));
                return (
                  <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <label
                      htmlFor="show-filter"
                      style={{
                        fontSize: 11,
                        color: "var(--text-fade)",
                        textTransform: "uppercase",
                        letterSpacing: "0.04em",
                      }}
                    >
                      Shows
                    </label>
                    <select
                      id="show-filter"
                      className="fp-input"
                      value={showFilter}
                      onChange={(e) => setShowFilter(e.target.value)}
                      title="Show only the drafts from one shoot"
                      style={{ maxWidth: 420, fontSize: 13, padding: "6px 10px" }}
                    >
                      <option value="">All shows ({drafts.length})</option>
                      {batches.map((b) => (
                        <option key={b.key} value={b.key}>
                          {b.label} ({b.ids.length})
                        </option>
                      ))}
                    </select>

                    {active && (
                      <>
                        {/* The chip row this replaced selected a whole show in one click, and
                            that fed Bulk Edit -> Smart Fill, which is the core workflow. Keep
                            it reachable rather than making the operator filter and then hunt
                            for "Select all visible" in the toolbar. */}
                        <button
                          type="button"
                          className="fp-btn fp-btn-ghost"
                          onClick={() => {
                            setMultiSelect(true);
                            setCheckedIds((prev) => {
                              const next = new Set(prev);
                              if (allSelected) active.ids.forEach((id) => next.delete(id));
                              else active.ids.forEach((id) => next.add(id));
                              return next;
                            });
                          }}
                          title={
                            allSelected
                              ? "Deselect this show's drafts"
                              : `Select all ${active.ids.length} drafts from this show — then Bulk Edit for shared venue/show/performers, Smart Fill to scatter`
                          }
                        >
                          {allSelected ? "Deselect all" : `Select all ${active.ids.length}`}
                        </button>
                        <button
                          type="button"
                          className="fp-btn fp-btn-ghost"
                          onClick={() => setShowFilter("")}
                          title="Show every draft again"
                        >
                          Clear filter
                        </button>
                      </>
                    )}

                    {/* No count here on purpose — the sort row already shows "N of M". */}
                  </div>
                );
              })()}
              <div className="fp-grid-cards">
                {visibleDrafts.map((p) => (
                  <DraftCard
                    key={p.id}
                    post={p}
                    title={isReady(p) ? undefined : `Not ready — missing: ${readyMissing(p).join(", ")}`}
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
                <button className="fp-btn-ghost" onClick={() => setSelectedId(null)} style={{ marginBottom: 8 }}>Close editor</button>
                <MetadataEditor
                  key={selected.id}
                  post={selected}
                  saving={false}
                  autosave={selectedSave}
                  onSave={async () => { await selectedSave?.flush(); }}
                  onSaveNext={saveNext}
                  onSchedule={() => {
                    setActionError(null);
                    void prepareSchedule(selected.id).then((post) => {
                      if (post) setScheduling(post);
                    }).catch((error) => setActionError(error.message));
                  }}
                  onDelete={() => {
                    setActionError(null);
                    deleteMutation.mutate(selected.id);
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
            await prepareSchedule(scheduling.id);
            await scheduleMutation.mutateAsync({ id: scheduling.id, iso });
          }}
        />
      )}

      {reelOpen && (
        <ReelFromDraftsDialog
          posts={drafts.filter((d) => checkedIds.has(d.id))}
          onCancel={() => setReelOpen(false)}
          onDone={() => {
            setReelOpen(false);
            exitMultiSelect();
            void qc.invalidateQueries({ queryKey: ["reels"] });
          }}
        />
      )}

      {carouselOpen && (
        <CarouselDialog
          posts={drafts.filter((d) => checkedIds.has(d.id))}
          onCancel={() => setCarouselOpen(false)}
          onGrouped={() => {
            setCarouselOpen(false);
            exitMultiSelect();
            void qc.invalidateQueries({ queryKey: ["drafts"] });
            void qc.invalidateQueries({ queryKey: ["schedule"] });
          }}
        />
      )}

      {filmstripOpen && (
        <IgCropFilmstrip
          posts={drafts.filter((d) => checkedIds.has(d.id))}
          onClose={() => setFilmstripOpen(false)}
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

      {findReplaceOpen && (
        <FindReplaceDialog onClose={() => setFindReplaceOpen(false)} />
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
