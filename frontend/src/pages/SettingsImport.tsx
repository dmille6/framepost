import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, fetchWatchConfig, updateWatchConfig } from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

export default function SettingsImport() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["watch-config"],
    queryFn: fetchWatchConfig,
    refetchInterval: 15_000,
  });

  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (data) setPath(data.path);
  }, [data?.path]);

  const updateMutation = useMutation({
    mutationFn: updateWatchConfig,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["watch-config"] });
      setError(null);
    },
    onError: (e) => {
      if (e instanceof ApiError) setError(e.message);
      else setError("Failed to update");
    },
  });

  if (isLoading || !data) {
    return (
      <div className="fp-card" style={{ display: "grid", gap: 12, maxWidth: 640 }}>
        <SkeletonRows count={3} />
      </div>
    );
  }

  const lastImported = data.last_imported_at
    ? new Date(data.last_imported_at).toLocaleString()
    : "—";

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 640 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>Watch folder</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          When enabled, FramePost watches a directory for new image files and runs them
          through the same import pipeline as browser uploads.
        </div>
      </div>

      <label style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--text-dim)" }}>
        Path
        <input
          className="fp-input"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="/mnt/photo-data/incoming"
        />
      </label>

      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="fp-btn-ghost"
          onClick={() => updateMutation.mutate({ path })}
          disabled={updateMutation.isPending || path === data.path}
        >
          Save path
        </button>
        <button
          className="fp-btn"
          onClick={() => updateMutation.mutate({ enabled: !data.enabled })}
          disabled={updateMutation.isPending || (!data.enabled && !path)}
        >
          {data.enabled ? "Disable" : "Enable"}
        </button>
      </div>

      {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}

      <div
        style={{
          background: "var(--bg)",
          padding: 12,
          borderRadius: 8,
          fontSize: 12,
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          rowGap: 6,
          columnGap: 16,
        }}
      >
        <div style={{ color: "var(--text-fade)" }}>Status</div>
        <div>
          <span
            style={{
              color: data.status === "active" ? "var(--teal)" : "var(--text-dim)",
            }}
          >
            ●
          </span>{" "}
          {data.status}
        </div>
        <div style={{ color: "var(--text-fade)" }}>Last imported</div>
        <div>{lastImported}</div>
        <div style={{ color: "var(--text-fade)" }}>Errors</div>
        <div>{data.error_count}</div>
        {data.last_error && (
          <>
            <div style={{ color: "var(--text-fade)" }}>Last error</div>
            <div style={{ color: "var(--danger)" }}>{data.last_error}</div>
          </>
        )}
      </div>

      <div style={{ fontSize: 12, color: "var(--text-fade)" }}>
        Reconcile runs every 30 s — toggling enable/disable takes effect within ~30 s as the
        worker picks up the change. File-stability is enforced before import (file size and
        mtime must be unchanged for one full poll cycle).
      </div>
    </div>
  );
}
