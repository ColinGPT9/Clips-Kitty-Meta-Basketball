# Clips Kitty Meta relay

A small Cloudflare Worker (with a D1 database) that sits between a
creator's phone, Meta's APIs, and the Clips Kitty PC. New for the Meta
hackathon edition. The creator deploys their own; it is single-tenant.

- Sends WhatsApp messages (Cloud API) and receives WhatsApp webhooks.
- Serves the phone's approval page.
- Runs Facebook Login and Threads Login, and keeps the tokens (sealed).
- Makes the Instagram, Facebook and Threads Graph API calls for an approved
  publish, streaming the rendered file the PC sends it.

The PC only ever calls out to it with signed requests; it never calls the
PC. See [docs/hackathon/meta/ARCHITECTURE.md](../docs/hackathon/meta/ARCHITECTURE.md)
and [SECURITY.md](../docs/hackathon/meta/SECURITY.md).

## Deploy

Full steps, including the Meta app: [WHATSAPP_SETUP.md](../docs/hackathon/meta/WHATSAPP_SETUP.md)
and [META_PUBLISHING.md](../docs/hackathon/meta/META_PUBLISHING.md). In short:

```
npx wrangler login
npx wrangler d1 create clips-kitty-relay        # put database_id in wrangler.toml
npx wrangler d1 execute clips-kitty-relay --remote --file=schema.sql
# set PUBLIC_URL (and optionally WHATSAPP_DISPLAY_NUMBER) in wrangler.toml
for s in ENROLL_KEY TOKEN_KEY WHATSAPP_TOKEN WHATSAPP_PHONE_NUMBER_ID WHATSAPP_WABA_ID \
         WEBHOOK_VERIFY_TOKEN META_APP_ID META_APP_SECRET THREADS_APP_ID THREADS_APP_SECRET; do
  npx wrangler secret put $s
done
npx wrangler deploy
```

Webhook: `https://<relay>/webhook` (fields: `messages`,
`message_template_status_update`). OAuth redirects:
`https://<relay>/oauth/facebook/callback` and `https://<relay>/oauth/threads/callback`.

## Run it locally with Meta faked

```
npm test                      # 29 tests: node --test, D1 on node:sqlite, Meta faked
npm run dev                   # http://127.0.0.1:8787, prints an enrolment key
```

`npm run dev` runs the real `worker.js` with `test/serve.mjs`, whose
`/__mock/*` routes play the phone (send a message, tap a button, a delivery
status), finish an OAuth sign-in, run the cron, and move the clock. Nothing
is sent to WhatsApp or posted anywhere. `scripts/meta_demo.py` uses it for
the end-to-end demo.

Needs Node 22.5+ (for `node:sqlite`). No npm dependencies.

## Files

| File | What |
|---|---|
| `worker.js` | Routes, pairing, notify, the approval page's actions, commands, the Graph step gate, cron |
| `lib/whatsapp.js` | Cloud API sends, the four templates, error meanings, the 24-hour window |
| `lib/meta.js` | Facebook Login, Threads Login and refresh, the Graph steps, uploads to rupload |
| `lib/page.js` | The approval and status pages (server-rendered, no JavaScript) |
| `lib/crypto.js` | HMAC, AES-GCM sealing, PBKDF2 PIN hashing, canonical JSON |
| `lib/db.js` | D1 helpers and the audit log |
| `schema.sql` | The D1 schema |
| `test/` | The harness (D1 on node:sqlite, FakeMeta), the tests, and the local server |
