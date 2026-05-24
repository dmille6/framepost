import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAppConfig,
  listScheduled,
  schedulePost,
  type ScheduledItem,
  unschedulePost,
} from "../api/client";
import Calendar from "../components/Calendar";
import PageHeader from "../components/PageHeader";
import RescheduleSidebar from "../components/RescheduleSidebar";
import ScheduledList from "../components/ScheduledList";
import ScheduleDialog from "../components/ScheduleDialog";
import ScheduledItemModal from "../components/ScheduledItemModal";
import Topbar from "../components/Topbar";
import { usePageTitle } from "../hooks/usePageTitle";

type View = "calendar" | "list";

type DragSchedule = { postId: string; date: Date };

export default function Scheduled() {
  usePageTitle("Scheduled");
  const qc = useQueryClient();
  const [view, setView] = useState<View>(() => {
    const saved = localStorage.getItem("framepost.scheduled.view");
    return saved === "list" ? "list" : "calendar";
  });
  const [month, setMonth] = useState(() => firstOfMonth(new Date()));
  const [selected, setSelected] = useState<ScheduledItem | null>(null);
  const [rescheduling, setRescheduling] = useState(false);
  const [dragSchedule, setDragSchedule] = useState<DragSchedule | null>(null);

  function setViewPersistent(v: View) {
    setView(v);
    localStorage.setItem("framepost.scheduled.view", v);
  }

  const range = useMemo(() => {
    // Pull a window that comfortably covers the visible 6-week grid (42 days starting on a Sunday).
    const start = new Date(month);
    start.setDate(start.getDate() - 14);
    const end = new Date(month);
    end.setMonth(end.getMonth() + 1);
    end.setDate(end.getDate() + 14);
    return { from: start.toISOString(), to: end.toISOString() };
  }, [month]);

  const { data: items = [] } = useQuery({
    queryKey: ["schedule", range.from, range.to],
    queryFn: () => listScheduled(range.from, range.to),
    refetchInterval: 60_000,
  });

  // Default-publish-time is set by the user in Settings → General, stored as "HH:MM" local.
  const { data: cfg } = useQuery({ queryKey: ["config"], queryFn: fetchAppConfig });
  const defaultPublishTime = cfg?.default_publish_time || "09:00";
  const fuzzMinutes = Number(cfg?.schedule_fuzz_minutes ?? "0") || 0;

  const unscheduleMutation = useMutation({
    mutationFn: unschedulePost,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      setSelected(null);
    },
  });

  const rescheduleMutation = useMutation({
    mutationFn: ({ id, iso }: { id: string; iso: string }) => schedulePost(id, iso),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["schedule"] });
      void qc.invalidateQueries({ queryKey: ["drafts"] });
      setRescheduling(false);
      setSelected(null);
      setDragSchedule(null);
    },
  });

  return (
    <>
      <Topbar />
      <div className="fp-page fp-fade-in">
        <PageHeader
          title="Scheduled"
          subtitle={`${items.length} post${items.length === 1 ? "" : "s"} scheduled in this window`}
          actions={
            <div
              role="tablist"
              style={{
                display: "inline-flex",
                gap: 0,
                border: "0.5px solid var(--border-strong)",
                borderRadius: 8,
                overflow: "hidden",
              }}
            >
              <ToggleButton active={view === "calendar"} onClick={() => setViewPersistent("calendar")}>
                Calendar
              </ToggleButton>
              <ToggleButton active={view === "list"} onClick={() => setViewPersistent("list")}>
                List
              </ToggleButton>
            </div>
          }
        />

        {view === "calendar" ? (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 320px", gap: 16, alignItems: "start" }}>
            <Calendar
              month={month}
              items={items}
              onPrev={() => setMonth(addMonth(month, -1))}
              onNext={() => setMonth(addMonth(month, 1))}
              onToday={() => setMonth(firstOfMonth(new Date()))}
              onPick={(it) => setSelected(it)}
              onDayDrop={(date, postId) => setDragSchedule({ postId, date })}
            />
            <div style={{ position: "sticky", top: 80 }}>
              <RescheduleSidebar />
            </div>
          </div>
        ) : (
          <ScheduledList items={items} onPick={(it) => setSelected(it)} />
        )}
      </div>

      {selected && !rescheduling && (
        <ScheduledItemModal
          item={selected}
          busy={unscheduleMutation.isPending}
          onClose={() => setSelected(null)}
          onReschedule={() => setRescheduling(true)}
          onUnschedule={async () => {
            await unscheduleMutation.mutateAsync(selected.id);
          }}
        />
      )}

      {selected && rescheduling && (
        <ScheduleDialog
          postTitle={selected.title || selected.original_filename || "(untitled)"}
          initial={selected.scheduled_at}
          onCancel={() => setRescheduling(false)}
          onSubmit={async (iso) => {
            await rescheduleMutation.mutateAsync({ id: selected.id, iso });
          }}
        />
      )}

      {dragSchedule && (
        <ScheduleDialog
          postTitle="Drag-scheduled draft"
          initial={localDateAtTimeToUtcInitial(dragSchedule.date, defaultPublishTime, fuzzMinutes)}
          onCancel={() => setDragSchedule(null)}
          onSubmit={async (iso) => {
            await rescheduleMutation.mutateAsync({ id: dragSchedule.postId, iso });
          }}
        />
      )}
    </>
  );
}

function localDateAtTimeToUtcInitial(date: Date, hhmm: string, fuzzMinutes: number = 0): string {
  // Build a Date for the user's local YYYY-MM-DD at hhmm (their local time), then emit as
  // a UTC ISO without the trailing Z — ScheduleDialog appends Z when parsing, so the round
  // trip ends up displaying correctly in the user's timezone. Without this conversion the
  // dialog would interpret e.g. "09:00" as UTC and show 04:00 to a Central-time user.
  //
  // fuzzMinutes > 0 adds a random 0-N minute offset + random seconds so drag-scheduled posts
  // don't all land at exactly :00:00 — looks more human, less like a scheduler. We always add
  // (never subtract) so the drag-target hour is preserved.
  const [hStr, mStr] = (hhmm || "09:00").split(":");
  const minuteOffset = fuzzMinutes > 0 ? Math.floor(Math.random() * (fuzzMinutes + 1)) : 0;
  const secondOffset = fuzzMinutes > 0 ? Math.floor(Math.random() * 60) : 0;
  const local = new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    Number(hStr) || 9,
    (Number(mStr) || 0) + minuteOffset,
    secondOffset,
    0,
  );
  return local.toISOString().replace(/\.\d{3}Z$/, "");
}

function firstOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}

function addMonth(d: Date, delta: number): Date {
  const out = new Date(d);
  out.setMonth(out.getMonth() + delta);
  return out;
}

function ToggleButton({
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
        padding: "8px 16px",
        fontSize: 13,
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}
