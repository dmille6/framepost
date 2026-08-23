# FramePost — Instagram Integration

FramePost publishes to Instagram fully automatically through Meta's
**"Instagram API with Instagram Login"** (`graph.instagram.com`). This is the
post-July-2024 route: no Facebook Page link, no OAuth redirect flow, and —
for a single-user app — **no App Review**. Everything here was verified
against a live account in August 2026.

## One-time Meta setup (~10 minutes)

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) →
   **Create App** → type **Business**.
2. Add the **Instagram** product → *API setup with Instagram business login*.
3. Confirm `instagram_business_content_publish` is enabled under
   **Permissions and features** (the setup screen only lists the messaging
   trio by default — publish is the one FramePost can't live without).
4. **App roles → Roles → Instagram Testers → Add** your IG username.
   The IG account must be **Professional** (Business or Creator) and public.
5. Accept the invite from the Instagram side:
   `instagram.com/accounts/manage_access` → **Tester Invites** → Accept.
   (This is the step everyone misses — without it, *Add account* fails with
   "Insufficient Developer Role".)
6. Back in the API-setup screen: **Generate access tokens → Add account** →
   log in → **Generate token**. The dashboard token is already long-lived
   (~60 days).
7. Paste it into FramePost → **Settings → Platforms → Instagram**.

Leave the app in **Development mode** — that is what exempts a
single-owner app from App Review. Skip webhooks and the business-login
setup entirely.

## Token lifecycle

- Long-lived tokens last ~60 days. FramePost refreshes via
  `GET /refresh_access_token?grant_type=ig_refresh_token` once the token is
  inside a 7-day leeway window, checked at post time and by a daily job —
  a healthy install never expires.
- A refresh needs no app secret — only the current token.
- **Instant invalidation**: changing the Instagram password, a Meta security
  reset, or removing the app. There is no API-side recovery — generate a new
  dashboard token and reconnect. FramePost surfaces this as a health-banner
  warning and treats Meta error `190` as "reconnect required", never a retry.

## Publishing flow

Two-step container publish, implemented in
`backend/services/platforms/instagram.py`:

1. `POST /{ig_user_id}/media` with `image_url`, `caption`, `alt_text`
   (Meta fetches the image server-side during this call)
2. Poll `GET /{container}?fields=status_code` until `FINISHED`
3. `POST /{ig_user_id}/media_publish` → media id → permalink

`image_url` is the photo's **Flickr derivative URL** — the canonical public
copy. Flickr static URLs embed the photo secret, so they resolve regardless
of the photo's privacy setting.

### Hard-won constraints (verified live)

| Finding | Consequence in code |
|---|---|
| Meta's fetcher **rejects Flickr `_o` "Original" URLs** ("The media URI doesn't meet our requirements") while accepting normal derivative URLs of the same photo | Staging variants use the *Large 2048* derivative; the display-URL helper never hands Meta an `_o` URL |
| "Media download has failed" can also be a **CDN propagation race** on freshly-uploaded staging photos | Staging waits for the URL to actually serve (HEAD poll) and the container call retries the download error as transient |
| Docs say the portrait floor is 4:5, but the API **accepted 3:4** on this account (May-2025 grid change) | The variant pipeline probes optimistically at 3:4; on an aspect rejection it records `4:5` in `app_config.ig_min_ratio_support` and regenerates — one wasted upload, once, ever |
| JPEG only, public URL only, no binary upload for feed images on this route | All feed images route through Flickr |
| Daily API-publish quota (docs currently say 50/24 h) | `GET /{ig_user_id}/content_publishing_limit` surfaced via the Settings test button — a non-issue at one-post-per-day cadence |
| `alt_text` accepted on image containers (since 2025-03), not Reels/Stories | The AI-generated alt text rides along on every feed publish |

## Portrait auto-transform

Photos taller than the accepted floor don't fail — the worker renders a
variant (see the diagram in [architecture.md](architecture.md)):

- **Smart crop** (default): window anchored by OpenCV face detection with a
  ~38% headroom bias; center-crop when no face is found. A per-post **nudge
  slider** in the editor overrides the window (top ↔ bottom), with a live
  preview served by `GET /api/posts/{id}/ig-preview`.
- **Pad** / **Pad-blur**: letterbox on black, or on a blurred darkened
  cover-fill of the photo itself.
- The variant uploads to Flickr as **private + hidden**, machine-tagged
  `framepost:ig_variant=<post_id>` (so sync and duplicate checks ignore it),
  and is deleted right after a successful publish. A daily sweep removes
  strays. Note: deleting on Flickr requires the OAuth grant to include
  **delete** perms.

## Engagement sync

The daily sync stores like/comment **counts** per post
(`engagement_snapshots`) and attempts the full comment thread:

- **Per-user like lists are never available** on any tier — Meta privacy
  policy. The Activity stream renders count *deltas* (`▲ +N likes`) instead.
- **Comment text is filtered in Development mode**: the `/comments` edge
  returns an empty `data` array (with paging cursors!) for comments authored
  by anyone without a role on the Meta app — i.e. all real followers. The
  counts are real; the content is withheld. **Switching the Meta app to Live
  mode lifts this** (requires a Privacy Policy URL in the app's Basic
  Settings; still no App Review for your own account). The moment the app is
  Live, the existing sync starts pulling author + text + replies with no
  code changes.
- Deleted-on-Instagram media are detected ("does not exist") and skipped
  quietly forever.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Insufficient Developer Role" during Add account | Tester invite not accepted (step 5), or the browser holds a different IG session — use a private window |
| "Form can't be saved" adding an Instagram Tester | Meta dashboard flakiness — use the dedicated *Instagram Testers* section (not the unified Add People dialog), keep instagram.com logged in alongside, retry |
| Error 190 / "token expired" | Password change or revoke — generate a new dashboard token, reconnect in Settings |
| "Media download has failed. The media URI doesn't meet our requirements" | Almost always an `_o` Original URL (never hand Meta one) or a not-yet-propagated fresh upload — both handled by current code |
| Container error mentioning aspect ratio | Outside the accepted range — the variant pipeline handles it; if you see it, the photo has no usable dimensions recorded |
| Comments sync shows counts but no text | Development-mode filtering — switch the app to Live mode (above) |
