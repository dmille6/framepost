import { useQuery } from "@tanstack/react-query";

import { fetchPostEvents } from "../api/client";
import { absoluteTime, relativeTime } from "../lib/time";
import { SkeletonRows } from "./Skeleton";

const LABELS: Record<string, string> = {
  imported: "Imported",
  edited: "Edited",
  scheduled: "Scheduled",
  rescheduled: "Rescheduled",
  flickr_uploading: "Uploading to Flickr",
  flickr_uploaded: "Posted to Flickr",
  flickr_failed: "Flickr failure",
  bluesky_uploaded: "Posted to Bluesky",
  bluesky_failed: "Bluesky failure",
  pixelfed_uploaded: "Posted to Pixelfed",
  pixelfed_failed: "Pixelfed failure",
  group_submitted: "Submitted to group",
  group_accepted: "Group accepted",
  group_rejected: "Group rejected",
  marked_late: "Marked late",
  marked_missed: "Marked missed",
  manual_repost: "Manual repost",
  manual_dismiss: "Dismissed",
  original_purged: "Original purged",
  deleted: "Deleted",
};

const TONE: Record<string, string> = {
  flickr_uploaded: "var(--teal)",
  bluesky_uploaded: "var(--teal)",
  pixelfed_uploaded: "var(--teal)",
  group_accepted: "var(--teal)",
  flickr_failed: "var(--danger)",
  bluesky_failed: "var(--danger)",
  pixelfed_failed: "var(--danger)",
  marked_missed: "var(--danger)",
  group_rejected: "var(--danger)",
  marked_late: "var(--amber)",
};

export default function ActivityTimeline({ postId }: { postId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["events", postId],
    queryFn: () => fetchPostEvents(postId),
  });

  if (isLoading) return <SkeletonRows count={4} height={36} />;
  if (error) return <div style={{ color: "var(--danger)" }}>{(error as Error).message}</div>;
  if (!data || data.length === 0)
    return <div style={{ color: "var(--text-dim)", fontSize: 13 }}>No events.</div>;

  return (
    <div style={{ position: "relative", paddingLeft: 18 }}>
      <div
        style={{
          position: "absolute",
          left: 5,
          top: 6,
          bottom: 6,
          width: 1,
          background: "var(--border)",
        }}
      />
      <div style={{ display: "grid", gap: 12 }}>
        {data.map((ev) => {
          const tone = TONE[ev.event_type] ?? "var(--text-fade)";
          return (
            <div
              key={ev.id}
              style={{
                position: "relative",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              <span
                style={{
                  position: "absolute",
                  left: -16,
                  top: 5,
                  width: 9,
                  height: 9,
                  borderRadius: 999,
                  background: tone,
                  border: "2px solid var(--card)",
                  boxShadow: "0 0 0 0.5px var(--border-strong)",
                }}
              />
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 500, color: "var(--text)" }}>
                  {LABELS[ev.event_type] ?? ev.event_type}
                </span>
                <span style={{ color: "var(--text-fade)" }}>by {ev.actor}</span>
                <span
                  title={absoluteTime(ev.created_at)}
                  style={{ color: "var(--text-fade)", marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}
                >
                  {relativeTime(ev.created_at)}
                </span>
              </div>
              {ev.details && Object.keys(ev.details).length > 0 && (
                <pre
                  style={{
                    margin: "6px 0 0",
                    fontSize: 11,
                    color: "var(--text-dim)",
                    background: "var(--bg)",
                    padding: 8,
                    borderRadius: 6,
                    overflow: "auto",
                    maxWidth: "100%",
                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                  }}
                >
                  {JSON.stringify(ev.details, null, 2)}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
