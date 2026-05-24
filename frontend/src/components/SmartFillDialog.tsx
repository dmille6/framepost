import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import {
  ApiError,
  smartFill,
  type SmartFillRequest,
  type SmartFillResponse,
  thumbnailUrl,
} from "../api/client";

type Props = {
  postIds: string[];
  onCancel: () => void;
  onConfirmed: () => void;
};

type Mode = "sequential" | "random_scatter";

export default function SmartFillDialog({ postIds, onCancel, onConfirmed }: Props) {
  const [mode, setMode] = useState<Mode>("sequential");
  const [time, setTime] = useState("10:00");
  const [cadence, setCadence] = useState(1);
  const [startDate, setStartDate] = useState(() => formatLocalDate(tomorrow()));
  const [skipWeekends, setSkipWeekends] = useState(false);
  const [preview, setPreview] = useState<SmartFillResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const buildRequest = (confirm: boolean): SmartFillRequest => ({
    post_ids: postIds,
    time_of_day: time,
    cadence_days: cadence,
    start_date: startDate,
    skip_weekends: skipWeekends,
    confirm,
    mode,
  });

  const previewMutation = useMutation({
    mutationFn: () => smartFill(buildRequest(false)),
    onSuccess: (resp) => {
      setPreview(resp);
      setError(null);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "preview failed"),
  });

  const confirmMutation = useMutation({
    mutationFn: () => smartFill(buildRequest(true)),
    onSuccess: () => onConfirmed(),
    onError: (e) => setError(e instanceof ApiError ? e.message : "schedule failed"),
  });

  // Auto-preview on first open and on form changes (light debounce).
  useEffect(() => {
    const t = setTimeout(() => previewMutation.mutate(), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, time, cadence, startDate, skipWeekends, postIds.length]);

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "grid", placeItems: "center", zIndex: 100, padding: 24,
      }}
      onClick={onCancel}
    >
      <div
        className="fp-card"
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(720px, 100%)", maxHeight: "92vh", overflow: "auto", display: "grid", gap: 16 }}
      >
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Smart Fill</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            {mode === "sequential"
              ? `Distribute ${postIds.length} draft${postIds.length === 1 ? "" : "s"} across the calendar at a chosen cadence. The one-post-per-hour rule applies — clashes auto-bump to the next day.`
              : `Scatter ${postIds.length} draft${postIds.length === 1 ? "" : "s"} randomly across the next 6 months at popular post times (9-11 AM / 6-8 PM local). Already-scheduled days are skipped.`}
          </div>
        </div>

        {/* Mode toggle */}
        <div
          role="tablist"
          style={{
            display: "inline-flex",
            border: "0.5px solid var(--border-strong)",
            borderRadius: 8,
            overflow: "hidden",
            alignSelf: "flex-start",
          }}
        >
          <ModeButton active={mode === "sequential"} onClick={() => setMode("sequential")}>
            Sequential
          </ModeButton>
          <ModeButton active={mode === "random_scatter"} onClick={() => setMode("random_scatter")}>
            Random scatter
          </ModeButton>
        </div>

        {mode === "sequential" ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            <Field label="Start date">
              <input className="fp-input" type="date" value={startDate}
                     onChange={(e) => setStartDate(e.target.value)} />
            </Field>
            <Field label="Time of day">
              <input className="fp-input" type="time" value={time}
                     onChange={(e) => setTime(e.target.value)} />
            </Field>
            <Field label="Days between posts">
              <input className="fp-input" type="number" min={1} max={30} value={cadence}
                     onChange={(e) => setCadence(Math.max(1, Number(e.target.value) || 1))} />
            </Field>
          </div>
        ) : (
          <div
            style={{
              padding: "12px 14px",
              background: "var(--bg)",
              border: "0.5px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--text-dim)",
              lineHeight: 1.6,
            }}
          >
            Each post lands on a random unoccupied day in the next 180 days, at one of these
            popular local hours: <strong>9 AM, 10 AM, 11 AM, 6 PM, 7 PM, 8 PM</strong>. Schedule
            fuzz still applies so post times look natural. Re-running this gives different dates.
          </div>
        )}

        <label style={{ display: "flex", gap: 8, fontSize: 13, color: "var(--text-dim)" }}>
          <input type="checkbox" checked={skipWeekends} onChange={(e) => setSkipWeekends(e.target.checked)} />
          Skip weekends
        </label>

        {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}

        {preview && (
          <div style={{ display: "grid", gap: 8 }}>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              <span style={{ color: "var(--teal)" }}>● {preview.scheduled} scheduled</span>
              {preview.skipped > 0 && (
                <span style={{ color: "var(--danger)", marginLeft: 12 }}>
                  ● {preview.skipped} skipped
                </span>
              )}
            </div>
            <div
              style={{
                background: "var(--bg)",
                border: "0.5px solid var(--border)",
                borderRadius: 8,
                maxHeight: 320,
                overflow: "auto",
              }}
            >
              {preview.slots.map((s) => (
                <div
                  key={s.post_id}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "40px 1fr auto",
                    gap: 12,
                    alignItems: "center",
                    padding: "8px 12px",
                    borderBottom: "0.5px solid var(--border)",
                    fontSize: 13,
                  }}
                >
                  <img src={thumbnailUrl(s.post_id)} alt=""
                       style={{ width: 36, height: 36, borderRadius: 6, objectFit: "cover" }} />
                  <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {s.title || s.original_filename || "(untitled)"}
                  </div>
                  <div style={{ fontSize: 12, fontFamily: "monospace" }}>
                    {s.scheduled_at ? (
                      <span style={{ color: "var(--teal)" }}>
                        {new Date(s.scheduled_at + "Z").toLocaleString(undefined, {
                          month: "short", day: "numeric",
                          hour: "2-digit", minute: "2-digit",
                        })}
                      </span>
                    ) : (
                      <span style={{ color: "var(--danger)" }}>{s.skipped_reason}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="fp-btn-ghost" onClick={onCancel}>Cancel</button>
          <button
            className="fp-btn"
            onClick={() => confirmMutation.mutate()}
            disabled={
              confirmMutation.isPending
              || !preview
              || preview.scheduled === 0
            }
          >
            {confirmMutation.isPending
              ? "Scheduling…"
              : preview ? `Schedule ${preview.scheduled}` : "Preview…"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function ModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        background: active ? "var(--hover)" : "transparent",
        color: active ? "var(--text)" : "var(--text-dim)",
        border: 0,
        padding: "7px 14px",
        fontSize: 13,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

function tomorrow(): Date {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d;
}

function formatLocalDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
