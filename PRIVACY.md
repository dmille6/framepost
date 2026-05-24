# Privacy Policy

_Last updated: 2026-05-24_

FramePost is open-source software ("the Software"). It is **self-hosted** —
you run it on your own infrastructure. The maintainer of this repository
("we") does not operate any FramePost servers, does not host any FramePost
instances for users, and does not receive, see, or store any data from your
deployment.

This policy describes what data the Software handles on your own server when
you run it, and what data it sends to third-party services on your behalf.

## What the Software stores on your server

When you connect external accounts (Flickr, Bluesky, Pixelfed, Pinterest,
Instagram engagement tracking), the Software stores the following **on your
own server**, in a local SQLite database:

- **OAuth access tokens and refresh tokens** for each connected platform,
  encrypted at rest using a per-deployment Fernet key (`TOKEN_ENCRYPTION_KEY`
  in `.env`).
- **Public account identifiers** returned by each platform (your username,
  user ID, profile URL, default board ID for Pinterest, instance URL for
  Pixelfed, etc.).
- **Photo files and their metadata** that you upload or import. Originals are
  retained for 30 days then purged; permanent thumbnails are kept.
- **Post records** — title, description, tags, schedule, posting outcome
  per platform, remote IDs and URLs of successful posts.
- **Engagement data** — comments, likes, and view counts fetched from the
  platforms you have connected, stored locally to surface in the Activity
  feed.

This data never leaves your server except through deliberate actions you take
in the application (publishing a post, refreshing engagement, etc.).

## What the Software sends to third parties

The Software acts as an HTTP client and makes requests to the APIs of the
platforms you have connected, **only when you initiate or schedule a post**
or when scheduled background jobs run on your server. Specifically:

- **Flickr API** — to upload photos, set metadata, add to albums/groups, and
  sync engagement statistics.
- **Bluesky (atproto)** — to publish posts and refresh comments/likes.
- **Pixelfed / Mastodon-compatible APIs** — to publish posts.
- **Pinterest API v5** — to create pins on your default board with the
  photo, title, description, and a link back to the photo's Flickr URL.
- **Anthropic API** and **OpenAI API** — if you supply API keys, photos and
  short prompts are sent to these services to generate caption and tag
  suggestions. No identifying user information is sent.

The Software does **not** send any data to the maintainer or to any third
party for analytics, telemetry, advertising, or any other purpose.

## Pinterest data specifically

For Pinterest integration, the Software:

- Requests these OAuth scopes: `boards:read`, `boards:write`, `pins:read`,
  `pins:write`, `user_accounts:read`.
- Stores your Pinterest access token, refresh token, username, user ID, and
  the ID/name of the board you select as the default pin destination — all
  in the local encrypted database described above.
- Sends each pin (image, title, description, and link to the Flickr URL of
  the same photo) to Pinterest's API using your access token when you
  publish a post.
- Does not read or modify any pin, board, or account data beyond what is
  required to create pins on the user-selected board and display the
  connected username and board list in the Settings UI.

## Data retention and deletion

- All connection credentials are deleted from the database when you click
  **Disconnect** for that platform in Settings.
- Original photo files are automatically purged after 30 days; thumbnails are
  retained permanently.
- You can wipe the entire database at any time by stopping the Software and
  deleting `backend/data/framepost.db`.

## Security

- OAuth tokens are encrypted at rest with Fernet (`cryptography` library)
  using a key you generate per deployment.
- The application requires authentication for all routes other than the
  health check.
- The `.env` file containing API keys and the encryption key is set to mode
  600 and is excluded from version control.
- HTTPS is the responsibility of the deployer; the Software ships with HTTP
  inside the local network perimeter.

## Children's privacy

FramePost is not intended for use by children under 13 and does not
knowingly collect any data from children.

## Changes to this policy

We may update this policy from time to time. Material changes will be
described in the repository commit history. The "Last updated" date above
will reflect the most recent revision.

## Contact

For questions about this policy, the data the Software handles, or the
maintainer's role: **darrell@darrellmillerphotography.com**.
