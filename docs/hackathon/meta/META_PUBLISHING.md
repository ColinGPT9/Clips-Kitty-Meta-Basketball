# Publishing to Instagram, Facebook and Threads

Approved clips are published through Meta's official APIs only, by the
creator's own relay, with the creator's own Meta app. WoopSocial and
Upload-Post remain available in Clips Kitty as alternatives; this path does
not use them.

## What gets posted

| Platform | How | API steps (Graph API v26.0) |
|---|---|---|
| Instagram Reels | The rendered MP4, uploaded with a resumable upload | `POST /{ig-user-id}/media` (`media_type=REELS`, `upload_type=resumable`, caption) → bytes to `rupload.facebook.com` → poll `GET /{container}?fields=status_code` until `FINISHED` → `POST /{ig-user-id}/media_publish` → `GET /{media-id}?fields=permalink` |
| Facebook Page Reels | The same file, to the Page | `POST /{page-id}/video_reels` (`upload_phase=start`) → bytes to `rupload.facebook.com` → `upload_phase=finish`, `video_state=PUBLISHED`, title and description → poll `GET /{video-id}?fields=status,permalink_url` until published |
| Threads | A text post linking to the Reel just confirmed | `POST /{threads-user-id}/threads` (`media_type=TEXT`, text, `link_attachment`) → poll status → `POST /threads_publish` → `GET /{id}?fields=permalink` |

Threads cannot take a video from a file (only from a public URL), and the
rendered clip lives on the creator's PC, so the Threads post links to the
Instagram (or Facebook) Reel instead. If neither went up, Threads is
skipped and reported as skipped.

The caption text is the clip's own metadata from the pipeline, or Muse
Spark's when turned on, held to each platform's limits (Instagram: 2,200
characters and 30 hashtags; Facebook title 100; Threads 500 bytes).

A post counts as published only when the API returned its id. The link
shown is the permalink the API returned, never one built from an id. Still
processing at the deadline (10 minutes) is reported as "not confirmed yet",
not as published.

## Set up the Meta app

Use the same Business app as WhatsApp ([WHATSAPP_SETUP.md](WHATSAPP_SETUP.md)).

**Facebook Login for Business** (for the Page and its Instagram account):

1. Add the **Facebook Login for Business** product.
2. Valid OAuth redirect URI: `https://<your relay>/oauth/facebook/callback`
3. Permissions requested: `pages_show_list`, `pages_read_engagement`,
   `pages_manage_posts`, `instagram_basic`, `instagram_content_publish`,
   `business_management`.
4. The Instagram account must be a **professional** (Business or Creator)
   account **linked to the Facebook Page**.

**Threads API**:

1. Add the **Threads API** use case. Note its **Threads app ID** and
   **Threads app secret** (they differ from the Meta app's) and set them as
   the relay's `THREADS_APP_ID` and `THREADS_APP_SECRET`.
2. Redirect callback URL: `https://<your relay>/oauth/threads/callback`
3. Permissions requested: `threads_basic`, `threads_content_publish`.
4. Add the Threads account as a **Threads tester** (*App roles*) and accept
   the invitation in the Threads app (*Settings → Account → Website
   permissions → Invites*).

Then in Clips Kitty, **Settings → Notifications & Remote Approval → Approval
PIN and accounts**: **Connect** next to *Facebook Page + Instagram* and next
to *Threads*. The sign-in opens in the browser on Meta's own site; the token
goes to the relay, sealed with `TOKEN_KEY`, and never to the PC. With more
than one Page, the relay asks which one.

## App Review and access levels

Without App Review, the app is in Standard Access: these permissions work
**only for people who have a role on the app** (admin, developer, tester).
For a creator publishing to their own accounts with their own app, and for
the hackathon demo, that is enough: add yourself (and the Instagram and
Threads accounts' owners) as app roles.

To publish for accounts whose owners have no role on the app, Meta requires
**Advanced Access** through App Review for `instagram_content_publish`,
`pages_manage_posts` and `threads_content_publish`, plus Business
Verification. This fork does not try to get around that, and does not
claim to have it.

## Limits worth knowing

- **Instagram**: 50 API-published posts per account per rolling 24 hours
  (`content_publishing_limit` reports the current quota); hitting it is
  reported as "daily publishing limit reached".
- **Facebook Page Reels**: 30 API-published Reels per Page per rolling 24 hours.
- **Threads**: 250 API-published posts per profile per rolling 24 hours.
- **Upload size**: every byte passes through the relay in one request, and
  Cloudflare limits a request body to 100 MB on the Free and Pro plans, so
  clips over 95 MB are refused with a message. Highlight clips at 1080×1920
  are typically 10–40 MB.
- **Tokens**: Facebook Page tokens from a long-lived user token don't
  expire while the app keeps its permissions; Threads tokens last 60 days
  and the relay refreshes them in its cron. An expired or revoked token is
  reported as "Reconnect it in Settings".

## Errors

Meta's error codes become messages a creator can act on
(`publish/meta_graph.py classify`); Meta's own error text stays on the
relay, where it might contain an id. For example: code 190 → "Your Instagram
connection has expired. Reconnect it…"; 10 or 200–299 → permission missing;
4/17/32/613 → "asked us to slow down"; subcode 2207042 → daily limit;
2207026 → video format; 368 → account restricted.
