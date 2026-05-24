import { useEffect, useState } from "react";

import { ApiError } from "../api/client";

type Props = {
  postTitle: string;
  initial?: string | null;            // existing scheduled_at (UTC ISO) for reschedule
  onCancel: () => void;
  onSubmit: (utcIso: string) => Promise<void>;
};

// Local form: split date + time inputs (in the user's browser local TZ for UX),
// converted to UTC ISO at submit time. Backend stores UTC and enforces hour rules.
export default function ScheduleDialog({ postTitle, initial, onCancel, onSubmit }: Props) {
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const start = initial ? new Date(initial + "Z") : nextHourSlot();
    setDate(formatLocalDate(start));
    setTime(formatLocalTime(start));
  }, [initial]);

  async function handleSubmit() {
    setError(null);
    if (!date || !time) {
      setError("date and time required");
      return;
    }
    const local = new Date(`${date}T${time}`);
    if (Number.isNaN(local.getTime())) {
      setError("invalid date/time");
      return;
    }
    if (local.getTime() <= Date.now()) {
      setError("must be in the future");
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(local.toISOString());
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.payload as { detail?: { message?: string } })?.detail;
        if (e.status === 409 && detail?.message) setError(detail.message);
        else setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : "failed to schedule");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "grid", placeItems: "center", zIndex: 100,
      }}
      onClick={onCancel}
    >
      <div
        className="fp-card"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 420, display: "grid", gap: 16 }}
      >
        <div>
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
            {initial ? "Reschedule" : "Schedule on Flickr"}
          </div>
          <div style={{ fontSize: 16, fontWeight: 500, marginTop: 2 }}>{postTitle}</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
            Date
            <input
              type="date"
              className="fp-input"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
          <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
            Time
            <input
              type="time"
              className="fp-input"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
          </label>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-fade)" }}>
          One post per clock hour. Times shown in your local timezone — stored in UTC.
        </div>
        {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="fp-btn-ghost" onClick={onCancel}>Cancel</button>
          <button className="fp-btn" onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Saving…" : initial ? "Reschedule" : "Schedule"}
          </button>
        </div>
      </div>
    </div>
  );
}

function nextHourSlot(): Date {
  const d = new Date();
  d.setHours(d.getHours() + 1, 0, 0, 0);
  return d;
}

function formatLocalDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatLocalTime(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
