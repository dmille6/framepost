// Thin fetch wrapper. Reads the CSRF cookie and echoes it on state-changing calls.

const BASE = ""; // same-origin: nginx proxies /api and /health.

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public payload: unknown = null,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  // Don't force JSON content-type on FormData uploads — let the browser set the boundary.
  if (
    init.body &&
    !headers.has("Content-Type") &&
    !(init.body instanceof FormData)
  ) {
    headers.set("Content-Type", "application/json");
  }
  if (UNSAFE.has(method)) {
    const csrf = readCookie("framepost_csrf");
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  const res = await fetch(`${BASE}${path}`, {
    credentials: "same-origin",
    ...init,
    method,
    headers,
  });
  if (!res.ok) {
    let payload: unknown = null;
    let message = res.statusText;
    try {
      payload = await res.json();
      const detail = (payload as { detail?: unknown })?.detail;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail === "object" && "message" in detail) {
        message = String((detail as { message: unknown }).message);
      }
    } catch { /* not JSON */ }
    throw new ApiError(res.status, message, payload);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export type HealthPayload = {
  status: "ok" | "degraded" | "down";
  worker_alive: boolean;
  db_writable: boolean;
  photo_volume_writable: boolean;
  photo_volume_free_gb: number;
  flickr_last_success: string | null;
  last_backup: string | null;
  version: string;
};

export const fetchHealth = () => apiFetch<HealthPayload>("/health");

export type Me = { id: number; username: string };

export const fetchMe = () => apiFetch<Me>("/api/auth/me");

export const login = (username: string, password: string) =>
  apiFetch<Me>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });

export const logout = () => apiFetch<{ ok: true }>("/api/auth/logout", { method: "POST" });

export type Post = {
  id: string;
  title: string | null;
  description: string | null;
  tags: string | null;
  original_filename: string | null;
  file_size_bytes: number | null;
  width: number | null;
  height: number | null;
  captured_at: string | null;
  camera_make: string | null;
  camera_model: string | null;
  lens: string | null;
  focal_length: number | null;
  iso: number | null;
  shutter_speed: string | null;
  aperture: number | null;
  sha256: string | null;
  privacy: string | null;
  safety_level: string | null;
  content_type: string | null;
  status: string;
  posted_to_instagram_at: string | null;
  reddit_posted_at: string | null;
  target_platforms: string[] | null;
  // Structured context (migration 0014). venue_id references venues.id; show/city are
  // free text typeahead'd from past values; alt_text is AI-generated.
  venue_id: string | null;
  show: string | null;
  city: string | null;
  alt_text: string | null;
  created_at: string;
};

export type UploadResponse = { post: Post; duplicate_of: string | null };

export type DuplicateDetail = {
  message: string;
  duplicate_of: string;
  existing_title: string | null;
  existing_filename: string | null;
};

export const listDrafts = () => apiFetch<Post[]>("/api/posts");

export const getPost = (id: string) => apiFetch<Post>(`/api/posts/${id}`);

export function uploadFile(file: File, allowDuplicate = false) {
  const fd = new FormData();
  fd.append("file", file);
  const path = `/api/posts/upload${allowDuplicate ? "?allow_duplicate=true" : ""}`;
  return apiFetch<UploadResponse>(path, { method: "POST", body: fd });
}

export type UploadStage = "uploading" | "processing" | "done";

/** Upload with byte-level progress + stage tracking. Returns the same UploadResponse but
 *  `onProgress` is called throughout: while bytes stream out (`uploading` 0→1) and once the
 *  upload completes (`processing` while waiting on server-side pipeline). */
export function uploadFileWithProgress(
  file: File,
  options: {
    allowDuplicate?: boolean;
    onProgress: (stage: UploadStage, fraction: number) => void;
  },
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const path = `/api/posts/upload${options.allowDuplicate ? "?allow_duplicate=true" : ""}`;
    xhr.open("POST", path, true);
    xhr.responseType = "text";

    const csrf = readCookie("framepost_csrf");
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        options.onProgress("uploading", e.loaded / e.total);
      }
    };
    xhr.upload.onload = () => {
      // All bytes sent; server is now processing. We don't get further progress events
      // from the server-side pipeline, so just flip the stage.
      options.onProgress("processing", 1);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const parsed = xhr.responseText ? JSON.parse(xhr.responseText) : {};
          options.onProgress("done", 1);
          resolve(parsed);
        } catch (e) {
          reject(new ApiError(xhr.status, "couldn't parse response", null));
        }
        return;
      }
      let payload: unknown = null;
      let message = xhr.statusText;
      try {
        payload = JSON.parse(xhr.responseText);
        const detail = (payload as { detail?: unknown })?.detail;
        if (typeof detail === "string") message = detail;
        else if (detail && typeof detail === "object" && "message" in detail) {
          message = String((detail as { message: unknown }).message);
        }
      } catch { /* not JSON */ }
      reject(new ApiError(xhr.status, message, payload));
    };
    xhr.onerror = () => reject(new ApiError(0, "network error", null));
    xhr.onabort = () => reject(new ApiError(0, "upload aborted", null));

    const fd = new FormData();
    fd.append("file", file);
    xhr.send(fd);
  });
}

export type PostUpdate = Partial<{
  title: string | null;
  description: string | null;
  tags: string | null;
  privacy: string;
  safety_level: string;
  content_type: string;
  target_platforms: string[] | null;
  venue_id: string | null;
  show: string | null;
  city: string | null;
  alt_text: string | null;
}>;

export const updatePost = (id: string, body: PostUpdate) =>
  apiFetch<Post>(`/api/posts/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const deletePost = (id: string) =>
  apiFetch<{ ok: true; post_id: string; was_on_flickr: boolean }>(
    `/api/posts/${id}`,
    { method: "DELETE" },
  );

export const thumbnailUrl = (id: string) => `/api/posts/${id}/thumbnail`;
export const previewUrl = (id: string) => `/api/posts/${id}/preview`;

export type InstagramFormat = {
  caption: string;
  hashtags: string[];
  title: string | null;
  description: string | null;
  alt_text: string | null;
  signature: string | null;
  posted_to_instagram_at: string | null;
  sizes: string[];
};

export type FaceCenter = {
  x: number | null;
  y: number | null;
  detected: boolean;
};

export const fetchFaceCenter = (postId: string) =>
  apiFetch<FaceCenter>(`/api/posts/${postId}/face-center`);

export const fetchInstagramFormat = (
  postId: string,
  opts: { extraPerformerPostIds?: string[] } = {},
) => {
  const extras = (opts.extraPerformerPostIds ?? []).filter((id) => id && id !== postId);
  const qs = extras.length
    ? `?extra_performer_post_ids=${encodeURIComponent(extras.join(","))}`
    : "";
  return apiFetch<InstagramFormat>(`/api/posts/${postId}/instagram${qs}`);
};

export const instagramImageUrl = (
  postId: string,
  fmt: "square" | "portrait",
  fit: "pad" | "crop",
  bg: "black" | "white",
) => `/api/posts/${postId}/instagram-image?fmt=${fmt}&fit=${fit}&bg=${bg}`;

export const markInstagramPosted = (postId: string, posted: boolean) =>
  apiFetch<Post>(`/api/posts/${postId}/instagram`, {
    method: "PATCH",
    body: JSON.stringify({ posted }),
  });

// ---- Instagram manual engagement tracking ----
export type IGComment = {
  id: number;
  author_handle: string | null;
  body: string;
  posted_at: string | null;
  fetched_at: string;
};

export type IGEngagement = {
  likes_count: number;
  comments_count: number;
  last_updated_at: string | null;
  comments: IGComment[];
};

export const fetchIGEngagement = (postId: string) =>
  apiFetch<IGEngagement>(`/api/posts/${postId}/instagram/engagement`);

export const setIGLikes = (postId: string, count: number) =>
  apiFetch<{ ok: true; likes_count: number }>(
    `/api/posts/${postId}/instagram/likes`,
    { method: "PUT", body: JSON.stringify({ count }) },
  );

export const addIGComment = (postId: string, author_handle: string, body: string) =>
  apiFetch<IGComment & { ok: true }>(
    `/api/posts/${postId}/instagram/comments`,
    {
      method: "POST",
      body: JSON.stringify({ author_handle, body }),
    },
  );

export const deleteIGComment = (postId: string, commentId: number) =>
  apiFetch<{ ok: true; removed: number }>(
    `/api/posts/${postId}/instagram/comments/${commentId}`,
    { method: "DELETE" },
  );

export type RepostFlickrResponse = {
  post: Post;
  flickr_deleted: boolean;
  flickr_delete_error: string | null;
};

export const repostToFlickr = (postId: string) =>
  apiFetch<RepostFlickrResponse>(`/api/posts/${postId}/repost-flickr`, {
    method: "POST",
  });

// ---- Reddit copy-paste assist ----
export type RedditSubredditShortcut = {
  name: string;
  submit_url: string;
  submit_url_with_oc: string;
};

export type RedditFormat = {
  title_clean: string;
  title_with_oc: string;
  subreddits: RedditSubredditShortcut[];
  reddit_posted_at: string | null;
  image_path: string;
};

export const fetchRedditFormat = (postId: string) =>
  apiFetch<RedditFormat>(`/api/posts/${postId}/reddit`);

export const markRedditPosted = (postId: string, posted: boolean) =>
  apiFetch<Post>(`/api/posts/${postId}/reddit`, {
    method: "PATCH",
    body: JSON.stringify({ posted }),
  });

export const fullImageUrl = (postId: string) => `/api/posts/${postId}/full-image`;
export const redditImageUrl = (postId: string) => `/api/posts/${postId}/reddit-image`;

export type ScheduledItem = {
  id: string;
  title: string | null;
  description: string | null;
  original_filename: string | null;
  width: number | null;
  height: number | null;
  scheduled_at: string | null;
  status: string;
  posted_at: string | null;
  error_message: string | null;
};

export const listScheduled = (fromIso?: string, toIso?: string) => {
  const qs = new URLSearchParams();
  if (fromIso) qs.set("from", fromIso);
  if (toIso) qs.set("to", toIso);
  const path = `/api/schedule${qs.size ? `?${qs.toString()}` : ""}`;
  return apiFetch<ScheduledItem[]>(path);
};

export const schedulePost = (postId: string, scheduledAtIso: string) =>
  apiFetch<Post>("/api/schedule", {
    method: "POST",
    body: JSON.stringify({ post_id: postId, scheduled_at: scheduledAtIso }),
  });

export const unschedulePost = (postId: string) =>
  apiFetch<{ ok: true }>(`/api/schedule/${postId}`, { method: "DELETE" });

export const postNow = (postId: string) =>
  apiFetch<{ ok: true; scheduled_at: string }>(`/api/schedule/${postId}/post-now`, {
    method: "POST",
  });

export type SmartFillSlot = {
  post_id: string;
  title: string | null;
  original_filename: string | null;
  scheduled_at: string | null;
  skipped_reason: string | null;
};

export type SmartFillResponse = {
  slots: SmartFillSlot[];
  scheduled: number;
  skipped: number;
  confirmed: boolean;
};

export type SmartFillRequest = {
  post_ids: string[];
  time_of_day: string;     // HH:MM
  cadence_days: number;
  start_date: string;      // YYYY-MM-DD
  skip_weekends: boolean;
  confirm: boolean;
  mode?: "sequential" | "random_scatter";
};

export const smartFill = (body: SmartFillRequest) =>
  apiFetch<SmartFillResponse>("/api/schedule/smart-fill", {
    method: "POST",
    body: JSON.stringify(body),
  });

export type WatchConfig = {
  enabled: boolean;
  path: string;
  status: string;
  last_imported_at: string | null;
  last_error: string | null;
  error_count: number;
};

export const fetchWatchConfig = () => apiFetch<WatchConfig>("/api/config/watch");

export const updateWatchConfig = (body: Partial<{ enabled: boolean; path: string }>) =>
  apiFetch<WatchConfig>("/api/config/watch", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export type FlickrStatus = {
  connected: boolean;
  account_name: string | null;
  connected_at: string | null;
  key_version: number | null;
};

export const fetchFlickrStatus = () => apiFetch<FlickrStatus>("/api/platforms/flickr/status");

export const disconnectFlickr = () =>
  apiFetch<{ ok: true }>("/api/platforms/flickr/disconnect", { method: "POST" });

// Connect is a top-level browser navigation, not a fetch — OAuth needs to redirect
// the user's window to flickr.com.
export const flickrConnectUrl = "/api/platforms/flickr/connect";

export type HistoryPost = {
  id: string;
  title: string | null;
  description: string | null;
  tags: string | null;
  original_filename: string | null;
  width: number | null;
  height: number | null;
  captured_at: string | null;
  camera_make: string | null;
  camera_model: string | null;
  lens: string | null;
  iso: number | null;
  shutter_speed: string | null;
  aperture: number | null;
  status: string;
  scheduled_at: string | null;
  posted_at: string | null;
  flickr_photo_id: string | null;
  flickr_url: string | null;
  error_message: string | null;
  retry_count: number;
  posted_to_instagram_at: string | null;
  reddit_posted_at: string | null;
};

export type TimelineEvent = {
  id: number;
  event_type: string;
  actor: string;
  details: Record<string, unknown> | null;
  created_at: string;
};

export const listHistory = (q?: string, statuses?: string[]) => {
  const qs = new URLSearchParams();
  if (q) qs.set("q", q);
  for (const s of statuses ?? []) qs.append("status", s);
  const path = `/api/published${qs.size ? `?${qs.toString()}` : ""}`;
  return apiFetch<HistoryPost[]>(path);
};

export const fetchPostEvents = (id: string) =>
  apiFetch<TimelineEvent[]>(`/api/published/${id}/events`);

export type PostPlatformStatus = {
  platform: string;
  account_name: string | null;
  instance_url: string | null;
  status: string;
  remote_id: string | null;
  remote_url: string | null;
  posted_at: string | null;
  error_message: string | null;
  retry_count: number;
};

export const fetchPostPlatforms = (id: string) =>
  apiFetch<PostPlatformStatus[]>(`/api/published/${id}/platforms`);

// ---- Bluesky ----
export type BlueskyStatus = {
  connected: boolean;
  handle: string | null;
  instance_url?: string;
  connected_at?: string | null;
  last_success_at?: string | null;
  last_error?: string | null;
  default_target?: boolean;
};

export const fetchBlueskyStatus = () =>
  apiFetch<BlueskyStatus>("/api/platforms/bluesky/status");

export const connectBluesky = (handle: string, app_password: string) =>
  apiFetch<{ ok: true; handle: string; connected_at: string | null }>(
    "/api/platforms/bluesky/connect",
    { method: "POST", body: JSON.stringify({ handle, app_password }) },
  );

export const disconnectBluesky = () =>
  apiFetch<{ ok: true; removed: boolean }>("/api/platforms/bluesky/disconnect", { method: "POST" });

export const testBluesky = () =>
  apiFetch<{ ok: true; handle: string; display_name: string | null; followers: number }>(
    "/api/platforms/bluesky/test",
    { method: "POST", body: JSON.stringify({}) },
  );

// ---- Pixelfed ----
export type PixelfedStatus = {
  connected: boolean;
  pending?: boolean;
  account: string | null;
  instance_url: string | null;
  profile_url?: string | null;
  connected_at?: string | null;
  last_success_at?: string | null;
  last_error?: string | null;
  default_target?: boolean;
};

export const fetchPixelfedStatus = () =>
  apiFetch<PixelfedStatus>("/api/platforms/pixelfed/status");

export const pixelfedConnectUrl = (instance: string) =>
  `/api/platforms/pixelfed/connect?instance=${encodeURIComponent(instance)}`;

export const disconnectPixelfed = () =>
  apiFetch<{ ok: true; removed: boolean }>("/api/platforms/pixelfed/disconnect", { method: "POST" });

// ---- Pinterest ----
export type PinterestStatus = {
  connected: boolean;
  pending?: boolean;
  account: string | null;
  profile_url?: string | null;
  default_board_id?: string | null;
  default_board_name?: string | null;
  connected_at?: string | null;
  last_success_at?: string | null;
  last_error?: string | null;
  default_target?: boolean;
  token_expires?: string | null;
};

export type PinterestBoard = {
  id: string;
  name: string;
  privacy?: string | null;
  pin_count?: number | null;
};

export const fetchPinterestStatus = () =>
  apiFetch<PinterestStatus>("/api/platforms/pinterest/status");

export const pinterestConnectUrl = () => `/api/platforms/pinterest/connect`;

export const disconnectPinterest = () =>
  apiFetch<{ ok: true; removed: boolean }>("/api/platforms/pinterest/disconnect", { method: "POST" });

export const fetchPinterestBoards = () =>
  apiFetch<{ boards: PinterestBoard[] }>("/api/platforms/pinterest/boards");

export const setPinterestDefaultBoard = (board_id: string, board_name: string) =>
  apiFetch<{ ok: true }>(
    "/api/platforms/pinterest/default-board",
    { method: "PUT", body: JSON.stringify({ board_id, board_name }) },
  );

// ---- shared default-target toggle ----
export const setPlatformDefaultTarget = (platform: string, default_target: boolean) =>
  apiFetch<{ ok: true; default_target: boolean }>(
    `/api/platforms/${platform}/default-target`,
    { method: "PATCH", body: JSON.stringify({ default_target }) },
  );

// ---- Activity feed (cross-platform comments + likes) ----
export type CommentActivityItem = {
  kind: "comment" | "like";
  id: number;
  post_id: string;
  post_title: string | null;
  platform: string;
  author_handle: string | null;
  author_display_name: string | null;
  author_url: string | null;
  body: string;
  posted_at: string | null;
  fetched_at: string;
  seen_at: string | null;
};

export const fetchCommentActivity = (only_unread = false, limit = 100, offset = 0) => {
  const qs = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (only_unread) qs.set("only_unread", "true");
  return apiFetch<CommentActivityItem[]>(`/api/activity?${qs.toString()}`);
};

export type PlatformBreakdown = {
  likes: number;
  comments: number;
  unread: number;
};

export type PostActivitySummary = {
  post_id: string;
  post_title: string | null;
  flickr_url: string | null;
  posted_at: string | null;
  newest_activity_at: string | null;
  total_likes: number;
  total_comments: number;
  unread: number;
  platforms: Record<string, PlatformBreakdown>;
};

export const fetchActivityByPost = (limit = 100) =>
  apiFetch<PostActivitySummary[]>(`/api/activity/by-post?limit=${limit}`);

export const fetchActivityUnreadCount = () =>
  apiFetch<{ unread: number }>("/api/activity/unread-count");

export const markAllActivitySeen = () =>
  apiFetch<{ marked: number }>("/api/activity/mark-all-seen", { method: "POST" });

export const syncActivityNow = () =>
  apiFetch<Record<string, { sampled: number; comments_new: number; errors: number }>>(
    "/api/activity/sync-now",
    { method: "POST" },
  );

export type PostComment = {
  id: number;
  platform: string;
  author_handle: string | null;
  author_display_name: string | null;
  author_url: string | null;
  body: string;
  posted_at: string | null;
  fetched_at: string;
  seen_at: string | null;
};

export const fetchPostComments = (postId: string) =>
  apiFetch<PostComment[]>(`/api/published/${postId}/comments`);


// ---- Title templates ----
export type TemplateField = {
  key: string;
  label: string;
  placeholder?: string | null;
};

export type TitleTemplate = {
  id: string;
  name: string;
  title_template: string;
  description_template: string | null;
  fields: TemplateField[];
  sort_order: number;
};

export type TitleTemplateInput = {
  name: string;
  title_template: string;
  description_template: string | null;
  fields: TemplateField[];
  sort_order: number;
};

export const listTitleTemplates = () =>
  apiFetch<TitleTemplate[]>("/api/title-templates");

export const createTitleTemplate = (body: TitleTemplateInput) =>
  apiFetch<TitleTemplate>("/api/title-templates", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateTitleTemplate = (id: string, body: TitleTemplateInput) =>
  apiFetch<TitleTemplate>(`/api/title-templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const deleteTitleTemplate = (id: string) =>
  apiFetch<{ ok: true }>(`/api/title-templates/${id}`, { method: "DELETE" });


// ---- unified connected-platforms list (for per-post targeting UI) ----
export type ConnectedPlatform = {
  platform: string;
  label: string;
  account_name: string | null;
  instance_url?: string;
  default_target: boolean;
};

export const listConnectedPlatforms = () =>
  apiFetch<ConnectedPlatform[]>("/api/platforms");

export type Album = {
  id: string;
  flickr_album_id: string | null;
  name: string;
  description: string | null;
  photo_count: number;
  last_synced_at: string | null;
};

export const listAlbums = () => apiFetch<Album[]>("/api/albums");
export const triggerAlbumSync = () =>
  apiFetch<{ synced: number }>("/api/albums/sync", { method: "POST" });
export const getPostAlbums = (postId: string) =>
  apiFetch<string[]>(`/api/albums/post/${postId}`);
export const setPostAlbums = (postId: string, albumIds: string[]) =>
  apiFetch<string[]>(`/api/albums/post/${postId}`, {
    method: "PUT",
    body: JSON.stringify({ album_ids: albumIds }),
  });

export type Group = {
  id: string;
  flickr_group_id: string | null;
  name: string;
  category: string | null;
  daily_limit: number | null;
  content_notes: string | null;
  no_watermark: boolean;
  default_enabled: boolean;
};

export type GroupInput = Omit<Group, "id">;

export const listGroups = () => apiFetch<Group[]>("/api/groups");
export const createGroup = (body: GroupInput) =>
  apiFetch<Group>("/api/groups", { method: "POST", body: JSON.stringify(body) });
export const updateGroup = (id: string, body: GroupInput) =>
  apiFetch<Group>(`/api/groups/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteGroup = (id: string) =>
  apiFetch<{ ok: true }>(`/api/groups/${id}`, { method: "DELETE" });
export const getPostGroups = (postId: string) =>
  apiFetch<string[]>(`/api/groups/post/${postId}`);
export const setPostGroups = (postId: string, groupIds: string[]) =>
  apiFetch<string[]>(`/api/groups/post/${postId}`, {
    method: "PUT",
    body: JSON.stringify({ group_ids: groupIds }),
  });

export type AppConfigMap = Record<string, string | null>;

export const fetchAppConfig = () => apiFetch<AppConfigMap>("/api/config");

export const patchAppConfig = (changes: Record<string, string | number>) =>
  apiFetch<AppConfigMap>("/api/config", {
    method: "PATCH",
    body: JSON.stringify(changes),
  });

export type DiskUsage = {
  photo_root: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  used_percent: number;
  warning_percent: number;
  hardstop_gb: number;
};

export const fetchDiskUsage = () => apiFetch<DiskUsage>("/api/system/disk");

export type DiskSamplePoint = {
  sampled_at: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
};

export const fetchDiskHistory = (hours: number) =>
  apiFetch<DiskSamplePoint[]>(`/api/system/disk-history?hours=${hours}`);

export type ActivityRow = {
  id: number;
  post_id: string;
  post_title: string | null;
  post_filename: string | null;
  event_type: string;
  actor: string;
  details: Record<string, unknown> | null;
  created_at: string;
};

export const fetchActivity = (params?: { limit?: number; offset?: number; event_type?: string; actor?: string }) => {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  if (params?.event_type) qs.set("event_type", params.event_type);
  if (params?.actor) qs.set("actor", params.actor);
  const path = `/api/system/activity${qs.size ? `?${qs.toString()}` : ""}`;
  return apiFetch<ActivityRow[]>(path);
};

export type DayPoint = { day: string; imported: number; posted: number; failed: number };
export type MetricsResponse = {
  window_days: number;
  daily: DayPoint[];
  totals: { imported: number; posted: number; failed: number };
  counts_now: Record<string, number>;
  retry_rate: number;
  success_rate: number;
  avg_upload_seconds: number | null;
};

export const fetchMetrics = (days: number) =>
  apiFetch<MetricsResponse>(`/api/system/metrics?days=${days}`);

export type AnalyticsOverview = {
  posts_with_engagement: number;
  total_views: number;
  total_faves: number;
  total_comments: number;
  last_sync: string | null;
};
export type TimeSlot = {
  dow: number;
  hour: number;
  posts: number;
  avg_views: number;
  avg_faves: number;
  avg_comments: number;
};
export type GroupStat = {
  group_id: string;
  name: string;
  category: string | null;
  submissions: number;
  accepted: number;
  failed: number;
  avg_views: number;
  avg_faves: number;
  avg_comments: number;
};
export type TagStat = {
  tag: string;
  posts: number;
  avg_views: number;
  avg_faves: number;
  avg_comments: number;
};
export type TopPost = {
  post_id: string;
  title: string | null;
  flickr_url: string | null;
  posted_at: string | null;
  views: number;
  faves: number;
  comments: number;
};

export const fetchAnalyticsOverview = () =>
  apiFetch<AnalyticsOverview>("/api/analytics/overview");
export const fetchBestTimes = () => apiFetch<TimeSlot[]>("/api/analytics/best-times");
export const fetchGroupStats = () => apiFetch<GroupStat[]>("/api/analytics/groups");
export const fetchTagStats = (minPosts = 2, limit = 40) =>
  apiFetch<TagStat[]>(`/api/analytics/tags?min_posts=${minPosts}&limit=${limit}`);
export const fetchTopPosts = (sort: "views" | "faves" | "comments" = "faves", limit = 10) =>
  apiFetch<TopPost[]>(`/api/analytics/top-posts?sort=${sort}&limit=${limit}`);
export const triggerEngagementSync = () =>
  apiFetch<{ sampled: number; errors: number }>("/api/analytics/sync", { method: "POST" });

export type BackupFile = {
  name: string;
  size_bytes: number;
  created_at: string;
};

export const listBackups = () => apiFetch<BackupFile[]>("/api/system/backups");

export const runBackup = () =>
  apiFetch<BackupFile>("/api/system/backups", { method: "POST" });

export const changePassword = (current_password: string, new_password: string) =>
  apiFetch<{ ok: true }>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password, new_password }),
  });

export type TagProfile = {
  id: string;
  name: string;
  tags: string;
  is_default: boolean;
  sort_order: number;
};

export type TagProfileInput = {
  name: string;
  tags: string;
  sort_order?: number;
};

export const listProfiles = () => apiFetch<TagProfile[]>("/api/profiles");
export const createProfile = (body: TagProfileInput) =>
  apiFetch<TagProfile>("/api/profiles", { method: "POST", body: JSON.stringify(body) });
export const updateProfile = (id: string, body: TagProfileInput) =>
  apiFetch<TagProfile>(`/api/profiles/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteProfile = (id: string) =>
  apiFetch<{ ok: true }>(`/api/profiles/${id}`, { method: "DELETE" });

export const getPostProfiles = (postId: string) =>
  apiFetch<string[]>(`/api/profiles/post/${postId}`);
export const setPostProfiles = (postId: string, profileIds: string[]) =>
  apiFetch<string[]>(`/api/profiles/post/${postId}`, {
    method: "PUT",
    body: JSON.stringify({ profile_ids: profileIds }),
  });

export type MergedTags = { user_tags: string[]; profile_tags: string[]; merged: string[] };
export const getMergedTags = (postId: string) =>
  apiFetch<MergedTags>(`/api/profiles/post/${postId}/merged`);

export type AIProvider = "anthropic" | "openai" | "both";

export type AIStatus = {
  enabled: boolean;
  provider: AIProvider;
  auto_apply: boolean;
  suggest_description: boolean;
  send_full_resolution: boolean;
  max_suggestions: number;
  tone: "concise" | "descriptive";
  providers: Record<string, { configured: boolean }>;
};

export type AISettingsUpdate = Partial<{
  enabled: boolean;
  provider: AIProvider;
  auto_apply: boolean;
  suggest_description: boolean;
  send_full_resolution: boolean;
  max_suggestions: number;
  tone: "concise" | "descriptive";
}>;

export type AITestResult = {
  ok: boolean;
  model: string | null;
  echo: string | null;
  error: string | null;
};

export type AISuggestion = {
  tags: string[];
  description: string | null;
  alt_text: string | null;
  provider: string;
  full_resolution: boolean;
  sources?: string[][] | null;
};

export const fetchAIStatus = () => apiFetch<AIStatus>("/api/ai/status");
export const updateAISettings = (body: AISettingsUpdate) =>
  apiFetch<AIStatus>("/api/ai/settings", { method: "PUT", body: JSON.stringify(body) });
export const testAIProvider = (provider: string) =>
  apiFetch<AITestResult>(`/api/ai/test/${provider}`, { method: "POST" });
export type SuggestHints = {
  hint_title?: string | null;
  hint_tags?: string | null;
  hint_description?: string | null;
  hint_venue?: string | null;
  hint_show?: string | null;
  hint_city?: string | null;
  hint_performers?: string[] | null;
};

export const aiSuggestForPost = (postId: string, hints?: SuggestHints) =>
  apiFetch<AISuggestion>(`/api/ai/suggest/${postId}`, {
    method: "POST",
    body: JSON.stringify(hints ?? {}),
  });

export type TrendingTag = {
  tag: string;
  score: number;
  seeds: string[];
};

export type TrendingResponse = {
  tags: TrendingTag[];
  seeds: string[];
  last_refresh: string | null;
};

export const fetchTrending = () => apiFetch<TrendingResponse>("/api/tags/trending");
export const setTrendingSeeds = (seeds: string[]) =>
  apiFetch<TrendingResponse>("/api/tags/trending/seeds", {
    method: "PUT",
    body: JSON.stringify({ seeds }),
  });
export const refreshTrending = () =>
  apiFetch<{ refreshed: number; seeds: string[]; errors: string[] }>(
    "/api/tags/trending/refresh",
    { method: "POST" },
  );

export type TagUsage = { tag: string; count: number };

export const fetchUsedTags = () => apiFetch<TagUsage[]>("/api/tags/used");

// --- Venues ----------------------------------------------------------------

export type Venue = {
  id: string;
  display_name: string;
  instagram_handle: string | null;
  usage_count: number;
  created_at: string;
  updated_at: string;
};

export const listVenues = (q?: string) => {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<Venue[]>(`/api/venues${qs}`);
};

export const createVenue = (display_name: string, instagram_handle?: string | null) =>
  apiFetch<Venue>("/api/venues", {
    method: "POST",
    body: JSON.stringify({ display_name, instagram_handle: instagram_handle || null }),
  });

export const updateVenue = (
  id: string,
  patch: { display_name?: string; instagram_handle?: string | null },
) =>
  apiFetch<Venue>(`/api/venues/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteVenue = (id: string) =>
  apiFetch<{ ok: boolean }>(`/api/venues/${id}`, { method: "DELETE" });

// Distinct values for typeahead.
export const fetchRecentShows = () => apiFetch<string[]>("/api/posts/recent-shows");
export const fetchRecentCities = () => apiFetch<string[]>("/api/posts/recent-cities");

// --- Performers ------------------------------------------------------------

export type Performer = {
  id: string;
  display_name: string;
  instagram_handle: string | null;
  usage_count: number;
  created_at: string;
  updated_at: string;
};

export const listPerformers = (q?: string) => {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<Performer[]>(`/api/performers${qs}`);
};

export const createPerformer = (display_name: string, instagram_handle?: string | null) =>
  apiFetch<Performer>("/api/performers", {
    method: "POST",
    body: JSON.stringify({ display_name, instagram_handle: instagram_handle || null }),
  });

export const updatePerformer = (
  id: string,
  patch: { display_name?: string; instagram_handle?: string | null },
) =>
  apiFetch<Performer>(`/api/performers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deletePerformer = (id: string) =>
  apiFetch<{ ok: boolean }>(`/api/performers/${id}`, { method: "DELETE" });

export const getPostPerformers = (post_id: string) =>
  apiFetch<Performer[]>(`/api/performers/by-post/${post_id}`);

export const setPostPerformers = (post_id: string, performer_ids: string[]) =>
  apiFetch<Performer[]>(`/api/performers/by-post/${post_id}`, {
    method: "PUT",
    body: JSON.stringify({ performer_ids }),
  });

// --- Reels ----------------------------------------------------------------

export type ReelCrop = { x: number; y: number; width: number; height: number };

export type ReelPhoto = {
  post_id: string;
  position: number;
  crop_start: ReelCrop;
  crop_end: ReelCrop | null;
};

export type Reel = {
  id: string;
  cover_post_id: string;
  total_duration_seconds: number;
  caption: string | null;
  status: "pending" | "ready" | "failed";
  error_message: string | null;
  mp4_available: boolean;
  photos: ReelPhoto[];
  created_at: string;
  updated_at: string;
};

export type ReelCreate = {
  cover_post_id: string;
  total_duration_seconds: number;
  caption?: string | null;
  photos: ReelPhoto[];
};

export const createReel = (body: ReelCreate) =>
  apiFetch<Reel>("/api/reels", { method: "POST", body: JSON.stringify(body) });

export const listReels = () => apiFetch<Reel[]>("/api/reels");

export const getReel = (id: string) => apiFetch<Reel>(`/api/reels/${id}`);

export const regenerateReel = (id: string) =>
  apiFetch<Reel>(`/api/reels/${id}/regenerate`, { method: "POST" });

export const updateReel = (id: string, patch: Partial<ReelCreate>) =>
  apiFetch<Reel>(`/api/reels/${id}`, { method: "PATCH", body: JSON.stringify(patch) });

export const deleteReel = (id: string) =>
  apiFetch<{ ok: boolean }>(`/api/reels/${id}`, { method: "DELETE" });

export const reelDownloadUrl = (id: string) => `/api/reels/${id}/mp4`;
