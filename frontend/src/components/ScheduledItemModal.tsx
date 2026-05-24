import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPost,
  postNow,
  type Post,
  type PostUpdate,
  type ScheduledItem,
  setPostAlbums,
  setPostGroups,
  setPostPerformers,
  setPostProfiles,
  updatePost,
} from "../api/client";
import { absoluteTime } from "../lib/time";
import InstagramPanel from "./InstagramPanel";
import MetadataEditor, { type EditorChanges } from "./MetadataEditor";
import RedditPanel from "./RedditPanel";
import ReelTab from "./ReelTab";
import { SkeletonRows } from "./Skeleton";

type Props = {
  item: ScheduledItem;
  onClose: () => void;
  onReschedule: () => void;
  onUnschedule: () => Promise<void>;
  busy: boolean;
};

export default function ScheduledItemModal({
  item,
  onClose,
  onReschedule,
  onUnschedule,
  busy,
}: Props) {
  const qc = useQueryClient();
  const { data: post, isLoading, error } = useQuery({
    queryKey: ["post", item.id],
    queryFn: () => getPost(item.id),
  });

  // Esc closes the modal
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const saveMutation = useMutation({
    mutationFn: async (changes: EditorChanges) => {
      const body: PostUpdate = {
        title: changes.title,
        description: changes.description,
        tags: changes.tags,
        privacy: changes.privacy,
        safety_level: changes.safety_level,
        content_type: changes.content_type,
      };
      const saved = await updatePost(item.id, body);
      await setPostAlbums(item.id, changes.album_ids);
      await setPostGroups(item.id, changes.group_ids);
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
    },
  });

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
      onClick={onClose}
    >
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
          onClose={onClose}
          onReschedule={onReschedule}
          onUnschedule={onUnschedule}
          onPostNow={() => postNowMutation.mutate()}
          postNowPending={postNowMutation.isPending}
        />

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

          {(!showCopyTabs || tab === "edit") && (
            <>
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

              {isLoading || !post ? (
                <SkeletonRows count={6} height={32} />
              ) : (
                <MetadataEditor
                  post={post}
                  saving={saveMutation.isPending}
                  onSave={async (changes) => {
                    await saveMutation.mutateAsync(changes);
                  }}
                  onSchedule={onReschedule}
                  scheduleLabel="Reschedule"
                />
              )}
            </>
          )}

          {showCopyTabs && tab === "instagram" && <InstagramPanel postId={item.id} />}
          {showCopyTabs && tab === "reddit" && <RedditPanel postId={item.id} />}
          {showCopyTabs && tab === "reel" && <ReelTab postId={item.id} post={item} />}
        </div>
      </div>
    </div>
  );
}

function ModalHeader({
  item,
  editable,
  busy,
  onClose,
  onReschedule,
  onUnschedule,
  onPostNow,
  postNowPending,
}: {
  item: ScheduledItem;
  editable: boolean;
  busy: boolean;
  onClose: () => void;
  onReschedule: () => void;
  onUnschedule: () => Promise<void>;
  onPostNow: () => void;
  postNowPending: boolean;
}) {
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
          {item.scheduled_at && (
            <span title={absoluteTime(item.scheduled_at)}>
              {item.status === "posted" || item.status === "late"
                ? `Posted ${absoluteTime(item.posted_at ?? item.scheduled_at)}`
                : `Scheduled ${absoluteTime(item.scheduled_at)}`}
            </span>
          )}
        </div>
      </div>

      {editable && (
        <>
          <button
            className="fp-btn-ghost"
            onClick={() => void onUnschedule()}
            disabled={busy}
            style={{ padding: "7px 12px", fontSize: 13 }}
          >
            Cancel schedule
          </button>
          <button
            onClick={onPostNow}
            disabled={busy}
            style={{
              padding: "7px 14px",
              fontSize: 13,
              background: "transparent",
              color: "var(--teal)",
              border: "0.5px solid rgba(93,202,165,0.4)",
              borderRadius: 8,
              cursor: busy ? "not-allowed" : "pointer",
              fontWeight: 500,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
            title="Fire this post immediately (worker picks it up within ~1 minute)"
          >
            {postNowPending && <span className="fp-spinner" />}
            {postNowPending ? "Queuing" : "Post now"}
          </button>
          <button
            className="fp-btn"
            onClick={onReschedule}
            disabled={busy}
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
