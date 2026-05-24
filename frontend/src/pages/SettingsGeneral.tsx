import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  type AppConfigMap,
  fetchAppConfig,
  patchAppConfig,
} from "../api/client";
import ConfigField from "../components/ConfigField";
import { SkeletonRows } from "../components/Skeleton";

type FormState = {
  studio_name: string;
  timezone: string;
  start_page: string;
  session_timeout_minutes: string;
  default_publish_time: string;
  default_privacy: string;
  default_safety_level: string;
  default_content_type: string;
  max_groups_default: string;
  warn_groups_threshold: string;
  schedule_fuzz_minutes: string;
  instagram_signature: string;
};

const FIELDS: (keyof FormState)[] = [
  "studio_name",
  "timezone",
  "start_page",
  "session_timeout_minutes",
  "default_publish_time",
  "default_privacy",
  "default_safety_level",
  "default_content_type",
  "max_groups_default",
  "warn_groups_threshold",
  "schedule_fuzz_minutes",
  "instagram_signature",
];

export default function SettingsGeneral() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["config"], queryFn: fetchAppConfig });
  const [form, setForm] = useState<FormState | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (data) setForm(toForm(data));
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: patchAppConfig,
    onSuccess: (next) => {
      qc.setQueryData(["config"], next);
      setForm(toForm(next));
      setErrors({});
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 400) {
        const validation = (err.payload as { detail?: { validation?: Record<string, string> } })
          ?.detail?.validation;
        if (validation) setErrors(validation);
      }
    },
  });

  if (isLoading || !form) {
    return (
      <div className="fp-card" style={{ display: "grid", gap: 12, maxWidth: 640 }}>
        <SkeletonRows count={6} />
      </div>
    );
  }

  const dirty = data ? FIELDS.some((k) => form[k] !== (data[k] ?? "")) : false;

  function set<K extends keyof FormState>(k: K, v: string) {
    setForm((f) => (f ? { ...f, [k]: v } : f));
  }

  function save() {
    if (!form) return;
    const changes: Record<string, string> = {};
    for (const k of FIELDS) {
      const original = data?.[k] ?? "";
      if (form[k] !== original) changes[k] = form[k];
    }
    if (Object.keys(changes).length > 0) saveMutation.mutate(changes);
  }

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 720 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>General</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Workspace identity, default values applied to new posts, and your session timeout.
        </div>
      </div>

      <ConfigField label="Studio name">
        <input className="fp-input" value={form.studio_name} onChange={(e) => set("studio_name", e.target.value)} />
        {errors.studio_name && <FieldError msg={errors.studio_name} />}
      </ConfigField>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <ConfigField label="Time zone" hint="e.g. America/Chicago">
          <input className="fp-input" value={form.timezone} onChange={(e) => set("timezone", e.target.value)} />
          {errors.timezone && <FieldError msg={errors.timezone} />}
        </ConfigField>
        <ConfigField label="Theme">
          <input className="fp-input" value="dark" disabled />
        </ConfigField>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <ConfigField label="Start page">
          <select className="fp-select" value={form.start_page} onChange={(e) => set("start_page", e.target.value)}>
            <option value="draft_queue">Draft Queue</option>
            <option value="scheduled">Scheduled</option>
            <option value="published">Published</option>
          </select>
        </ConfigField>
        <ConfigField label="Session timeout (min)">
          <input
            className="fp-input"
            type="number"
            min={5}
            value={form.session_timeout_minutes}
            onChange={(e) => set("session_timeout_minutes", e.target.value)}
          />
          {errors.session_timeout_minutes && <FieldError msg={errors.session_timeout_minutes} />}
        </ConfigField>
        <ConfigField label="Default publish time">
          <input
            className="fp-input"
            type="time"
            value={form.default_publish_time}
            onChange={(e) => set("default_publish_time", e.target.value)}
          />
          {errors.default_publish_time && <FieldError msg={errors.default_publish_time} />}
        </ConfigField>
      </div>

      <div style={{ fontSize: 13, color: "var(--text)", marginTop: 8 }}>Default for new posts</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <ConfigField label="Privacy">
          <select className="fp-select" value={form.default_privacy} onChange={(e) => set("default_privacy", e.target.value)}>
            <option value="private">Private</option>
            <option value="friends_family">Friends & Family</option>
            <option value="public">Public</option>
          </select>
        </ConfigField>
        <ConfigField label="Safety">
          <select className="fp-select" value={form.default_safety_level} onChange={(e) => set("default_safety_level", e.target.value)}>
            <option value="safe">Safe</option>
            <option value="moderate">Moderate</option>
            <option value="restricted">Restricted</option>
          </select>
        </ConfigField>
        <ConfigField label="Type">
          <select className="fp-select" value={form.default_content_type} onChange={(e) => set("default_content_type", e.target.value)}>
            <option value="photo">Photo</option>
            <option value="screenshot">Screenshot</option>
            <option value="other">Other</option>
          </select>
        </ConfigField>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <ConfigField label="Max groups per post">
          <input
            className="fp-input"
            type="number"
            min={1}
            value={form.max_groups_default}
            onChange={(e) => set("max_groups_default", e.target.value)}
          />
          {errors.max_groups_default && <FieldError msg={errors.max_groups_default} />}
        </ConfigField>
        <ConfigField label="Warn at groups threshold">
          <input
            className="fp-input"
            type="number"
            min={1}
            value={form.warn_groups_threshold}
            onChange={(e) => set("warn_groups_threshold", e.target.value)}
          />
          {errors.warn_groups_threshold && <FieldError msg={errors.warn_groups_threshold} />}
        </ConfigField>
      </div>

      <ConfigField
        label="Schedule fuzz (minutes)"
        hint="Adds a random 0–N minute offset (plus random seconds) to Smart Fill and drag-scheduled posts so they don't all land at exactly :00:00. Set 0 to disable. 5 is a good default."
      >
        <input
          className="fp-input"
          type="number"
          min={0}
          max={30}
          value={form.schedule_fuzz_minutes}
          onChange={(e) => set("schedule_fuzz_minutes", e.target.value)}
          style={{ maxWidth: 120 }}
        />
        {errors.schedule_fuzz_minutes && <FieldError msg={errors.schedule_fuzz_minutes} />}
      </ConfigField>

      <div style={{ fontSize: 13, color: "var(--text)", marginTop: 8 }}>Instagram</div>
      <ConfigField
        label="Caption signature"
        hint="Auto-appended to every Instagram caption. Leave blank to skip."
      >
        <textarea
          className="fp-textarea"
          value={form.instagram_signature}
          onChange={(e) => set("instagram_signature", e.target.value)}
          placeholder="📷 Darrell Miller Photography&#10;darrellmiller.photo"
          rows={3}
        />
        {errors.instagram_signature && <FieldError msg={errors.instagram_signature} />}
      </ConfigField>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
        <button className="fp-btn-ghost" disabled={!dirty} onClick={() => data && setForm(toForm(data))}>
          Reset
        </button>
        <button className="fp-btn" disabled={!dirty || saveMutation.isPending} onClick={save}>
          {saveMutation.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function toForm(c: AppConfigMap): FormState {
  return {
    studio_name: c.studio_name ?? "",
    timezone: c.timezone ?? "America/Chicago",
    start_page: c.start_page ?? "draft_queue",
    session_timeout_minutes: c.session_timeout_minutes ?? "1440",
    default_publish_time: c.default_publish_time ?? "10:00",
    default_privacy: c.default_privacy ?? "public",
    default_safety_level: c.default_safety_level ?? "safe",
    default_content_type: c.default_content_type ?? "photo",
    max_groups_default: c.max_groups_default ?? "5",
    warn_groups_threshold: c.warn_groups_threshold ?? "8",
    schedule_fuzz_minutes: c.schedule_fuzz_minutes ?? "5",
    instagram_signature: c.instagram_signature ?? "",
  };
}

function FieldError({ msg }: { msg: string }) {
  return <div style={{ color: "var(--danger)", fontSize: 11 }}>{msg}</div>;
}
