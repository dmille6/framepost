import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  type AppConfigMap,
  changePassword,
  fetchAppConfig,
  fetchDiskUsage,
  fetchHealth,
  listBackups,
  patchAppConfig,
  runBackup,
} from "../api/client";
import { useAuth } from "../auth";
import ConfigField from "../components/ConfigField";
import DiskHistoryChart from "../components/DiskHistoryChart";

const SYSTEM_FIELDS = [
  "original_retention_days",
  "storage_warning_percent",
  "storage_hardstop_gb",
  "cleanup_time",
  "flickr_sync_time",
  "flickr_max_long_edge",
  "retry_max_attempts",
  "retry_backoff_minutes",
  "upload_max_mb",
] as const;

type FormState = Record<(typeof SYSTEM_FIELDS)[number], string>;

export default function SettingsSystem() {
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <DiskPanel />
      <ConfigPanel />
      <AccountPanel />
      <BackupPanel />
      <HealthPanel />
    </div>
  );
}

// --- Disk usage ---

function DiskPanel() {
  const { data } = useQuery({ queryKey: ["disk"], queryFn: fetchDiskUsage, refetchInterval: 30_000 });
  if (!data) return null;
  const used_pct = data.used_percent;
  const overWarning = used_pct >= data.warning_percent;
  const freeGb = data.free_bytes / (1024 ** 3);
  const overHardstop = freeGb < data.hardstop_gb;

  return (
    <div className="fp-card" style={{ maxWidth: 720, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontSize: 16, fontWeight: 500 }}>Photo storage</div>
        <div style={{ fontSize: 12, color: "var(--text-fade)" }}>{data.photo_root}</div>
      </div>
      <div style={{ position: "relative", height: 14, background: "#111", borderRadius: 8, overflow: "hidden" }}>
        <div
          style={{
            width: `${Math.min(used_pct, 100)}%`,
            height: "100%",
            background: overHardstop ? "var(--danger)" : overWarning ? "#f0c97a" : "var(--teal)",
          }}
        />
        <div
          title={`warn at ${data.warning_percent}%`}
          style={{
            position: "absolute",
            left: `${data.warning_percent}%`,
            top: 0,
            bottom: 0,
            width: 1,
            background: "rgba(255,255,255,0.4)",
          }}
        />
      </div>
      <div style={{ fontSize: 13, color: "var(--text-dim)", display: "flex", justifyContent: "space-between" }}>
        <span>{used_pct.toFixed(1)}% used</span>
        <span>
          {fmtGB(data.used_bytes)} / {fmtGB(data.total_bytes)} · {fmtGB(data.free_bytes)} free
        </span>
      </div>
      {overHardstop && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          Below the {data.hardstop_gb} GB hard-stop. New imports are being refused with HTTP 507.
        </div>
      )}
      <DiskHistoryChart current={data} />
    </div>
  );
}

// --- Configurable system knobs ---

function ConfigPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["config"], queryFn: fetchAppConfig });
  const [form, setForm] = useState<FormState | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (data) setForm(toForm(data));
  }, [data]);

  const save = useMutation({
    mutationFn: patchAppConfig,
    onSuccess: (next) => {
      qc.setQueryData(["config"], next);
      qc.invalidateQueries({ queryKey: ["disk"] });
      setForm(toForm(next));
      setErrors({});
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 400) {
        const v = (err.payload as { detail?: { validation?: Record<string, string> } })
          ?.detail?.validation;
        if (v) setErrors(v);
      }
    },
  });

  if (isLoading || !form) return null;
  const dirty = data ? SYSTEM_FIELDS.some((k) => form[k] !== (data[k] ?? "")) : false;

  function set<K extends (typeof SYSTEM_FIELDS)[number]>(k: K, v: string) {
    setForm((f) => (f ? { ...f, [k]: v } : f));
  }

  function commit() {
    if (!form) return;
    const changes: Record<string, string> = {};
    for (const k of SYSTEM_FIELDS) {
      if (form[k] !== (data?.[k] ?? "")) changes[k] = form[k];
    }
    if (Object.keys(changes).length > 0) save.mutate(changes);
  }

  return (
    <div className="fp-card" style={{ maxWidth: 720, display: "grid", gap: 12 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>Storage &amp; pipeline</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Retention windows, hard-stop, cron times, retry policy, upload ceiling.
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <ConfigField label="Original retention (days)">
          <input className="fp-input" type="number" min={1} value={form.original_retention_days}
            onChange={(e) => set("original_retention_days", e.target.value)} />
          {errors.original_retention_days && <Err msg={errors.original_retention_days} />}
        </ConfigField>
        <ConfigField label="Warning %" hint="storage warn badge">
          <input className="fp-input" type="number" min={50} max={99} value={form.storage_warning_percent}
            onChange={(e) => set("storage_warning_percent", e.target.value)} />
          {errors.storage_warning_percent && <Err msg={errors.storage_warning_percent} />}
        </ConfigField>
        <ConfigField label="Hard-stop (GB free)">
          <input className="fp-input" type="number" min={1} value={form.storage_hardstop_gb}
            onChange={(e) => set("storage_hardstop_gb", e.target.value)} />
          {errors.storage_hardstop_gb && <Err msg={errors.storage_hardstop_gb} />}
        </ConfigField>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <ConfigField label="Cleanup time (UTC)">
          <input className="fp-input" type="time" value={form.cleanup_time}
            onChange={(e) => set("cleanup_time", e.target.value)} />
          {errors.cleanup_time && <Err msg={errors.cleanup_time} />}
        </ConfigField>
        <ConfigField label="Flickr sync time (UTC)">
          <input className="fp-input" type="time" value={form.flickr_sync_time}
            onChange={(e) => set("flickr_sync_time", e.target.value)} />
          {errors.flickr_sync_time && <Err msg={errors.flickr_sync_time} />}
        </ConfigField>
        <ConfigField label="Flickr max long edge (px)" hint="0 disables resize">
          <input className="fp-input" type="number" min={0} value={form.flickr_max_long_edge}
            onChange={(e) => set("flickr_max_long_edge", e.target.value)} />
          {errors.flickr_max_long_edge && <Err msg={errors.flickr_max_long_edge} />}
        </ConfigField>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr 1fr", gap: 12 }}>
        <ConfigField label="Retry max attempts">
          <input className="fp-input" type="number" min={1} value={form.retry_max_attempts}
            onChange={(e) => set("retry_max_attempts", e.target.value)} />
          {errors.retry_max_attempts && <Err msg={errors.retry_max_attempts} />}
        </ConfigField>
        <ConfigField label="Backoff schedule (minutes)" hint="comma-separated">
          <input className="fp-input" value={form.retry_backoff_minutes}
            onChange={(e) => set("retry_backoff_minutes", e.target.value)} />
          {errors.retry_backoff_minutes && <Err msg={errors.retry_backoff_minutes} />}
        </ConfigField>
        <ConfigField label="Upload ceiling (MB)">
          <input className="fp-input" type="number" min={1} value={form.upload_max_mb}
            onChange={(e) => set("upload_max_mb", e.target.value)} />
          {errors.upload_max_mb && <Err msg={errors.upload_max_mb} />}
        </ConfigField>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button className="fp-btn-ghost" disabled={!dirty} onClick={() => data && setForm(toForm(data))}>
          Reset
        </button>
        <button className="fp-btn" disabled={!dirty || save.isPending} onClick={commit}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function toForm(c: AppConfigMap): FormState {
  return {
    original_retention_days: c.original_retention_days ?? "30",
    storage_warning_percent: c.storage_warning_percent ?? "80",
    storage_hardstop_gb: c.storage_hardstop_gb ?? "5",
    cleanup_time: c.cleanup_time ?? "03:00",
    flickr_sync_time: c.flickr_sync_time ?? "04:00",
    flickr_max_long_edge: c.flickr_max_long_edge ?? "2048",
    retry_max_attempts: c.retry_max_attempts ?? "5",
    retry_backoff_minutes: c.retry_backoff_minutes ?? "1,5,15,60,240",
    upload_max_mb: c.upload_max_mb ?? "200",
  };
}

// --- Account ---

function AccountPanel() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const mut = useMutation({
    mutationFn: ({ c, n }: { c: string; n: string }) => changePassword(c, n),
    onSuccess: () => {
      setMsg({ ok: true, text: "Password updated." });
      setCurrent(""); setNext(""); setConfirm("");
    },
    onError: (e) => setMsg({ ok: false, text: e instanceof Error ? e.message : "failed" }),
  });

  function submit() {
    if (next !== confirm) {
      setMsg({ ok: false, text: "New passwords don't match" });
      return;
    }
    if (next.length < 8) {
      setMsg({ ok: false, text: "Must be ≥ 8 characters" });
      return;
    }
    mut.mutate({ c: current, n: next });
  }

  return (
    <div className="fp-card" style={{ maxWidth: 720, display: "grid", gap: 12 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>Account</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Signed in as <span style={{ color: "var(--text)" }}>{user?.username}</span>.
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
        <ConfigField label="Current password">
          <input className="fp-input" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
        </ConfigField>
        <ConfigField label="New password" hint="≥ 8 chars">
          <input className="fp-input" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
        </ConfigField>
        <ConfigField label="Confirm new">
          <input className="fp-input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
        </ConfigField>
      </div>
      {msg && (
        <div style={{ fontSize: 13, color: msg.ok ? "var(--teal)" : "var(--danger)" }}>{msg.text}</div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button className="fp-btn" disabled={mut.isPending || !current || !next || !confirm} onClick={submit}>
          {mut.isPending ? "Updating…" : "Change password"}
        </button>
      </div>
    </div>
  );
}

// --- Backups ---

function BackupPanel() {
  const qc = useQueryClient();
  const { data: backups = [] } = useQuery({ queryKey: ["backups"], queryFn: listBackups, refetchInterval: 60_000 });
  const trigger = useMutation({
    mutationFn: runBackup,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backups"] }),
  });

  return (
    <div className="fp-card" style={{ maxWidth: 720, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Database backups</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            Hot backups land on the photo volume so they survive an OS-disk failure.
            Daily cron runs at the cleanup time configured above.
          </div>
        </div>
        <button className="fp-btn" onClick={() => trigger.mutate()} disabled={trigger.isPending}>
          {trigger.isPending ? "Backing up…" : "Run backup now"}
        </button>
      </div>
      {trigger.error && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          {trigger.error instanceof Error ? trigger.error.message : "failed"}
        </div>
      )}
      {backups.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--text-dim)" }}>No backups yet.</div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-fade)", fontSize: 11 }}>
              <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>Backup</th>
              <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>Size</th>
              <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>Created</th>
            </tr>
          </thead>
          <tbody>
            {backups.map((b) => (
              <tr key={b.name}>
                <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>{b.name}</td>
                <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)", color: "var(--text-dim)" }}>
                  {fmtBytes(b.size_bytes)}
                </td>
                <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)", color: "var(--text-fade)", fontSize: 12 }}>
                  {new Date(b.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// --- Health ---

function HealthPanel() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 30_000 });
  if (!data) return null;
  const items: [string, string][] = [
    ["Status", data.status],
    ["Worker alive", data.worker_alive ? "yes" : "no"],
    ["DB writable", data.db_writable ? "yes" : "no"],
    ["Photo volume", data.photo_volume_writable ? `${data.photo_volume_free_gb} GB free` : "not writable"],
    ["Last Flickr success", data.flickr_last_success ? new Date(data.flickr_last_success).toLocaleString() : "—"],
    ["Last backup", data.last_backup ? new Date(data.last_backup).toLocaleString() : "—"],
    ["Version", data.version],
  ];
  return (
    <div className="fp-card" style={{ maxWidth: 720, display: "grid", gap: 12 }}>
      <div style={{ fontSize: 16, fontWeight: 500 }}>System health</div>
      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", rowGap: 4, columnGap: 16, fontSize: 13 }}>
        {items.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <div style={{ color: "var(--text-fade)" }}>{k}</div>
            <div style={{ color: "var(--text-dim)" }}>{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Err({ msg }: { msg: string }) {
  return <div style={{ color: "var(--danger)", fontSize: 11 }}>{msg}</div>;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtGB(n: number): string {
  return `${(n / (1024 ** 3)).toFixed(2)} GB`;
}
