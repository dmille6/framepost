import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPost,
  postNow,
  ungroupCarousel,
  type Post,
  type ScheduledItem,
  setPostAlbums,
  setPostGroups,
  setPostPerformers,
  setPostProfiles,
  updatePost,
} from "../api/client";
import { ownsArrowKeys, useBackdropDismiss, useModalLayer } from "../hooks/useModalDismiss";
import { absoluteTime } from "../lib/time";
import InstagramPanel from "./InstagramPanel";
import MetadataEditor, { editorChangesToPatch, type EditorChanges } from "./MetadataEditor";
import RedditPanel from "./RedditPanel";
import ReelTab from "./ReelTab";
import { SkeletonRows } from "./Skeleton";

type Props = {
  item: ScheduledItem;
  onClose: () => void;
  onReschedule: () => void;
  onUnschedule: () => Promise<void>;
  busy: boolean;
  /** Step to the neighbouring entry. Leave undefined at either end of the queue. */
  onPrev?: () => void;
  onNext?: () => void;
  /** Where this entry sits in the order the arrows walk, for the "3 of 24" label. */
  position?: { index: number; total: number };
};

export default function ScheduledItemModal({
  item,
  onClose,
  onReschedule,
  onUnschedule,
  busy,
  onPrev,
  onNext,
  position,
}: Props) {
  const qc = useQueryClient();
  const { data: post, isLoading, error } = useQuery({
    queryKey: ["post", item.id],
    queryFn: () => getPost(item.id),
  });

  const saveMutation = useMutation({
    mutationFn: async (changes: EditorChanges) => {
      const body = editorChangesToPatch(changes);
      const saved = await updatePost(item.id, body);
      await setPostAlbums(item.id, changes.album_ids);
      await setPostGroups(item.id, changes.group_ids, changes.use_routing);
      await setPostProfiles(item.id, changes.profile_ids);
      await setPostPerformers(item.id, changes.performer_ids);
      return saved;
    },
    onSuccess: (saved: Post) => {
      qc.setQueryData(["post", item.id], saved);
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      void qc.invalidateQueries({ queryKey: ["post-albums", item.id] });
      void qc.invalidateQueries({ queryKey: ["post-groups", item.id] });
      void qc.invalidateQueries({ queryKey: ["post-profiles", item.id] });
      void qc.invalidateQueries({ queryKey: ["post-performers", item.id] });
      void qc.invalidateQueries({ queryKey: ["instagram-format", item.id] });
      void qc.invalidateQueries({ queryKey: ["merged-tags", item.id] });
      // New structured-context fields can affect the venues count / recent-shows/cities lists.
      void qc.invalidateQueries({ queryKey: ["venues"] });
      void qc.invalidateQueries({ queryKey: ["recent-shows"] });
      void qc.invalidateQueries({ queryKey: ["recent-cities"] });
    },
  });

  // This view saves only when asked (a half-typed caption must not reach a post that
  // publishes in a minute), so it has to know when there is something to lose. Before
  // it did, a stray click, Escape, or the Reschedule button discarded the whole form.
  const [dirty, setDirty] = useState(false);
  const saveRef = useRef<(() => Promise<boolean>) | null>(null);
  // Set when a close was asked for with edits unsaved: the view stays and asks.
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    setDirty(false);
    setLeaving(false);
    saveMutation.reset();
  }, [item.id]);

  function requestClose() {
    if (dirty) setLeaving(true);
    else onClose();
  }

  async function saveEdits(): Promise<boolean> {
    return (await saveRef.current?.()) ?? false;
  }

  /** Previous/next. Moving on keeps the edits, the way stepping to the next photo does
   *  everywhere else; if the save fails the view stays put with the error showing. */
  async function step(go: (() => void) | undefined) {
    if (!go || saveMutation.isPending) return;
    if (dirty && !(await saveEdits())) return;
    go();
  }

  const layer = useModalLayer(() => {
    if (leaving) setLeaving(false);
    else requestClose();
  });
  const backdrop = useBackdropDismiss(requestClose);

  // Left/right arrows walk the queue, unless the key belongs to a text field or slider,
  // or another dialog (template, lightbox, crop) is open on top of this one.
  const keys = useRef({ step, onPrev, onNext });
  keys.current = { step, onPrev, onNext };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      if (!layer.isTop() || ownsArrowKeys(e.target)) return;
      const { step: go, onPrev: prev, onNext: next } = keys.current;
      const target = e.key === "ArrowLeft" ? prev : next;
      if (!target) return;
      e.preventDefault();
      void go(target);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const editable = item.status === "pending";
  const showCopyTabs = item.status === "posted" || item.status === "late";
  const [tab, setTab] = useState<"edit" | "instagram" | "reddit" | "reel">("edit");

  const postNowMutation = useMutation({
    mutationFn: () => postNow(item.id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      void qc.invalidateQueries({ queryKey: ["published"] });
      onClose();
    },
  });

  return (
    <div
      className="fp-backdrop"
      style={{ display: "grid", placeItems: "center", padding: 24 }}
      {...backdrop}
    >
      {(onPrev || onNext) && (
        <>
          <NavArrow side="left" disabled={!onPrev || saveMutation.isPending} onClick={() => void step(onPrev)} />
          <NavArrow side="right" disabled={!onNext || saveMutation.isPending} onClick={() => void step(onNext)} />
        </>
      )}
      <div
        className="fp-card fp-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(960px, 100%)",
          maxHeight: "90vh",
          overflow: "auto",
          padding: 0,
          boxShadow: "var(--shadow-lg)",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <ModalHeader
          item={item}
          editable={editable}
          busy={busy || postNowMutation.isPending}
          position={position}
          dirty={dirty}
          saving={saveMutation.isPending}
          onSave={() => void saveEdits()}
          onClose={requestClose}
          onReschedule={onReschedule}
          onUnschedule={onUnschedule}
          onPostNow={() => postNowMutation.mutate()}
          postNowPending={postNowMutation.isPending}
          onUngroup={async () => {
            if (!item.carousel_id) return;
            await ungroupCarousel(item.carousel_id);
            void qc.invalidateQueries({ queryKey: ["schedule"] });
            void qc.invalidateQueries({ queryKey: ["drafts"] });
            onClose();
          }}
        />

        {leaving && (
          <div
            role="alertdialog"
            aria-label="Unsaved changes"
            style={{
              display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
              padding: "10px 20px",
              background: "var(--amber-tint)",
              borderBottom: "0.5px solid rgba(240, 201, 122, 0.2)",
              fontSize: 13, color: "var(--amber)",
            }}
          >
            <span style={{ marginRight: "auto" }}>You have unsaved changes to this post.</span>
            <button
              className="fp-btn"
              disabled={saveMutation.isPending}
              onClick={() => void saveEdits().then((ok) => { if (ok) onClose(); })}
              style={{ padding: "6px 12px", fontSize: 13 }}
            >
              {saveMutation.isPending ? "Saving…" : "Save and close"}
            </button>
            <button className="fp-btn-ghost" onClick={onClose} style={{ padding: "6px 12px", fontSize: 13 }}>
              Discard changes
            </button>
            <button className="fp-btn-ghost" onClick={() => setLeaving(false)} style={{ padding: "6px 12px", fontSize: 13 }}>
              Keep editing
            </button>
          </div>
        )}

        {saveMutation.error && (
          <div role="alert" style={{ padding: "8px 20px", fontSize: 13, color: "var(--danger)" }}>
            Couldn't save: {(saveMutation.error as Error).message}
          </div>
        )}

        <div style={{ padding: 20, overflow: "auto" }}>
          {showCopyTabs && (
            <div
              role="tablist"
              style={{
                display: "flex",
                gap: 0,
                borderBottom: "0.5px solid var(--border)",
                marginBottom: 20,
              }}
            >
              <ModalTab active={tab === "edit"} onClick={() => setTab("edit")}>
                Edit
              </ModalTab>
              <ModalTab active={tab === "instagram"} onClick={() => setTab("instagram")}>
                Instagram
              </ModalTab>
              <ModalTab active={tab === "reddit"} onClick={() => setTab("reddit")}>
                Reddit
              </ModalTab>
              <ModalTab active={tab === "reel"} onClick={() => setTab("reel")}>
                Reel
              </ModalTab>
            </div>
          )}

          {/* Hidden rather than unmounted on the other tabs: unmounting dropped any
              unsaved edits the moment a tab was clicked. */}
          <div style={{ display: !showCopyTabs || tab === "edit" ? "block" : "none" }}>
              {!editable && (
                <div
                  style={{
                    marginBottom: 16,
                    padding: "10px 12px",
                    background: "var(--amber-tint)",
                    border: "0.5px solid rgba(240, 201, 122, 0.2)",
                    borderRadius: 8,
                    fontSize: 12,
                    color: "var(--amber)",
                  }}
                >
                  This post is <strong>{item.status}</strong>. The photo is already on Flickr.
                  Editing here updates only the local record — it won't push changes back to Flickr.
                </div>
              )}

              {error && (
                <div style={{ color: "var(--danger)", fontSize: 13 }}>
                  {(error as Error).message}
                </div>
              )}

              {isLoading || !post || post.id !== item.id ? (
                <SkeletonRows count={6} height={32} />
              ) : (
                <MetadataEditor
                  // Keyed: stepping to another post must start a fresh form, not carry
                  // this one's typed values and "touched" flags across.
                  key={post.id}
                  onDirtyChange={setDirty}
                  saveRef={saveRef}
                  post={post}
                  saving={saveMutation.isPending}
                  onSave={async (changes) => {
                    await saveMutation.mutateAsync(changes);
                  }}
                  onSchedule={onReschedule}
                  scheduleLabel="Reschedule"
                />
              )}
          </div>

          {showCopyTabs && tab === "instagram" && <InstagramPanel key={item.id} postId={item.id} />}
          {showCopyTabs && tab === "reddit" && <RedditPanel key={item.id} postId={item.id} />}
          {showCopyTabs && tab === "reel" && <ReelTab key={item.id} postId={item.id} post={item} />}
        </div>
      </div>
    </div>
  );
}

function ModalHeader({
  item,
  editable,
  busy,
  position,
  dirty,
  saving,
  onSave,
  onClose,
  onReschedule,
  onUnschedule,
  onPostNow,
  postNowPending,
  onUngroup,
}: {
  item: ScheduledItem;
  editable: boolean;
  busy: boolean;
  position?: { index: number; total: number };
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onClose: () => void;
  onReschedule: () => void;
  onUnschedule: () => Promise<void>;
  onPostNow: () => void;
  postNowPending: boolean;
  onUngroup: () => Promise<void>;
}) {
  // Every action here either publishes or closes the view. With edits unsaved, the
  // first would ship the old text and the second would drop the new, so they wait.
  const held = busy || dirty;
  const heldTitle = dirty ? "Save your changes first" : undefined;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "14px 20px",
        borderBottom: "0.5px solid var(--border)",
        position: "sticky",
        top: 0,
        background: "var(--card)",
        zIndex: 1,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 15,
            fontWeight: 600,
            letterSpacing: "-0.01em",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {item.title || item.original_filename || "(untitled)"}
        </div>
        <div
          style={{
            fontSize: 12,
            color: "var(--text-dim)",
            marginTop: 2,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span className={`fp-pill fp-pill-${item.status}`}>{item.status}</span>
          {item.carousel_id && (
            <span
              title="These publish as one Instagram post"
              style={{
                fontSize: 11, padding: "1px 6px", borderRadius: 999,
                color: "var(--teal)", border: "0.5px solid var(--teal)",
              }}
            >
              ⧉ carousel
            </span>
          )}
          {item.scheduled_at && (
            <span title={absoluteTime(item.scheduled_at)}>
              {item.status === "posted" || item.status === "late"
                ? `Posted ${absoluteTime(item.posted_at ?? item.scheduled_at)}`
                : `Scheduled ${absoluteTime(item.scheduled_at)}`}
            </span>
          )}
          {position && position.index >= 0 && (
            <span className="fp-num" style={{ color: "var(--text-fade)" }}>
              {position.index + 1} of {position.total}
            </span>
          )}
        </div>
      </div>

      {dirty && (
        <>
          <span style={{ fontSize: 12, color: "var(--amber)", whiteSpace: "nowrap" }}>Unsaved changes</span>
          <button
            className="fp-btn"
            onClick={onSave}
            disabled={saving}
            style={{ padding: "7px 14px", fontSize: 13 }}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </>
      )}

      {editable && item.carousel_id && (
        <button
          className="fp-btn-ghost"
          onClick={() => void onUngroup()}
          disabled={held}
          title={heldTitle ?? "Split back into separate posts. Their scheduled times stay as they are."}
          style={{ padding: "7px 12px", fontSize: 13 }}
        >
          Ungroup
        </button>
      )}

      {editable && (
        <>
          <button
            className="fp-btn-ghost"
            onClick={() => void onUnschedule()}
            disabled={held}
            title={heldTitle}
            style={{ padding: "7px 12px", fontSize: 13 }}
          >
            Cancel schedule
          </button>
          <button
            onClick={onPostNow}
            disabled={held}
            style={{
              padding: "7px 14px",
              fontSize: 13,
              background: "transparent",
              color: "var(--teal)",
              border: "0.5px solid rgba(93,202,165,0.4)",
              borderRadius: 8,
              cursor: held ? "not-allowed" : "pointer",
              opacity: held ? 0.5 : 1,
              fontWeight: 500,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
            title={heldTitle ?? "Fire this post immediately (worker picks it up within ~1 minute)"}
          >
            {postNowPending && <span className="fp-spinner" />}
            {postNowPending ? "Queuing" : "Post now"}
          </button>
          <button
            className="fp-btn"
            onClick={onReschedule}
            disabled={held}
            title={heldTitle}
            style={{ padding: "7px 14px", fontSize: 13 }}
          >
            Reschedule
          </button>
        </>
      )}
      <button
        onClick={onClose}
        aria-label="Close"
        style={{
          background: "transparent",
          border: "0.5px solid var(--border-strong)",
          borderRadius: 8,
          width: 32,
          height: 32,
          color: "var(--text-dim)",
          cursor: "pointer",
          display: "grid",
          placeItems: "center",
          fontSize: 16,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}

/** Round previous/next button, parked just outside the card (or over its edge when the
 *  window is too narrow to leave room). */
function NavArrow({
  side,
  disabled,
  onClick,
}: {
  side: "left" | "right";
  disabled: boolean;
  onClick: () => void;
}) {
  const label = side === "left" ? "Previous scheduled post" : "Next scheduled post";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={`${label} (${side === "left" ? "←" : "→"})`}
      style={{
        position: "fixed",
        top: "50%",
        transform: "translateY(-50%)",
        // The card is at most 960px wide and centred; sit 56px outside it, but never
        // closer than 8px to the window edge.
        [side]: "max(8px, calc(50% - 480px - 56px))",
        width: 44,
        height: 44,
        borderRadius: 999,
        border: "0.5px solid var(--border-strong)",
        background: "var(--card)",
        color: "var(--text)",
        fontSize: 22,
        lineHeight: 1,
        display: "grid",
        placeItems: "center",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.3 : 1,
        boxShadow: "var(--shadow-lg)",
        zIndex: 2,
      }}
    >
      {side === "left" ? "‹" : "›"}
    </button>
  );
}

function ModalTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        background: "transparent",
        border: 0,
        borderBottom: active ? "2px solid var(--teal)" : "2px solid transparent",
        marginBottom: -1,
        padding: "8px 14px",
        fontSize: 13,
        fontWeight: active ? 500 : 400,
        color: active ? "var(--text)" : "var(--text-dim)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}
