import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, listAlbums, triggerAlbumSync } from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

export default function SettingsAlbums() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["albums"],
    queryFn: listAlbums,
    refetchInterval: 60_000,
  });

  const syncMutation = useMutation({
    mutationFn: triggerAlbumSync,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["albums"] }),
  });

  const error = syncMutation.error instanceof ApiError ? syncMutation.error.message : null;

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 720 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 500 }}>Flickr albums</div>
          <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
            Synced from Flickr. Pick which albums a post joins in the Draft Queue metadata editor.
          </div>
        </div>
        <button
          className="fp-btn"
          onClick={() => syncMutation.mutate()}
          disabled={syncMutation.isPending}
        >
          {syncMutation.isPending ? "Syncing…" : "Sync now"}
        </button>
      </div>
      {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}
      {syncMutation.data && (
        <div style={{ color: "var(--teal)", fontSize: 13 }}>
          Synced {syncMutation.data.synced} album(s).
        </div>
      )}
      {isLoading ? (
        <SkeletonRows count={3} />
      ) : data.length === 0 ? (
        <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
          No albums yet. Connect Flickr in the Flickr tab, then click Sync now.
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-fade)", fontSize: 11 }}>
              <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>Album</th>
              <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>Photos</th>
              <th style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>Last synced</th>
            </tr>
          </thead>
          <tbody>
            {data.map((a) => (
              <tr key={a.id}>
                <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)" }}>
                  {a.name}
                  {a.description && (
                    <div style={{ fontSize: 11, color: "var(--text-fade)" }}>{a.description}</div>
                  )}
                </td>
                <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)", color: "var(--text-dim)" }}>
                  {a.photo_count}
                </td>
                <td style={{ padding: "8px 0", borderBottom: "0.5px solid var(--border)", color: "var(--text-fade)", fontSize: 12 }}>
                  {a.last_synced_at ? new Date(a.last_synced_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
