import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  ApiError,
  type BlueskyStatus,
  type PinterestStatus,
  type PixelfedStatus,
  connectBluesky,
  disconnectBluesky,
  disconnectPinterest,
  disconnectPixelfed,
  fetchAppConfig,
  fetchBlueskyStatus,
  fetchFlickrStatus,
  fetchPinterestBoards,
  fetchPinterestStatus,
  fetchPixelfedStatus,
  flickrConnectUrl,
  patchAppConfig,
  pinterestConnectUrl,
  pixelfedConnectUrl,
  setPinterestDefaultBoard,
  setPlatformDefaultTarget,
  testBluesky,
} from "../api/client";
import { CardHeader } from "../components/PageHeader";
import { SkeletonRows } from "../components/Skeleton";
import { absoluteTime, relativeTime } from "../lib/time";

export default function SettingsPlatforms() {
  const [params, setParams] = useSearchParams();
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    const connected = params.get("connected");
    if (connected === "pixelfed") {
      setBanner({ kind: "ok", text: "Pixelfed connected." });
      params.delete("connected");
      setParams(params, { replace: true });
    }
    if (connected === "pinterest") {
      setBanner({ kind: "ok", text: "Pinterest connected. Pick a default board below to start pinning." });
      params.delete("connected");
      setParams(params, { replace: true });
    }
    const error = params.get("error");
    if (error) {
      setBanner({ kind: "error", text: error });
      params.delete("error");
      setParams(params, { replace: true });
    }
  }, [params, setParams]);

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 800 }}>
      {banner && (
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 8,
            background: banner.kind === "ok" ? "var(--teal-tint)" : "var(--danger-tint)",
            color: banner.kind === "ok" ? "var(--teal)" : "var(--danger)",
            border: `0.5px solid ${banner.kind === "ok" ? "rgba(93,202,165,0.2)" : "rgba(245,156,156,0.2)"}`,
            fontSize: 13,
          }}
        >
          {banner.text}
        </div>
      )}

      <FlickrPanel />
      <BlueskyPanel />
      <PixelfedPanel />
      <PinterestPanel />
      <RedditPanel />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Flickr — already wired elsewhere; here we just show summary + link.
// ---------------------------------------------------------------------------
function FlickrPanel() {
  const { data } = useQuery({ queryKey: ["flickr-status"], queryFn: fetchFlickrStatus });
  const connected = data?.connected ?? false;

  return (
    <div className="fp-card">
      <CardHeader
        title="Flickr"
        subtitle={connected
          ? `Connected as ${data?.account_name ?? ""}`
          : "Primary publishing platform — connect via OAuth."}
        action={
          connected ? (
            <a href="/settings/flickr" className="fp-btn-ghost" style={{ padding: "7px 12px", fontSize: 13 }}>
              Manage
            </a>
          ) : (
            <a href={flickrConnectUrl} className="fp-btn" style={{ padding: "7px 14px", fontSize: 13 }}>
              Connect Flickr
            </a>
          )
        }
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bluesky
// ---------------------------------------------------------------------------
function BlueskyPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["bluesky-status"],
    queryFn: fetchBlueskyStatus,
  });

  const [handle, setHandle] = useState("");
  const [appPw, setAppPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const connect = useMutation({
    mutationFn: ({ handle, password }: { handle: string; password: string }) =>
      connectBluesky(handle, password),
    onSuccess: () => {
      setHandle("");
      setAppPw("");
      setError(null);
      void qc.invalidateQueries({ queryKey: ["bluesky-status"] });
    },
    onError: (e) => {
      setError(e instanceof ApiError ? e.message : "Connection failed");
    },
  });

  const disconnect = useMutation({
    mutationFn: () => disconnectBluesky(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bluesky-status"] }),
  });

  const test = useMutation({
    mutationFn: () => testBluesky(),
    onSuccess: (r) => setTestResult({ kind: "ok", text: `Logged in as @${r.handle} (${r.followers} followers).` }),
    onError: (e) => setTestResult({ kind: "error", text: e instanceof Error ? e.message : "Test failed" }),
  });

  const toggleDefault = useMutation({
    mutationFn: (next: boolean) => setPlatformDefaultTarget("bluesky", next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bluesky-status"] }),
  });

  // Default hashtags live in app_config. We fetch once and let the user edit inline.
  const { data: cfg } = useQuery({ queryKey: ["config"], queryFn: fetchAppConfig });
  const [defaultHashtags, setDefaultHashtags] = useState("");
  const [hashtagsSaved, setHashtagsSaved] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [hashtagsError, setHashtagsError] = useState<string | null>(null);
  useEffect(() => {
    if (cfg && cfg.bluesky_default_hashtags !== undefined) {
      setDefaultHashtags(cfg.bluesky_default_hashtags ?? "");
    }
  }, [cfg?.bluesky_default_hashtags]);
  const savedDefaults = cfg?.bluesky_default_hashtags ?? "";
  const hashtagsDirty = defaultHashtags.trim() !== savedDefaults.trim();

  const saveHashtags = useMutation({
    mutationFn: () =>
      patchAppConfig({ bluesky_default_hashtags: defaultHashtags.trim() }),
    onMutate: () => {
      setHashtagsSaved("saving");
      setHashtagsError(null);
    },
    onSuccess: () => {
      setHashtagsSaved("saved");
      void qc.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (e) => {
      setHashtagsSaved("error");
      const detail = e instanceof ApiError ? (e.payload as { detail?: { validation?: Record<string, string> } })?.detail?.validation : null;
      setHashtagsError(
        detail?.bluesky_default_hashtags ??
          (e instanceof Error ? e.message : "Save failed"),
      );
    },
  });

  if (isLoading) return <div className="fp-card"><SkeletonRows count={3} /></div>;
  const connected = data?.connected ?? false;

  return (
    <div className="fp-card">
      <CardHeader
        title="Bluesky"
        subtitle={connected
          ? <ConnectedSubtitle status={data!} />
          : "Auto-publish to bsky.social. Uses an app-specific password — your main login isn't shared with FramePost."}
      />

      {!connected ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!handle.trim() || !appPw.trim()) return;
            connect.mutate({ handle: handle.trim(), password: appPw });
          }}
          style={{ display: "grid", gap: 10 }}
        >
          <Field label="Handle" hint="e.g. dmillerphotography.bsky.social">
            <input
              className="fp-input"
              value={handle}
              onChange={(e) => setHandle(e.target.value)}
              placeholder="yourhandle.bsky.social"
              autoComplete="off"
            />
          </Field>
          <Field
            label="App password"
            hint={
              <>
                Generate at <a href="https://bsky.app/settings/app-passwords" target="_blank" rel="noreferrer">bsky.app → Settings → App passwords</a>. Format: <code>xxxx-xxxx-xxxx-xxxx</code>.
              </>
            }
          >
            <input
              className="fp-input"
              type="password"
              value={appPw}
              onChange={(e) => setAppPw(e.target.value)}
              placeholder="xxxx-xxxx-xxxx-xxxx"
              autoComplete="off"
            />
          </Field>
          {error && <div style={{ color: "var(--danger)", fontSize: 13 }}>{error}</div>}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button
              type="submit"
              className="fp-btn"
              disabled={connect.isPending || !handle.trim() || !appPw.trim()}
            >
              {connect.isPending && <span className="fp-spinner" />}
              {connect.isPending ? "Connecting" : "Connect Bluesky"}
            </button>
          </div>
        </form>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <DefaultTargetToggle
            value={data?.default_target ?? true}
            onToggle={(v) => toggleDefault.mutate(v)}
            label="Auto-post new scheduled photos to Bluesky"
          />

          <Field
            label="Default hashtags"
            hint={
              <>
                Always added to every Bluesky post. Space-separated, no <code>#</code> needed.
                Post-specific tags are appended after these (deduplicated, fits up to 300 chars).
              </>
            }
          >
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="fp-input"
                value={defaultHashtags}
                onChange={(e) => setDefaultHashtags(e.target.value)}
                placeholder="photography burlesque"
                style={{ flex: 1 }}
              />
              <button
                className="fp-btn"
                disabled={!hashtagsDirty || saveHashtags.isPending}
                onClick={() => saveHashtags.mutate()}
                style={{ padding: "8px 14px", fontSize: 13 }}
              >
                {saveHashtags.isPending && <span className="fp-spinner" />}
                {saveHashtags.isPending ? "Saving" : "Save"}
              </button>
            </div>
            {hashtagsSaved === "saved" && !hashtagsDirty && (
              <span style={{ fontSize: 11, color: "var(--teal)" }}>Saved.</span>
            )}
            {hashtagsError && (
              <span style={{ fontSize: 11, color: "var(--danger)" }}>{hashtagsError}</span>
            )}
            {savedDefaults && !hashtagsDirty && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                {savedDefaults.split(/\s+/).filter(Boolean).map((t) => (
                  <span
                    key={t}
                    style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 999,
                      background: "var(--teal-tint)",
                      color: "var(--teal)",
                      border: "0.5px solid rgba(93,202,165,0.2)",
                    }}
                  >
                    #{t}
                  </span>
                ))}
              </div>
            )}
          </Field>

          {testResult && (
            <div
              style={{
                fontSize: 12,
                padding: "8px 12px",
                borderRadius: 8,
                background: testResult.kind === "ok" ? "var(--teal-tint)" : "var(--danger-tint)",
                color: testResult.kind === "ok" ? "var(--teal)" : "var(--danger)",
              }}
            >
              {testResult.text}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button
              className="fp-btn-ghost"
              onClick={() => test.mutate()}
              disabled={test.isPending}
            >
              {test.isPending ? "Testing" : "Test connection"}
            </button>
            <button
              className="fp-btn-danger"
              onClick={() => {
                if (confirm("Disconnect Bluesky? FramePost will stop auto-posting to Bluesky for new scheduled photos.")) {
                  disconnect.mutate();
                }
              }}
              disabled={disconnect.isPending}
            >
              Disconnect
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pixelfed
// ---------------------------------------------------------------------------
function PixelfedPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["pixelfed-status"],
    queryFn: fetchPixelfedStatus,
  });

  const [instance, setInstance] = useState("https://pixelfed.social");

  const disconnect = useMutation({
    mutationFn: () => disconnectPixelfed(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pixelfed-status"] }),
  });

  const toggleDefault = useMutation({
    mutationFn: (next: boolean) => setPlatformDefaultTarget("pixelfed", next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pixelfed-status"] }),
  });

  if (isLoading) return <div className="fp-card"><SkeletonRows count={3} /></div>;
  const connected = data?.connected ?? false;
  const pending = data?.pending ?? false;

  return (
    <div className="fp-card">
      <CardHeader
        title="Pixelfed"
        subtitle={connected
          ? <PixelfedSubtitle status={data!} />
          : pending
            ? "OAuth in progress — finish the authorization on Pixelfed to complete the connection."
            : "Federates to Mastodon, so this also reaches Mastodon followers. OAuth-based — no manual tokens."}
      />

      {!connected ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!instance.trim()) return;
            // Top-level navigation — needs a real GET, not fetch (cookies + redirect).
            window.location.href = pixelfedConnectUrl(instance.trim());
          }}
          style={{ display: "grid", gap: 10 }}
        >
          <Field label="Instance URL" hint="The Pixelfed instance you signed up on.">
            <input
              className="fp-input"
              value={instance}
              onChange={(e) => setInstance(e.target.value)}
              placeholder="https://pixelfed.social"
            />
          </Field>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button type="submit" className="fp-btn" disabled={!instance.trim()}>
              Connect Pixelfed
            </button>
          </div>
        </form>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <DefaultTargetToggle
            value={data?.default_target ?? true}
            onToggle={(v) => toggleDefault.mutate(v)}
            label="Auto-post new scheduled photos to Pixelfed"
          />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            {data?.profile_url && (
              <a
                href={data.profile_url}
                target="_blank"
                rel="noreferrer"
                className="fp-btn-ghost"
                style={{ padding: "7px 12px", fontSize: 13 }}
              >
                View profile ↗
              </a>
            )}
            <button
              className="fp-btn-danger"
              onClick={() => {
                if (confirm("Disconnect Pixelfed?")) disconnect.mutate();
              }}
              disabled={disconnect.isPending}
            >
              Disconnect
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reddit — copy-paste only (no connection); manage saved subreddit list.
// ---------------------------------------------------------------------------
function RedditPanel() {
  const qc = useQueryClient();
  const { data: cfg } = useQuery({ queryKey: ["config"], queryFn: fetchAppConfig });
  const [subs, setSubs] = useState("");
  const [saved, setSaved] = useState<"idle" | "saved" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cfg && cfg.reddit_subreddits !== undefined) {
      setSubs(cfg.reddit_subreddits ?? "");
    }
  }, [cfg?.reddit_subreddits]);

  const stored = cfg?.reddit_subreddits ?? "";
  const dirty = subs.trim() !== stored.trim();

  const save = useMutation({
    mutationFn: () => patchAppConfig({ reddit_subreddits: subs.trim() }),
    onMutate: () => {
      setSaved("idle");
      setError(null);
    },
    onSuccess: () => {
      setSaved("saved");
      void qc.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (e) => {
      setSaved("error");
      const detail = e instanceof ApiError
        ? (e.payload as { detail?: { validation?: Record<string, string> } })?.detail?.validation
        : null;
      setError(detail?.reddit_subreddits ?? (e instanceof Error ? e.message : "Save failed"));
    },
  });

  const tokens = stored.split(/\s+/).filter(Boolean);

  return (
    <div className="fp-card">
      <CardHeader
        title="Reddit"
        subtitle="Copy-paste assist — no API connection. Configure the subreddits you typically post to; the Published modal's Reddit tab opens each one's submit page with your title pre-filled."
      />
      <div style={{ display: "grid", gap: 10 }}>
        <Field
          label="Subreddits"
          hint={
            <>
              Space- or comma-separated. Don't include <code>r/</code>. Names must be 3–21 chars,
              letters/numbers/underscore.
            </>
          }
        >
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="fp-input"
              value={subs}
              onChange={(e) => setSubs(e.target.value)}
              placeholder="Burlesque itookapicture NewOrleans circus"
              style={{ flex: 1 }}
            />
            <button
              className="fp-btn"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
              style={{ padding: "8px 14px", fontSize: 13 }}
            >
              {save.isPending && <span className="fp-spinner" />}
              {save.isPending ? "Saving" : "Save"}
            </button>
          </div>
          {saved === "saved" && !dirty && (
            <span style={{ fontSize: 11, color: "var(--teal)" }}>Saved.</span>
          )}
          {error && <span style={{ fontSize: 11, color: "var(--danger)" }}>{error}</span>}
          {tokens.length > 0 && !dirty && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
              {tokens.map((name) => (
                <span
                  key={name}
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 999,
                    background: "rgba(255, 138, 101, 0.1)",
                    color: "#ff8a65",
                    border: "0.5px solid rgba(255, 138, 101, 0.25)",
                  }}
                >
                  r/{name}
                </span>
              ))}
            </div>
          )}
        </Field>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
function ConnectedSubtitle({ status }: { status: BlueskyStatus }) {
  return (
    <span>
      Connected as <strong>@{status.handle}</strong>
      {status.last_success_at && (
        <>
          {" · "}
          <span title={absoluteTime(status.last_success_at)}>
            last success {relativeTime(status.last_success_at)}
          </span>
        </>
      )}
    </span>
  );
}

function PixelfedSubtitle({ status }: { status: PixelfedStatus }) {
  return (
    <span>
      Connected as <strong>@{status.account}</strong> on {status.instance_url}
      {status.last_success_at && (
        <>
          {" · "}
          <span title={absoluteTime(status.last_success_at)}>
            last success {relativeTime(status.last_success_at)}
          </span>
        </>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Pinterest — API v5 OAuth 2.0, requires app keys in .env first.
// ---------------------------------------------------------------------------
function PinterestPanel() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["pinterest-status"],
    queryFn: fetchPinterestStatus,
  });

  const disconnect = useMutation({
    mutationFn: () => disconnectPinterest(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pinterest-status"] }),
  });

  const toggleDefault = useMutation({
    mutationFn: (next: boolean) => setPlatformDefaultTarget("pinterest", next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pinterest-status"] }),
  });

  if (isLoading) return <div className="fp-card"><SkeletonRows count={3} /></div>;
  const connected = data?.connected ?? false;
  const pending = data?.pending ?? false;

  return (
    <div className="fp-card">
      <CardHeader
        title="Pinterest"
        subtitle={connected
          ? <PinterestSubtitle status={data!} />
          : pending
            ? "OAuth in progress — finish the authorization on Pinterest to complete the connection."
            : "Pins drive perpetual referral traffic — each pin links back to the photo on Flickr. Requires PINTEREST_APP_ID + PINTEREST_APP_SECRET in .env (register at developers.pinterest.com)."}
      />

      {!connected ? (
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <a
            href={pinterestConnectUrl()}
            className="fp-btn"
            style={{ textDecoration: "none" }}
          >
            Connect Pinterest
          </a>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <PinterestBoardPicker
            currentBoardId={data?.default_board_id ?? null}
            currentBoardName={data?.default_board_name ?? null}
            onSaved={() => qc.invalidateQueries({ queryKey: ["pinterest-status"] })}
          />
          <DefaultTargetToggle
            value={data?.default_target ?? true}
            onToggle={(v) => toggleDefault.mutate(v)}
            label="Auto-post new scheduled photos to Pinterest"
          />
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            {data?.profile_url && (
              <a
                href={data.profile_url}
                target="_blank"
                rel="noreferrer"
                className="fp-btn-ghost"
                style={{ padding: "7px 12px", fontSize: 13 }}
              >
                View profile ↗
              </a>
            )}
            <button
              className="fp-btn-danger"
              onClick={() => {
                if (confirm("Disconnect Pinterest?")) disconnect.mutate();
              }}
              disabled={disconnect.isPending}
            >
              Disconnect
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function PinterestSubtitle({ status }: { status: PinterestStatus }) {
  return (
    <span>
      Connected as <strong>@{status.account}</strong>
      {status.default_board_name && <> · pinning to <strong>{status.default_board_name}</strong></>}
      {status.last_success_at && (
        <>
          {" · "}
          <span title={absoluteTime(status.last_success_at)}>
            last success {relativeTime(status.last_success_at)}
          </span>
        </>
      )}
    </span>
  );
}

function PinterestBoardPicker({
  currentBoardId,
  currentBoardName,
  onSaved,
}: {
  currentBoardId: string | null;
  currentBoardName: string | null;
  onSaved: () => void;
}) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["pinterest-boards"],
    queryFn: fetchPinterestBoards,
  });
  const [picked, setPicked] = useState<string>(currentBoardId ?? "");
  useEffect(() => {
    setPicked(currentBoardId ?? "");
  }, [currentBoardId]);

  const save = useMutation({
    mutationFn: () => {
      const board = data?.boards.find((b) => b.id === picked);
      return setPinterestDefaultBoard(picked, board?.name ?? "");
    },
    onSuccess: () => onSaved(),
  });

  const boards = data?.boards ?? [];
  const dirty = picked !== (currentBoardId ?? "");

  return (
    <Field
      label="Default board"
      hint={
        currentBoardName
          ? `All pins go to this board. Per-post board picking is v2.`
          : "All pins from FramePost will be created on the board you pick here."
      }
    >
      {isLoading ? (
        <SkeletonRows count={1} height={32} />
      ) : error ? (
        <div style={{ fontSize: 12, color: "var(--danger)" }}>
          Couldn't load boards: {(error as Error).message}{" "}
          <button
            className="fp-btn-ghost"
            onClick={() => refetch()}
            style={{ padding: "3px 8px", fontSize: 11 }}
          >
            Retry
          </button>
        </div>
      ) : boards.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
          No boards found on your Pinterest account. Create one at{" "}
          <a href="https://www.pinterest.com/" target="_blank" rel="noreferrer">
            pinterest.com
          </a>{" "}
          first, then click Retry.{" "}
          <button
            className="fp-btn-ghost"
            onClick={() => refetch()}
            style={{ padding: "3px 8px", fontSize: 11 }}
          >
            Retry
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select
            className="fp-input"
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            style={{ flex: 1 }}
          >
            <option value="" disabled>— pick a board —</option>
            {boards.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
                {b.privacy && b.privacy !== "PUBLIC" ? ` (${b.privacy.toLowerCase()})` : ""}
                {typeof b.pin_count === "number" ? ` · ${b.pin_count} pins` : ""}
              </option>
            ))}
          </select>
          <button
            className="fp-btn"
            disabled={!dirty || !picked || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </Field>
  );
}

function Field({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <label style={{ display: "grid", gap: 4 }}>
      <span style={{ fontSize: 12, color: "var(--text-dim)", fontWeight: 500 }}>{label}</span>
      {children}
      {hint && <span style={{ fontSize: 11, color: "var(--text-fade)" }}>{hint}</span>}
    </label>
  );
}

function DefaultTargetToggle({
  value,
  onToggle,
  label,
}: {
  value: boolean;
  onToggle: (v: boolean) => void;
  label: string;
}) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        fontSize: 13,
        color: "var(--text)",
        cursor: "pointer",
      }}
    >
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onToggle(e.target.checked)}
        style={{ width: 16, height: 16, cursor: "pointer" }}
      />
      {label}
    </label>
  );
}
