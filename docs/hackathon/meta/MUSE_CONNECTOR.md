# Connecting your Muse agent

Muse is Meta's personal AI agent app. In Muse chat you can add a private
**custom connector** for any service that offers an API or an MCP server,
and Muse keeps its credential in its Secure Credentials Store. Clips Kitty
offers one, so the person who runs a team's socials can ask Muse, from
anywhere:

- "What highlights are ready?"
- "Send me the approval link."
- "What got posted?"
- "Remind me later."
- "Cancel that one."

**Muse can never post anything.** Publishing still happens only on the
approval page (`/a/<link>`), where you tick the clips and the places to post
and enter your own PIN. Muse can hand you that link. No tool takes or shows
the PIN, and the connector tells Muse never to ask for it or type it.

Muse works with or without WhatsApp. With Muse connected and no WhatsApp
number paired, offers are kept on the relay for Muse to find, and no message
is sent anywhere.

## How it fits together

Muse's servers can't reach your PC or anything on a private network, so the
connector lives on your own relay (`meta-relay/`, the Cloudflare Worker you
deploy), next to the approval page:

```
Muse (Meta's servers) --HTTPS, Muse key--> your relay /mcp --> offers, links, results (D1)
                                                 ^
Clips Kitty PC --signed requests, outbound only--+   (the PC still opens no port)
```

The PC sends each finished video's offer to the relay as before. Muse reads
it there. When you approve on the page, the PC picks the approval up on its
next pass, checks it (`remote_approval/commands.py`) and publishes.

## Set it up

1. **Deploy the relay** as in step 2 of [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md),
   or redeploy it if you set it up before this connector existed. Run the
   schema again (it only adds what is missing, so it is safe on a relay
   that is already in use):

   ```
   npx wrangler d1 execute clips-kitty-relay --remote --file=schema.sql
   npx wrangler deploy
   ```

   For Muse alone, the WhatsApp secrets (`WHATSAPP_TOKEN`,
   `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WABA_ID`, `WEBHOOK_VERIFY_TOKEN`) can
   be left out. Publishing still needs `META_APP_ID`, `META_APP_SECRET` and,
   for Threads, `THREADS_APP_ID` and `THREADS_APP_SECRET`
   ([META_PUBLISHING.md](META_PUBLISHING.md)).

2. **Connect the PC** in Clips Kitty: **Settings → Notifications & Remote
   Approval → 1. Your relay**, with the relay's address and enrolment key
   (step 4 of [WHATSAPP_SETUP.md](WHATSAPP_SETUP.md), without the pairing).

3. **Make the key**: **2. Your Muse agent → Make a Muse key**. The key
   (`ckm_...`) is shown once, with a Copy button. Clips Kitty doesn't keep it:
   close the box and it is gone from the PC. If you lose it, make a new one.

4. **Tell Muse.** In Muse chat, say the sentence Settings shows, for example:

   > Create a custom connector called Clips Kitty: a remote MCP server at
   > https://clips-kitty-meta-relay.example.workers.dev/mcp that uses the
   > header Authorization: Bearer &lt;your key&gt;

   When Muse asks for the credential, paste the key into **Muse's credential
   prompt, not into the chat**. The sentence itself holds no secret.

5. **If Muse asks for API details instead** of an MCP server, give it the
   OpenAPI description: `https://<your relay>/muse/openapi.json`. It describes
   the same tools as plain HTTPS routes with the same key. A plain-text guide
   is at `https://<your relay>/muse`.

6. **Set an approval PIN and connect your accounts** (section 4 in
   Settings), then tick **Let me approve publishing from my phone**
   (section 5). With Muse connected this no longer needs a WhatsApp number;
   it still needs the PIN.

Try it: "Clips Kitty, what highlights are ready?"

## The tools

Every tool works only for the PC whose key it is.

| Tool | Changes anything? | What it does |
|---|---|---|
| `list_ready_highlights` | No | Offers waiting for a decision: title, what was found, kind of video, each clip (title, play, length, where it is already posted), the places it can post, when the link expires, any reminder, and when the PC last checked in. Optional: `match`, `since`, `limit` (1 to 20), `include_captions`. Never a link token or a thumbnail. |
| `get_approval_link` | No | The approval link for one offer (`approval_id`, or `match` to pick by name, or the one open offer). When none or several match, the choices instead, so Muse asks which one. The link is the same one a WhatsApp message carries. Each link handed out is logged as Muse's. |
| `list_recent_posts` | No | Offers approved in the last 1 to 7 days and what became of each clip, with only the links Instagram, Facebook and Threads returned. |
| `remind_me_later` | The offer | What the page's **Remind me later** does (15 to 1440 minutes, or your usual wait). With WhatsApp set up, it sends the link again then; without it, nothing is sent then, and Muse is told the time so it can remind you itself. |
| `cancel_offer` | The offer | What the page's **Cancel** does. Can't be undone, so it needs `confirm: true`, which Muse should only send after you say so. |
| `relay_status` | No | When the PC last checked in, whether a PIN is set, whether wrong PINs have blocked approving from the phone, the WhatsApp number's last four digits, the connected accounts and how many offers are open. |

There is no tool that approves, publishes, takes a PIN, pairs a number,
connects an account or changes a setting.

The same tools over plain HTTPS (same key, same answers):

| Route | Tool |
|---|---|
| `GET /muse/v1/offers`, optional query `match`, `since`, `limit`, `include_captions` (leave out any you don't set) | `list_ready_highlights` |
| `GET /muse/v1/offers/{approval_id}/link` | `get_approval_link` |
| `GET /muse/v1/posts`, optional query `days`, `limit` (leave out any you don't set) | `list_recent_posts` |
| `POST /muse/v1/offers/{approval_id}/remind` `{"minutes": 60}` | `remind_me_later` |
| `POST /muse/v1/offers/{approval_id}/cancel` `{"confirm": true}` | `cancel_offer` |
| `GET /muse/v1/status` | `relay_status` |

To check the connector yourself, with the key in an environment variable
rather than typed into a command:

```
curl https://<your relay>/muse/openapi.json
curl -H "Authorization: Bearer $MUSE_KEY" https://<your relay>/muse/v1/offers
curl -X POST https://<your relay>/mcp -H "Authorization: Bearer $MUSE_KEY" \
  -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## What Muse can and can't do

Muse **can**:

- say which highlights are waiting, and what they are;
- give you the approval link;
- say what got posted, with the links the platforms returned;
- put an offer aside until later, or cancel it when you say so.

Muse **can't**:

- approve or publish anything. Approving takes the approval page, your PIN
  and the "post publicly" tick; then the PC checks the approval against its
  own record of the offer; then the relay opens each Instagram, Facebook or
  Threads step only under an approval the PC accepted. Muse is in none of
  those steps.
- see or use your PIN. The page says next to the PIN box: "Enter your PIN
  yourself. Never give it to an assistant." If Muse ever asks for it, don't.
- call the PC's own routes. A Muse key works on `/mcp` and `/muse/v1/` only,
  and the PC's signature works everywhere else, never there.

Everything Muse does is marked as Muse's: a reminder or a cancel reaches the
PC as a signed command carrying `via: "muse"`, and the relay's and the PC's
audit logs say `muse`. The PC refuses any approval that says it came from
Muse, even one the relay signed.

## The PIN, harder to guess

Five wrong PINs lock the approval page for 15 minutes, as before, and wrong
PINs sent all at once count the same as one at a time. Three of those
lockouts within a day now **block approving from the phone** until you set a
new PIN in Clips Kitty on the PC, and the page says so (so does Muse, if you
ask it for the link). Reminding and cancelling still work while it is
blocked. Someone with a link and no PIN gets at most fifteen guesses in a
day, where the 15-minute lock alone allowed twenty an hour.

## The key

- It starts with `ckm_`. The relay keeps only its SHA-256, never the key.
- Keep it like a password. Whoever has it can read your offers and get their
  approval links, so they could remind or cancel, but approving still takes
  your PIN.
- It is accepted only in the `Authorization: Bearer` header, never in the
  address.
- About 60 calls a minute per key; more get `429` with `Retry-After`.
- One key works per PC. **Make a new key** replaces the old one at once.
- **Revoke** in Settings stops it at once. Disconnecting the PC from the
  relay stops it too.
- Settings shows when it was last used.

If approving from the phone was on only because of Muse, revoking the key
switches it off as well: nothing would reach you any more.

## Not confirmed yet about Muse's connectors

These are built from Meta's description of custom connectors and the MCP
specification, not tested against Muse itself:

- **Which MCP revision Muse speaks.** The relay speaks both: the
  `initialize` handshake (2025-03-26, 2025-06-18 and 2025-11-25, answered in
  plain JSON, never a session) and the stateless 2026-07-28 revision, where
  each request names its version in `_meta` and the `MCP-Protocol-Version`,
  `Mcp-Method` and `Mcp-Name` headers must agree with the body. Server
  Sent Events are not offered (GET `/mcp` answers 405), and neither are
  JSON-RPC batches, which only 2025-03-26 allowed: a client that sends
  several messages in one request is asked to send one at a time.
- **OAuth or a header key.** The relay takes a key in a header. Meta
  describes custom connectors as keeping a credential in Muse's Secure
  Credentials Store, but not yet whether a remote MCP server can be given a
  header key that way. If Muse only connects to remote MCP servers through
  OAuth, this connector won't connect as it is: the OpenAPI routes with the
  same key are the fallback to try, and OAuth would need to be added to the
  relay.
- **The `Origin` header.** Requests that carry an `Origin` other than the
  relay's own are refused (a protection against web pages calling it). Muse's
  servers are not expected to send one; if they do, Muse's origin would need
  to be allowed.
- **Annotations and structured results.** Every tool says whether it reads
  only or changes something (`readOnlyHint`, `destructiveHint`), and answers
  with plain text and the same data as `structuredContent`. Whether Muse asks
  you before a changing tool, or reads the structured data, is up to Muse.

## Where it is

| File | What |
|---|---|
| `meta-relay/lib/muse.js` | The MCP server, the tools, the REST routes, the OpenAPI description, keys and their rate limit |
| `meta-relay/worker.js` | Making and revoking keys for the PC (`/v1/muse/token`, `/v1/muse/revoke`), offers kept for Muse alone, the PIN lockouts |
| `meta-relay/test/muse.test.mjs` | The connector's tests, including that nothing Muse sends ever makes an approval |
| `server/remote_approval_api.py` | `POST /remote-approval/muse/token` and `/muse/revoke`, behind the desktop app's session header |
| `ui/src/renderer/src/components/NotificationsApprovalCard.tsx` | **2. Your Muse agent** in Settings |
