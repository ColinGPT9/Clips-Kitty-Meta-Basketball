// The relay end to end under Node: D1 on node:sqlite, Meta faked, nothing
// leaves the machine. Run with `node --test test/` from meta-relay/.

import assert from "node:assert/strict";
import { createHmac, randomBytes } from "node:crypto";
import { afterEach, beforeEach, describe, it } from "node:test";

import worker, { cron, handleWebhook } from "../worker.js";
import { canonical } from "../lib/crypto.js";
import { FakeMeta, inboundButton, inboundText, makeCtx, makeEnv, signed, statusUpdate, webhookRequest } from "./harness.mjs";

const BASE = "https://relay.test";
const DEVICE = "dev_0123456789abcdef";
const SECRET = "device-secret-for-tests-0123456789abcdef";
const PHONE = "15551234567";

let env;
let fake;
let realFetch;

async function call(req) {
  const ctx = makeCtx();
  const res = await worker.fetch(req, env, ctx);
  await ctx.settle();
  return res;
}

async function pc(method, path, body) {
  const res = await call(signed(BASE, DEVICE, SECRET, method, path, body));
  const data = await res.json();
  return { status: res.status, data };
}

async function enroll() {
  const res = await call(new Request(`${BASE}/v1/devices`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.ENROLL_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: DEVICE, device_secret: SECRET, name: "Test PC" }),
  }));
  assert.equal(res.status, 200);
}

async function wh(payload) {
  const res = await call(webhookRequest(BASE, env.META_APP_SECRET, payload));
  assert.equal(res.status, 200);
}

async function pairPhone() {
  const start = await pc("POST", "/v1/pair/start", {});
  assert.equal(start.status, 200);
  await wh(inboundText(PHONE, `LINK ${start.data.code}`));
  const confirm = await pc("POST", "/v1/pair/confirm", { confirm: true });
  assert.equal(confirm.data.pairing.state, "confirmed");
}

async function connectAll() {
  for (const platform of ["facebook", "threads"]) {
    const { data } = await pc("POST", "/v1/accounts/connect", { platform });
    const state = new URL(data.url).searchParams.get("state");
    const res = await call(new Request(`${BASE}/oauth/${platform}/callback?state=${state}&code=abc`));
    assert.equal(res.status, 200, await res.clone().text());
  }
}

function offer(overrides = {}) {
  return {
    kind: "ready",
    event_key: "ready:job:7:1",
    review_ref: 3,
    fingerprint: "f".repeat(32),
    expires_at: new Date(Date.now() + 24 * 3600 * 1000).toISOString(),
    title: "Raptors vs Celtics",
    summary_text: "3 clips: 2 dunks and 1 three",
    muse_line: "",
    remote_approvals: true,
    preferred: ["instagram"],
    remind_minutes: 120,
    clips: [
      { clip_id: 11, title: "Barnes poster dunk", label: "Poster dunk", category: "dunks", duration: 14, texts: { instagram: "Poster! #basketball", facebook: "Poster!", threads: "Poster dunk" }, thumb: Buffer.from("jpegbytes").toString("base64") },
      { clip_id: 12, title: "Corner three", label: "Corner three", category: "threes", duration: 11, texts: { instagram: "Cash", facebook: "Cash", threads: "Cash" } },
      { clip_id: 13, title: "Alley-oop", label: "Alley-oop", category: "dunks", duration: 12, texts: { instagram: "Oop", facebook: "Oop", threads: "Oop" } },
    ],
    ...overrides,
  };
}

function tokenFrom(message) {
  const button = message.template.components.find((c) => c.sub_type === "url");
  return button.parameters[0].text;
}

async function page(token) {
  const res = await call(new Request(`${BASE}/a/${token}`));
  return { status: res.status, text: await res.text() };
}

function csrfOf(html) {
  return html.match(/name="csrf" value="([0-9a-f]{64})"/)[1];
}

async function submit(token, fields, headers = {}) {
  const form = new URLSearchParams();
  for (const [k, v] of fields) form.append(k, v);
  const res = await call(new Request(`${BASE}/a/${token}`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Origin: BASE, ...headers },
    body: form.toString(),
  }));
  return { status: res.status, text: res.status === 303 ? "" : await res.text(), location: res.headers.get("Location") };
}

function uploadRequest(pathAndQuery, bytes) {
  const headers = signed(BASE, DEVICE, SECRET, "POST", pathAndQuery, undefined, { unsigned: true }).headers;
  headers.set("Content-Type", "application/octet-stream");
  headers.set("Content-Length", String(bytes.length));
  return new Request(BASE + pathAndQuery, { method: "POST", headers, body: bytes, duplex: "half" });
}

async function commands() {
  return (await pc("GET", "/v1/commands")).data.commands;
}

beforeEach(async () => {
  env = makeEnv();
  realFetch = globalThis.fetch;
  fake = new FakeMeta(realFetch);
  globalThis.fetch = fake.fetch;
  await enroll();
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

describe("basics", () => {
  it("answers health", async () => {
    const res = await call(new Request(`${BASE}/health`));
    assert.equal((await res.json()).ok, true);
  });

  it("verifies the WhatsApp webhook only with the right token", async () => {
    const good = await call(new Request(`${BASE}/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345`));
    assert.equal(good.status, 200);
    assert.equal(await good.text(), "12345");
    const bad = await call(new Request(`${BASE}/webhook?hub.mode=subscribe&hub.verify_token=nope&hub.challenge=12345`));
    assert.equal(bad.status, 403);
  });

  it("refuses a webhook whose signature is wrong", async () => {
    const raw = JSON.stringify(inboundText(PHONE, "hello"));
    const res = await call(new Request(`${BASE}/webhook`, { method: "POST", headers: { "X-Hub-Signature-256": `sha256=${"0".repeat(64)}` }, body: raw }));
    assert.equal(res.status, 401);
  });

  it("enrols only with the relay's key", async () => {
    const res = await call(new Request(`${BASE}/v1/devices`, {
      method: "POST", headers: { Authorization: "Bearer wrong" }, body: JSON.stringify({ device_id: DEVICE, device_secret: SECRET }),
    }));
    assert.equal(res.status, 401);
  });
});

describe("signed requests", () => {
  it("accepts a good signature and refuses a bad one", async () => {
    assert.equal((await pc("GET", "/v1/status")).status, 200);
    const res = await call(signed(BASE, DEVICE, "the-wrong-secret-0123456789abcdefghij", "GET", "/v1/status"));
    assert.equal(res.status, 401);
  });

  it("refuses a replayed nonce", async () => {
    const nonce = randomBytes(12).toString("hex");
    const first = await call(signed(BASE, DEVICE, SECRET, "GET", "/v1/status", undefined, { nonce }));
    assert.equal(first.status, 200);
    const again = await call(signed(BASE, DEVICE, SECRET, "GET", "/v1/status", undefined, { nonce }));
    assert.equal(again.status, 401);
    assert.equal((await again.json()).code, "replay");
  });

  it("refuses a request from a clock far off", async () => {
    const res = await call(signed(BASE, DEVICE, SECRET, "GET", "/v1/status", undefined, { ts: Math.floor(Date.now() / 1000) - 3600 }));
    assert.equal((await res.json()).code, "clock");
  });

  it("refuses a body changed after signing", async () => {
    const req = signed(BASE, DEVICE, SECRET, "POST", "/v1/pin", { pin: "4826" });
    const tampered = new Request(req.url, { method: "POST", headers: req.headers, body: JSON.stringify({ pin: "1357" }) });
    assert.equal((await call(tampered)).status, 401);
  });
});

describe("pairing", () => {
  it("pairs a number that sends the code, after the PC confirms it", async () => {
    const start = await pc("POST", "/v1/pair/start", {});
    assert.match(start.data.code, /^[A-Z2-9]{6}$/);
    assert.match(start.data.link, /^https:\/\/wa\.me\/15550001111\?text=LINK%20/);
    await wh(inboundText(PHONE, `link ${start.data.code.toLowerCase()}`));
    let status = (await pc("GET", "/v1/status")).data;
    assert.equal(status.pairing.state, "awaiting_confirm");
    assert.equal(status.pairing.tail, "4567");
    assert.match(fake.messages.at(-1).text.body, /ending in 4567/);
    await pc("POST", "/v1/pair/confirm", { confirm: true });
    status = (await pc("GET", "/v1/status")).data;
    assert.equal(status.pairing.state, "confirmed");
    assert.match(fake.messages.at(-1).text.body, /Reply STOP/);
  });

  it("does not pair on a wrong code, and ignores strangers", async () => {
    await pc("POST", "/v1/pair/start", {});
    await wh(inboundText(PHONE, "LINK ZZZZZZ"));
    assert.match(fake.messages.at(-1).text.body, /didn't match/);
    const before = fake.messages.length;
    await wh(inboundText("15559999999", "hello there"));
    assert.equal(fake.messages.length, before);
    assert.equal((await pc("GET", "/v1/status")).data.pairing.state, "pending");
  });

  it("handles a webhook delivered twice once", async () => {
    const start = await pc("POST", "/v1/pair/start", {});
    const payload = inboundText(PHONE, `LINK ${start.data.code}`, "wamid.same");
    await wh(payload);
    const sent = fake.messages.length;
    await wh(payload);
    assert.equal(fake.messages.length, sent);
  });

  it("stops on STOP and refuses to notify", async () => {
    await pairPhone();
    await wh(inboundText(PHONE, "STOP"));
    const res = await pc("POST", "/v1/notify", { kind: "test", event_key: "t1" });
    assert.equal(res.status, 409);
    assert.equal(res.data.code, "opted_out");
  });

  it("rejects weak PINs", async () => {
    assert.equal((await pc("POST", "/v1/pin", { pin: "1111" })).status, 400);
    assert.equal((await pc("POST", "/v1/pin", { pin: "1234" })).status, 400);
    assert.equal((await pc("POST", "/v1/pin", { pin: "12a4" })).status, 400);
    assert.equal((await pc("POST", "/v1/pin", { pin: "4826" })).status, 200);
  });
});

describe("offers and approval", () => {
  beforeEach(async () => {
    await pairPhone();
    await pc("POST", "/v1/pin", { pin: "4826" });
    await pc("POST", "/v1/preferences", { notify_on_finish: true, notify_on_fail: true, notify_on_publish: true, remote_approvals: true, preferred: ["instagram"], remind_minutes: 120 });
    await connectAll();
  });

  it("sends the ready template once per event key", async () => {
    const first = await pc("POST", "/v1/notify", offer());
    assert.equal(first.status, 200);
    assert.equal(first.data.channel, "template");
    const msg = fake.messages.at(-1);
    assert.equal(msg.template.name, "ck_highlights_ready");
    assert.deepEqual(msg.template.components[0].parameters.map((p) => p.text), ["Raptors vs Celtics", "3 clips: 2 dunks and 1 three"]);
    assert.match(tokenFrom(msg), /^[A-Za-z0-9_-]{43}$/);
    const count = fake.messages.length;
    const again = await pc("POST", "/v1/notify", offer());
    assert.equal(again.data.duplicate, true);
    assert.equal(again.data.approval_id, first.data.approval_id);
    assert.equal(fake.messages.length, count);
  });

  it("falls back to an interactive link while the template is not approved", async () => {
    fake.templateFails = 1;
    const res = await pc("POST", "/v1/notify", offer());
    assert.equal(res.data.channel, "interactive");
    const msg = fake.messages.at(-1);
    assert.equal(msg.interactive.type, "cta_url");
    assert.match(msg.interactive.action.parameters.url, /^https:\/\/relay\.test\/a\//);
  });

  it("approves only with the PIN, the confirm tick and real choices", async () => {
    const sent = await pc("POST", "/v1/notify", offer());
    const token = tokenFrom(fake.messages.at(-1));
    const view = await page(token);
    assert.equal(view.status, 200);
    assert.match(view.text, /Barnes poster dunk/);
    assert.match(view.text, /hoopsdaily/);
    assert.doesNotMatch(view.text, /page-token|fb-long|th-long/);
    const csrf = csrfOf(view.text);
    const base = [["csrf", csrf], ["action", "approve"], ["clip", "11"], ["platform", "instagram"], ["confirm", "yes"]];

    let r = await submit(token, [...base, ["pin", "0000"]]);
    assert.equal(r.status, 403);
    assert.match(r.text, /PIN is not right/);
    r = await submit(token, base.filter(([k]) => k !== "confirm").concat([["pin", "4826"]]));
    assert.match(r.text, /Tick the box/);
    r = await submit(token, [["csrf", csrf], ["action", "approve"], ["clip", "99"], ["platform", "instagram"], ["confirm", "yes"], ["pin", "4826"]]);
    assert.match(r.text, /Tick at least one clip/);
    r = await submit(token, [["csrf", csrf], ["action", "approve"], ["clip", "11"], ["platform", "threads"], ["confirm", "yes"], ["pin", "4826"]]);
    assert.match(r.text, /Threads posts link/);
    assert.equal((await commands()).filter((c) => c.kind === "approve").length, 0);

    r = await submit(token, [...base, ["clip", "12"], ["platform", "threads"], ["pin", "4826"]]);
    assert.equal(r.status, 303);
    const cmds = await commands();
    const approve = cmds.find((c) => c.kind === "approve");
    assert.deepEqual(approve.clips, [11, 12]);
    assert.deepEqual(approve.platforms, ["instagram", "threads"]);
    assert.equal(approve.approval_id, sent.data.approval_id);
    assert.equal(approve.review_ref, 3);
    assert.equal(approve.fingerprint, "f".repeat(32));
    const { sig, ...rest } = approve;
    assert.equal(sig, createHmac("sha256", SECRET).update(canonical(rest)).digest("hex"));

    // Single use: a second approval of the same link does nothing.
    r = await submit(token, [...base, ["pin", "4826"]]);
    assert.equal((await commands()).filter((c) => c.kind === "approve").length, 1);
    assert.match((await page(token)).text, /Waiting for your PC/);
  });

  it("locks the PIN after five wrong tries", async () => {
    await pc("POST", "/v1/notify", offer());
    const token = tokenFrom(fake.messages.at(-1));
    const csrf = csrfOf((await page(token)).text);
    const fields = [["csrf", csrf], ["action", "approve"], ["clip", "11"], ["platform", "instagram"], ["confirm", "yes"]];
    for (let i = 0; i < 5; i++) await submit(token, [...fields, ["pin", "9999"]]);
    const r = await submit(token, [...fields, ["pin", "4826"]]);
    assert.equal(r.status, 429);
    assert.match(r.text, /Too many wrong PINs/);
  });

  it("refuses a decision without the page's CSRF token or from another site", async () => {
    await pc("POST", "/v1/notify", offer());
    const token = tokenFrom(fake.messages.at(-1));
    let r = await submit(token, [["csrf", "0".repeat(64)], ["action", "cancel"]]);
    assert.equal(r.status, 403);
    const csrf = csrfOf((await page(token)).text);
    r = await submit(token, [["csrf", csrf], ["action", "cancel"]], { Origin: "https://evil.example" });
    assert.equal(r.status, 403);
  });

  it("never approves from a bare yes, and handles the quick replies", async () => {
    const sent = await pc("POST", "/v1/notify", offer());
    const msg = fake.messages.at(-1);
    await wh(inboundText(PHONE, "yes"));
    assert.match(fake.messages.at(-1).text.body, /open the Review clips link/);
    assert.equal((await commands()).filter((c) => c.kind === "approve").length, 0);
    await wh(inboundButton(PHONE, `REMIND:${sent.data.approval_id}`, msg.wamid));
    assert.match(fake.messages.at(-1).text.body, /remind you in about 2 hours/);
    let cmds = await commands();
    assert.ok(cmds.find((c) => c.kind === "defer" && c.remind_at));
    await wh(inboundButton(PHONE, `CANCEL:${sent.data.approval_id}`, msg.wamid));
    cmds = await commands();
    assert.ok(cmds.find((c) => c.kind === "cancel"));
    assert.match((await page(tokenFrom(msg))).text, /Cancelled\. Nothing was posted/);
  });

  it("ignores a quick reply tapped on a different message", async () => {
    const first = await pc("POST", "/v1/notify", offer());
    await pc("POST", "/v1/notify", offer({ event_key: "ready:job:8:1", review_ref: 4 }));
    const second = fake.messages.at(-1);
    await wh(inboundButton(PHONE, `CANCEL:${first.data.approval_id}`, second.wamid));
    assert.equal((await commands()).filter((c) => c.kind === "cancel").length, 0);
  });

  it("tells the PC about delivery, once, and about failures", async () => {
    await pc("POST", "/v1/notify", offer());
    const msg = fake.messages.at(-1);
    await wh(statusUpdate(msg.wamid, "delivered"));
    await wh(statusUpdate(msg.wamid, "read"));
    let delivered = (await commands()).filter((c) => c.kind === "delivery");
    assert.equal(delivered.length, 1);
    assert.equal(delivered[0].status, "delivered");
    await pc("POST", "/v1/notify", offer({ event_key: "ready:job:9:1", review_ref: 5 }));
    await wh(statusUpdate(fake.messages.at(-1).wamid, "failed", 131026));
    delivered = (await commands()).filter((c) => c.kind === "delivery" && c.status === "failed");
    assert.match(delivered[0].error, /could not deliver/);
  });

  it("expires offers and sends reminders on the cron", async () => {
    const sent = await pc("POST", "/v1/notify", offer());
    const msg = fake.messages.at(-1);
    await wh(inboundButton(PHONE, `REMIND:${sent.data.approval_id}`, msg.wamid));
    env.DB.raw.prepare("UPDATE approvals SET remind_at = ?").run(Math.floor(Date.now() / 1000) - 5);
    const before = fake.messages.filter((m) => m.type === "template").length;
    await cron(env);
    assert.equal(fake.messages.filter((m) => m.type === "template").length, before + 1);
    assert.equal(tokenFrom(fake.messages.at(-1)), tokenFrom(msg)); // the same link
    assert.ok((await commands()).find((c) => c.kind === "reminded"));
    env.DB.raw.prepare("UPDATE approvals SET expires_at = ?").run(Math.floor(Date.now() / 1000) - 5);
    await cron(env);
    assert.ok((await commands()).find((c) => c.kind === "expired"));
    assert.match((await page(tokenFrom(msg))).text, /expired/);
  });

  it("allows publishing steps only inside an accepted approval", async () => {
    const sent = await pc("POST", "/v1/notify", offer());
    const token = tokenFrom(fake.messages.at(-1));
    const csrf = csrfOf((await page(token)).text);
    await submit(token, [["csrf", csrf], ["action", "approve"], ["clip", "11"], ["platform", "instagram"], ["platform", "facebook"], ["platform", "threads"], ["confirm", "yes"], ["pin", "4826"]]);
    const approve = (await commands()).find((c) => c.kind === "approve");
    const ctx = { command_id: approve.id, approval_id: sent.data.approval_id, clip_id: 11 };

    // Not yet accepted by the PC: refused.
    let r = await pc("POST", "/v1/meta/ig_create", { ...ctx, caption: "x", file_size: 2048 });
    assert.equal(r.status, 403);
    await pc("POST", `/v1/commands/${approve.id}/ack`, { outcome: "accepted", reason: "1 clip queued" });

    // A clip or platform that was not approved: refused.
    assert.equal((await pc("POST", "/v1/meta/ig_create", { ...ctx, clip_id: 12, caption: "x", file_size: 2048 })).status, 403);

    r = await pc("POST", "/v1/meta/ig_create", { ...ctx, caption: "Poster! #basketball", file_size: 2048 });
    assert.equal(r.status, 200);
    const container = r.data.container_id;
    const bytes = randomBytes(2048);
    const q = new URLSearchParams({ ...Object.fromEntries(Object.entries(ctx).map(([k, v]) => [k, String(v)])), platform: "instagram", ref: container, offset: "0", file_size: "2048" });
    const up = await call(uploadRequest(`/v1/meta/upload?${q}`, bytes));
    assert.equal(up.status, 200, await up.clone().text());
    assert.equal(fake.uploads[0].bytes, 2048);
    assert.equal((await pc("POST", "/v1/meta/ig_status", { ...ctx, container_id: container })).data.status_code, "FINISHED");
    const media = (await pc("POST", "/v1/meta/ig_publish", { ...ctx, container_id: container })).data.media_id;
    const link = (await pc("POST", "/v1/meta/ig_permalink", { ...ctx, media_id: media })).data.permalink;
    assert.match(link, /^https:\/\/www\.instagram\.com\/reel\//);

    // A container from this command can't be used for another clip.
    assert.equal((await pc("POST", "/v1/meta/ig_status", { ...ctx, clip_id: 12, container_id: container })).status, 403);

    // Threads links to the confirmed Reel; an outside link is refused.
    assert.equal((await pc("POST", "/v1/meta/th_create", { ...ctx, text: "Poster", link: "https://evil.example/x" })).status, 422);
    const th = await pc("POST", "/v1/meta/th_create", { ...ctx, text: "Poster", link });
    assert.equal(th.status, 200);
    const thMedia = (await pc("POST", "/v1/meta/th_publish", { ...ctx, container_id: th.data.container_id })).data.media_id;
    assert.match((await pc("POST", "/v1/meta/th_permalink", { ...ctx, media_id: thMedia })).data.permalink, /threads\.com/);
    assert.equal(fake.posts.find((p) => p.platform === "threads").link, link);
  });

  it("shows only confirmed links from Meta's own hosts", async () => {
    const sent = await pc("POST", "/v1/notify", offer());
    const token = tokenFrom(fake.messages.at(-1));
    const csrf = csrfOf((await page(token)).text);
    await submit(token, [["csrf", csrf], ["action", "approve"], ["clip", "11"], ["platform", "instagram"], ["confirm", "yes"], ["pin", "4826"]]);
    const approve = (await commands()).find((c) => c.kind === "approve");
    await pc("POST", `/v1/commands/${approve.id}/ack`, { outcome: "accepted" });
    const res = await pc("POST", "/v1/notify", {
      kind: "result", event_key: "result:review:3:0", approval_id: sent.data.approval_id, text: "1 of 2 posts published (1 failed)",
      results: { clips: [{ clip_id: 11, title: "Poster", posts: [
        { platform: "instagram", state: "published", url: "https://www.instagram.com/reel/ABC/" },
        { platform: "facebook", state: "published", url: "https://evil.example/phish" },
      ] }] },
    });
    assert.equal(res.data.channel, "template");
    assert.equal(fake.messages.at(-1).template.name, "ck_publish_update");
    const view = (await page(token)).text;
    assert.match(view, /instagram\.com\/reel\/ABC/);
    assert.doesNotMatch(view, /evil\.example/);
  });

  it("tells the page when the PC refused an approval", async () => {
    await pc("POST", "/v1/notify", offer());
    const token = tokenFrom(fake.messages.at(-1));
    const csrf = csrfOf((await page(token)).text);
    await submit(token, [["csrf", csrf], ["action", "approve"], ["clip", "11"], ["platform", "instagram"], ["confirm", "yes"], ["pin", "4826"]]);
    const approve = (await commands()).find((c) => c.kind === "approve");
    await pc("POST", `/v1/commands/${approve.id}/ack`, { outcome: "rejected", reason: "A clip changed after the message was sent" });
    assert.match((await page(token)).text, /did not publish this: A clip changed/);
    assert.equal((await commands()).filter((c) => c.kind === "approve").length, 0);
  });

  it("keeps the approval page read-only when remote approval is off", async () => {
    await pc("POST", "/v1/preferences", { remote_approvals: false });
    await pc("POST", "/v1/notify", offer());
    const view = (await page(tokenFrom(fake.messages.at(-1)))).text;
    assert.match(view, /switched off/);
    assert.doesNotMatch(view, /Approve and publish/);
  });
});

describe("accounts", () => {
  it("connects Facebook (with Instagram) and Threads, and never returns a token", async () => {
    const { data } = await pc("POST", "/v1/accounts/connect", { platform: "facebook" });
    const url = new URL(data.url);
    assert.equal(url.hostname, "www.facebook.com");
    assert.match(url.searchParams.get("scope"), /instagram_content_publish/);
    const state = url.searchParams.get("state");
    let res = await call(new Request(`${BASE}/oauth/facebook/callback?state=${state}&code=abc`));
    assert.match(await res.text(), /Hoops Daily/);
    res = await call(new Request(`${BASE}/oauth/facebook/callback?state=${state}&code=abc`));
    assert.equal(res.status, 400); // a state is single use
    const status = (await pc("GET", "/v1/status")).data;
    assert.deepEqual(status.accounts.instagram, { connected: true, name: "@hoopsdaily" });
    assert.equal(status.accounts.facebook.name, "Hoops Daily");
    assert.doesNotMatch(JSON.stringify(status), /page-token|fb-long/);
    const row = env.DB.raw.prepare("SELECT token_enc FROM accounts WHERE platform = 'facebook'").get();
    assert.doesNotMatch(row.token_enc, /page-token/);
  });

  it("checks templates and creates the missing ones", async () => {
    const res = await pc("POST", "/v1/templates", {});
    assert.equal(Object.keys(res.data.templates).length, 4);
    assert.equal(fake.templates.length, 4);
    const ready = fake.templates.find((t) => t.name === "ck_highlights_ready");
    const body = ready.components.find((c) => c.type === "BODY").text;
    assert.doesNotMatch(body, /^\{\{|\}\}$/);
    assert.equal(ready.category, "UTILITY");
    const failed = fake.templates.find((t) => t.name === "ck_processing_failed");
    assert.equal(failed.components[0].text, "Clips Kitty could not finish processing your basketball video. Open the app for details.");
  });
});

describe("webhook helpers", () => {
  it("records template status updates", async () => {
    await handleWebhook(env, { entry: [{ changes: [{ field: "message_template_status_update", value: { message_template_name: "ck_highlights_ready", event: "APPROVED" } }] }] });
    assert.equal((await pc("GET", "/v1/status")).data.templates.ck_highlights_ready, "APPROVED");
  });
});
