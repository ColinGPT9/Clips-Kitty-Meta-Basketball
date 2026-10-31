# Setting up WhatsApp notifications

These steps have not been run against live Meta yet: every test fakes WhatsApp
and the Graph API (meta-relay/test/harness.mjs). Allow about 30 minutes, once.
You need: a Meta developer account, a Meta Business portfolio, a phone number
for the business (Meta's free test number is meant for trials like the demo), a Cloudflare account (free), and Node 22.5+ on the PC you
deploy from. Nothing here goes into the desktop app except the relay's
address and a one-time enrolment key.

**Keep every token, secret and key out of chats, issues and screenshots.**
They go into `wrangler secret put` prompts only.

## 1. Create the Meta app

1. At [developers.facebook.com/apps](https://developers.facebook.com/apps),
   create an app of type **Business**, linked to your Business portfolio.
2. Add the **WhatsApp** product. In *WhatsApp → API Setup*, note the
   **Phone number ID** and the **WhatsApp Business Account ID**. Add your own
   phone as a recipient for the test number, or add your real business number.
3. Create a **System User** in Business Settings with the
   `whatsapp_business_messaging` and `whatsapp_business_management`
   permissions on this WhatsApp account, and generate a permanent token for
   it. (The 24-hour token on the API Setup page also works for a quick test.)
4. Note the app's **App ID** and **App secret** (*App settings → Basic*).

Publishing to Instagram, Facebook and Threads uses the same app; see
[META_PUBLISHING.md](META_PUBLISHING.md) for the products and permissions to add.

## 2. Deploy the relay

From the `meta-relay/` folder of this repository:

```
npx wrangler login
npx wrangler d1 create clips-kitty-relay          # copy the database_id it prints into wrangler.toml
npx wrangler d1 execute clips-kitty-relay --remote --file=schema.sql
npx wrangler deploy                                # prints https://clips-kitty-meta-relay.<you>.workers.dev
```

Put that address in `wrangler.toml` as `PUBLIC_URL` (no trailing slash) and,
optionally, your business number's digits as `WHATSAPP_DISPLAY_NUMBER` (for
the one-tap pairing link). Then set the secrets. Each command prompts for the
value; paste it there, not anywhere else:

```
npx wrangler secret put ENROLL_KEY                 # any long random string; you type it once in Clips Kitty
npx wrangler secret put TOKEN_KEY                  # openssl rand -base64 32
npx wrangler secret put WHATSAPP_TOKEN
npx wrangler secret put WHATSAPP_PHONE_NUMBER_ID
npx wrangler secret put WHATSAPP_WABA_ID
npx wrangler secret put WEBHOOK_VERIFY_TOKEN       # any random string; you type it once in Meta's webhook form
npx wrangler secret put META_APP_ID
npx wrangler secret put META_APP_SECRET
npx wrangler secret put THREADS_APP_ID             # for Threads publishing (META_PUBLISHING.md)
npx wrangler secret put THREADS_APP_SECRET
npx wrangler deploy
```

Check it: `https://<your relay>/health` answers `{"ok": true, ...}`.

## 3. Point WhatsApp's webhook at the relay

In the app, *WhatsApp → Configuration → Webhook*:

- Callback URL: `https://<your relay>/webhook`
- Verify token: the `WEBHOOK_VERIFY_TOKEN` you chose
- Subscribe to the **messages** field (and
  **message_template_status_update**, so template approvals show in Settings).

The relay checks every webhook's `X-Hub-Signature-256` with the app secret
and ignores anything unsigned.

## 4. Connect Clips Kitty

In Clips Kitty, **Settings → Notifications & Remote Approval**:

1. **Your relay**: paste the relay address and the enrolment key, then
   *Connect this PC*. The PC makes its own device secret (stored with
   Windows DPAPI) and registers it; the enrolment key is used once and not kept.
2. **WhatsApp (optional)**, the card's third section (the second, Your Muse agent, is only for Muse): *Pair my WhatsApp number* shows a code like
   `LINK K7QM2X`. Send it from your phone to the business number (or tap
   *Open WhatsApp*). The card then asks whether the number ending in, say,
   **4567** is yours; confirm it. A code alone never pairs a number.
3. Turn on **Send me WhatsApp messages**, choose which ones, and **Send a
   test message**.
4. **Submit templates to Meta**. The four utility templates
   (`ck_highlights_ready`, `ck_processing_failed`, `ck_publish_update`,
   `ck_test_message`) go to Meta for review; approval usually takes minutes
   to a few hours. *Check status* refreshes it. Until they are approved,
   messages only reach you within 24 hours of your last message to the
   business number (the relay sends a free-form message then).

Then set the approval PIN and connect accounts ([META_PUBLISHING.md](META_PUBLISHING.md)),
and switch on **Let me approve publishing from my phone**.

## Turning it off

- From the phone: send `STOP` (and `START` to resume).
- From the app: switch off *Send me WhatsApp messages*, or **Disconnect**
  (in 1. Your relay), which unpairs the number on the relay, cancels open
  offers, stops the Muse key, and makes this PC forget its device secret. The
  relay keeps this PC's old record, its PIN hash and the connected accounts'
  sealed tokens; to remove those tokens, press **Disconnect** next to each
  account in 4. Approval PIN and accounts first.

## Costs

WhatsApp utility templates are charged per delivered message (about
$0.0034 each in North America at the time of writing); replies inside the
24-hour window are free. Cloudflare Workers and D1 are free at this volume.
Settings shows a running estimate.
