import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  disconnectFlickr,
  fetchFlickrStatus,
  flickrConnectUrl,
} from "../api/client";
import { SkeletonRows } from "../components/Skeleton";

export default function SettingsFlickr() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  // Pick up ?connected=1 / ?error=... from the OAuth callback redirect.
  useEffect(() => {
    if (params.get("connected") === "1") {
      setBanner({ kind: "ok", text: "Connected to Flickr." });
      void qc.invalidateQueries({ queryKey: ["flickr-status"] });
      params.delete("connected");
      setParams(params, { replace: true });
    }
    const err = params.get("error");
    if (err) {
      setBanner({ kind: "error", text: err });
      params.delete("error");
      setParams(params, { replace: true });
    }
  }, []);

  const { data, isLoading } = useQuery({
    queryKey: ["flickr-status"],
    queryFn: fetchFlickrStatus,
    refetchInterval: 30_000,
  });

  const disconnectMutation = useMutation({
    mutationFn: disconnectFlickr,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["flickr-status"] });
      setBanner({ kind: "ok", text: "Disconnected." });
    },
  });

  if (isLoading || !data) {
    return (
      <div className="fp-card" style={{ display: "grid", gap: 12, maxWidth: 640 }}>
        <SkeletonRows count={4} />
      </div>
    );
  }

  const connectedAt = data.connected_at ? new Date(data.connected_at).toLocaleString() : "—";

  return (
    <div className="fp-card" style={{ display: "grid", gap: 16, maxWidth: 640 }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 500 }}>Flickr</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>
          Connect FramePost to your Flickr account so the worker can post on your behalf.
          Tokens are encrypted at rest with the configured Fernet key.
        </div>
      </div>

      {banner && (
        <div
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            fontSize: 13,
            background: banner.kind === "ok" ? "#1d3328" : "#3a1818",
            color: banner.kind === "ok" ? "#7adcb1" : "#f59c9c",
          }}
        >
          {banner.text}
        </div>
      )}

      <div
        style={{
          background: "var(--bg)",
          padding: 12,
          borderRadius: 8,
          fontSize: 13,
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          rowGap: 6,
          columnGap: 16,
        }}
      >
        <div style={{ color: "var(--text-fade)" }}>Status</div>
        <div style={{ color: data.connected ? "var(--teal)" : "var(--text-dim)" }}>
          {data.connected ? "● Connected" : "○ Not connected"}
        </div>
        <div style={{ color: "var(--text-fade)" }}>Account</div>
        <div>{data.account_name ?? "—"}</div>
        <div style={{ color: "var(--text-fade)" }}>Connected at</div>
        <div>{connectedAt}</div>
        <div style={{ color: "var(--text-fade)" }}>Key version</div>
        <div>{data.key_version ?? "—"}</div>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        {!data.connected ? (
          <a className="fp-btn" href={flickrConnectUrl} style={{ textDecoration: "none" }}>
            Connect Flickr
          </a>
        ) : (
          <>
            <a className="fp-btn-ghost" href={flickrConnectUrl} style={{ textDecoration: "none" }}>
              Reconnect
            </a>
            <button
              className="fp-btn-ghost"
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
            >
              {disconnectMutation.isPending ? "Working…" : "Disconnect"}
            </button>
          </>
        )}
      </div>

      <div style={{ fontSize: 12, color: "var(--text-fade)" }}>
        Phase 3A wires the OAuth flow and stores tokens encrypted. Real posts to Flickr
        come in Phase 3B — until then the worker still simulates the upload.
      </div>
    </div>
  );
}
